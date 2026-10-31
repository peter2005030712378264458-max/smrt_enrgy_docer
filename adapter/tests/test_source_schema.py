"""Regression tests against an isolated PostgreSQL, not SQL-string assertions."""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from dataclasses import replace
import os
import unittest
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from adapter.mapping import FIELDS, column_types, inspect_source, source_capabilities, validate_profile
from adapter.model import Settings, table_parts
from adapter.runner import RefreshError, refresh, target_lock
from adapter.service import Controller, process_job
from adapter.tests import test_adapter as fixtures

PROFILE = fixtures.PROFILE


class IdentifierTests(unittest.TestCase):
    def test_qualified_names_are_literal_and_accept_catalog_quoting(self):
        self.assertEqual(table_parts(' public.customer '), ('public', 'customer'))
        self.assertEqual(table_parts('"Review source"."raw.readings"'), ('Review source', 'raw.readings'))
        self.assertEqual(table_parts('public."meter""name"'), ('public', 'meter"name'))
        for name in ('readings', 'db.public.readings', 'public.a;DROP TABLE x', 'public.""', 'public."bad\0name"'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                table_parts(name)

    def test_only_time_and_meter_are_required(self):
        profile = {**PROFILE, 'columns': {'ts': 'ts', 'sensor_name': 'meter'}}
        self.assertEqual(validate_profile(profile)['columns'], profile['columns'])
        for field in ('ts', 'sensor_name'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_profile({**profile, 'columns': {k: v for k, v in profile['columns'].items() if k != field}})


@unittest.skipUnless(os.environ.get('ADAPTER_TEST_ADMIN_DSN'), 'Isolated test PostgreSQL not configured')
class SourceSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.IntegrationTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.IntegrationTests()
        self.fixture.setUp()
        self.source_dsn, self.target_dsn = self.fixture.source_dsn, self.fixture.target_dsn
        self.store = self.fixture.store
        self.reset_source()

    def reset_source(self, fields=FIELDS):
        values = {'pt': 1000, 'p1': 100, 'p2': 200, 'p3': 700, 'qt': 600,
                  'q1': 100, 'q2': 200, 'q3': 300, 'st': 1200,
                  's1': 400, 's2': 400, 's3': 400, 'i1': 1, 'i2': 2, 'i3': 3,
                  'u1': 230, 'u2': 231, 'u3': 232, 'frequency': 50, 't': 20}
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('DROP TABLE IF EXISTS public.review_readings CASCADE')
            definitions = [sql.SQL('{} {}').format(sql.Identifier(field), sql.SQL(
                'timestamptz' if field == 'ts' else 'text' if field == 'sensor_name' else 'numeric'))
                for field in fields]
            conn.execute(sql.SQL('CREATE TABLE public.review_readings ({})').format(sql.SQL(', ').join(definitions)))
            expressions = [sql.SQL('ts') if f == 'ts' else sql.Literal('Meter A' if f == 'sensor_name' else values[f]) for f in fields]
            conn.execute(sql.SQL("""INSERT INTO public.review_readings SELECT {} FROM generate_series(
                timestamptz '2026-10-15 00:00+03',timestamptz '2026-10-15 23:00+03',interval '1 hour') ts""").format(sql.SQL(', ').join(expressions)))
        self.profile = validate_profile({**deepcopy(PROFILE), 'source_table': 'public.review_readings',
            'columns': {field: field for field in fields}, 'scales': {}, 'sensor_map': {}, 'sample_seconds': 3600})

    def run_refresh(self, apply=False, profile=True, **kwargs):
        settings = Settings(self.source_dsn, self.target_dsn, source_table='public.review_readings',
                            sample_seconds=Decimal(3600), profile=self.profile if profile else None)
        return refresh(settings, start=date(2026, 10, 15), end=date(2026, 10, 16), apply=apply,
                       now=datetime(2026, 10, 18, tzinfo=timezone.utc), allowed_sensors={'Meter A'}, **kwargs)

    def daily(self):
        with psycopg.connect(self.target_dsn, row_factory=dict_row) as conn:
            return conn.execute('SELECT * FROM student_schema.electricity_sensor_readings_daily').fetchone()

    def test_every_optional_column_can_disappear_without_sql_failure(self):
        for field in FIELDS[2:]:
            with self.subTest(field=field):
                self.reset_source()
                with psycopg.connect(self.source_dsn) as conn:
                    conn.execute(sql.SQL('ALTER TABLE public.review_readings DROP COLUMN {}').format(sql.Identifier(field)))
                events = self.run_refresh(True)
                schema = next(e for e in events if e['event'] == 'schema')
                self.assertIn({'field': field, 'column': field}, schema['missing_columns'])
                self.assertTrue(schema['warnings'])
                self.assertEqual(self.daily()['points_count'], 24)

    def test_minimal_metric_sets_and_points_only_are_aggregated(self):
        cases = [(('pt',), 'active_power_w_avg', Decimal(1000)),
                 (('p1',), 'phase1_power_w_avg', Decimal(100)),
                 (('p1', 'p2'), 'phase2_power_w_avg', Decimal(200)),
                 (('p1', 'p2', 'p3'), 'active_power_w_avg', Decimal(1000)),
                 (('qt',), 'reactive_power_var_avg', Decimal(600)),
                 (('st',), 'apparent_power_va_avg', Decimal(1200)),
                 (('u1',), 'voltage_avg_v', Decimal(230)),
                 (('i2',), None, None),
                 (('frequency',), 'frequency_hz_avg', Decimal(50)),
                 (('t',), 'meter_temperature_avg', Decimal(20)), ((), None, None)]
        for fields, metric, value in cases:
            with self.subTest(fields=fields):
                self.reset_source(('ts', 'sensor_name', *fields))
                events = self.run_refresh(True)
                row = self.daily()
                self.assertEqual(row['points_count'], 24)
                if metric:
                    with psycopg.connect(self.target_dsn) as conn:
                        actual = conn.execute(sql.SQL('SELECT {} FROM student_schema.electricity_sensor_readings_hourly LIMIT 1').format(sql.Identifier(metric))).fetchone()[0]
                    self.assertEqual(actual, value)
                has_power = fields in (('pt',), ('p1', 'p2', 'p3'))
                self.assertEqual(row['energy_kwh'], Decimal(24) if has_power else None)
                quality = next(e for e in events if e['event'] == 'quality')
                self.assertEqual(quality['errors'], [])
                self.assertEqual(quality['coverage'], {'Meter A': 1})
                if not has_power:
                    self.assertIsNone(row['active_power_w_avg'])
                    self.assertEqual(quality['coverage_basis'], 'readings')

    def test_partial_phases_never_become_total_and_nan_does_not_poison_other_phases(self):
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute("UPDATE public.review_readings SET pt=NULL,p3=NULL,qt=NULL,q3=NULL,st=NULL,s3=NULL,u1='NaN'")
        # Confirm a source without complete total-power mappings. Individual
        # phase power and available voltage are still useful measurements.
        self.profile['columns'] = {key: value for key, value in self.profile['columns'].items() if key not in ('pt','p3')}
        self.run_refresh(True)
        row = self.daily()
        self.assertIsNone(row['active_power_w_avg'])
        self.assertIsNone(row['reactive_power_var_avg'])
        self.assertIsNone(row['apparent_power_va_avg'])
        self.assertEqual(row['voltage_avg_v'], Decimal('231.5'))
        self.assertIsNone(row['energy_kwh'])
        with psycopg.connect(self.target_dsn) as conn:
            self.assertEqual(conn.execute('SELECT phase1_power_w_avg FROM student_schema.electricity_sensor_readings_hourly LIMIT 1').fetchone()[0], 100)

    def test_missing_required_columns_and_null_keys_preserve_previous_aggregates(self):
        self.run_refresh(True)
        for field in ('ts', 'sensor_name'):
            with self.subTest(field=field, failure='missing'):
                self.reset_source()
                with psycopg.connect(self.source_dsn) as conn:
                    conn.execute(sql.SQL('ALTER TABLE public.review_readings DROP COLUMN {}').format(sql.Identifier(field)))
                with self.assertRaisesRegex(ValueError, 'Обязательное поле'):
                    self.run_refresh(True)
                self.assertEqual(self.daily()['energy_kwh'], 24)
            with self.subTest(field=field, failure='null'):
                self.reset_source()
                with psycopg.connect(self.source_dsn) as conn:
                    conn.execute(sql.SQL('UPDATE public.review_readings SET {}=NULL WHERE ctid=(SELECT ctid FROM public.review_readings LIMIT 1)').format(sql.Identifier(field)))
                with self.assertRaisesRegex(RefreshError, 'без времени|без идентификатора'):
                    self.run_refresh(True)
                self.assertEqual(self.daily()['energy_kwh'], 24)
        self.reset_source()
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute("UPDATE public.review_readings SET sensor_name=' ' WHERE ctid=(SELECT ctid FROM public.review_readings LIMIT 1)")
        with self.assertRaisesRegex(RefreshError, 'без идентификатора'):
            self.run_refresh(True)
        self.assertEqual(self.daily()['energy_kwh'], 24)

    def test_float_infinity_is_missing_before_numeric_conversion(self):
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('ALTER TABLE public.review_readings ALTER COLUMN u1 TYPE double precision')
            conn.execute("UPDATE public.review_readings SET u1='Infinity'")
        self.run_refresh(True)
        self.assertEqual(self.daily()['voltage_avg_v'], Decimal('231.5'))
        self.assertEqual(self.daily()['energy_kwh'], 24)

    def test_text_timestamp_is_not_guessed_and_numeric_alias_with_text_type_is_not_suggested(self):
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('ALTER TABLE public.review_readings ALTER COLUMN ts TYPE text USING ts::text')
            conn.execute('ALTER TABLE public.review_readings ALTER COLUMN pt TYPE text USING pt::text')
        with psycopg.connect(self.source_dsn, row_factory=dict_row) as conn:
            inspection = inspect_source(conn, 'public.review_readings')
        self.assertIsNone(inspection['suggestions']['ts']['column'])
        self.assertIsNone(inspection['suggestions']['pt']['column'])
        with self.assertRaisesRegex(ValueError, 'тип timestamp'):
            self.run_refresh(True)

    def test_cli_without_profile_handles_missing_measurements_and_rejects_null_time(self):
        self.reset_source(('ts','sensor_name','u1'))
        self.run_refresh(True, profile=False)
        self.assertEqual(self.daily()['voltage_avg_v'], 230)
        self.assertIsNone(self.daily()['energy_kwh'])
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('UPDATE public.review_readings SET ts=NULL')
        with self.assertRaisesRegex(RefreshError, 'без времени'):
            self.run_refresh(True, profile=False)

    def test_incomplete_hour_has_unknown_energy_even_when_coverage_is_allowed(self):
        self.profile['quality']['min_coverage'] = 0
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('INSERT INTO public.review_readings SELECT * FROM public.review_readings')
            conn.execute('UPDATE public.review_readings SET pt=NULL,p3=NULL WHERE ctid IN (SELECT ctid FROM public.review_readings LIMIT 1)')
        self.profile['quality']['max_duplicate_fraction'] = 1
        self.profile['quality']['min_interval_match'] = 0
        self.run_refresh(True)
        self.assertIsNone(self.daily()['energy_kwh'])

    def test_inspection_retains_catalog_columns_when_sample_times_out(self):
        with psycopg.connect(self.source_dsn) as blocker:
            blocker.execute('LOCK TABLE public.review_readings IN ACCESS EXCLUSIVE MODE')
            with psycopg.connect(self.source_dsn, row_factory=dict_row) as conn:
                conn.execute("SET statement_timeout='200ms'")
                inspection = inspect_source(conn, 'public.review_readings')
                self.assertEqual([c['name'] for c in inspection['columns']], list(FIELDS))
                self.assertEqual(inspection['sample'], [])
                self.assertTrue(inspection['warnings'])
                self.assertEqual(conn.execute('SELECT 1 AS ok').fetchone()['ok'], 1)

    def test_catalog_quoted_names_comments_domains_and_readable_views(self):
        with psycopg.connect(self.source_dsn, row_factory=dict_row) as conn:
            conn.execute('CREATE SCHEMA IF NOT EXISTS "Review source"')
            conn.execute('DROP VIEW IF EXISTS "Review source"."raw.readings"')
            conn.execute('CREATE VIEW "Review source"."raw.readings" AS SELECT ts AS "Read at", sensor_name,pt FROM public.review_readings')
            conn.execute('COMMENT ON COLUMN "Review source"."raw.readings"."Read at" IS \'Source timestamp comment\'')
            inspection = inspect_source(conn, '"Review source"."raw.readings"')
            self.assertEqual(inspection['columns'][0]['name'], 'Read at')
            self.assertEqual(inspection['columns'][0]['comment'], 'Source timestamp comment')
            self.assertEqual(inspection['columns'][0]['type'], 'timestamptz')
            self.assertEqual(len(inspection['sample']), 5)
            conn.commit()
            controller = Controller(self.store, {'paradigm': self.source_dsn})
            listed = controller.dispatch('GET', '/tables?connection=paradigm', {})
            self.assertIn('"Review source"."raw.readings"', [item['name'] for item in listed])
            conn.execute('DROP VIEW "Review source"."raw.readings"')
            conn.execute('DROP DOMAIN IF EXISTS public.review_power')
            conn.execute('CREATE DOMAIN public.review_power AS numeric')
            conn.execute('ALTER TABLE public.review_readings ALTER COLUMN pt TYPE public.review_power')
            inspection = inspect_source(conn, 'public.review_readings')
            self.assertEqual(inspection['suggestions']['pt']['column'], 'pt')
            self.assertEqual(column_types(conn, self.profile)['pt'], 'numeric')
        self.run_refresh(True)
        self.assertEqual(self.daily()['energy_kwh'], 24)

    def test_naive_source_time_is_interpreted_in_its_declared_zone(self):
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute("ALTER TABLE public.review_readings ALTER COLUMN ts TYPE timestamp USING ts AT TIME ZONE 'America/New_York'")
        self.profile['source_timezone'] = 'America/New_York'
        self.run_refresh(True)
        self.assertEqual(self.daily()['points_count'], 24)
        self.assertEqual(self.daily()['bucket_start'], date(2026, 10, 15))
        self.assertEqual(self.daily()['energy_kwh'], 24)

    def test_quoted_aliases_do_not_bypass_target_locks(self):
        settings = Settings(self.source_dsn, self.target_dsn, source_table='public.review_readings',
                            sample_seconds=Decimal(3600), profile=self.profile)
        alternate = replace(settings, hourly_table='"student_schema"."electricity_sensor_readings_hourly"')
        with psycopg.connect(self.target_dsn, autocommit=True, row_factory=dict_row) as connection:
            with target_lock(connection, settings), self.assertRaisesRegex(RefreshError, 'already running'):
                refresh(alternate, start=date(2026, 10, 15), end=date(2026, 10, 16))

    def test_column_privileges_allow_only_readable_measurements(self):
        role = 'adapter_review_columns'
        with psycopg.connect(self.source_dsn, autocommit=True) as conn:
            if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'test-columns-only'").format(sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT SELECT(ts,sensor_name,pt) ON public.review_readings TO {}').format(sql.Identifier(role)))
            conn.execute('CREATE SCHEMA IF NOT EXISTS review_private')
            conn.execute('DROP VIEW IF EXISTS review_private.readings')
            conn.execute('CREATE VIEW review_private.readings AS SELECT ts,sensor_name,pt FROM public.review_readings')
            conn.execute(sql.SQL('GRANT SELECT ON review_private.readings TO {}').format(sql.Identifier(role)))
        restricted = make_conninfo(self.source_dsn,user=role,password='test-columns-only')
        with psycopg.connect(restricted,row_factory=dict_row) as conn:
            inspection = inspect_source(conn,'public.review_readings')
            self.assertTrue(next(c for c in inspection['columns'] if c['name']=='pt')['readable'])
            self.assertFalse(next(c for c in inspection['columns'] if c['name']=='u1')['readable'])
            self.assertEqual(set(inspection['sample'][0]),{'ts','sensor_name','pt'})
            inaccessible = inspect_source(conn, 'review_private.readings')
            self.assertFalse(any(column['readable'] for column in inaccessible['columns']))
            self.assertEqual(inaccessible['sample'], [])
            self.assertTrue(inaccessible['warnings'])
        self.source_dsn=restricted
        self.run_refresh(True)
        self.assertIsNone(self.daily()['voltage_avg_v'])
        self.assertEqual(self.daily()['energy_kwh'],24)

    def test_schema_change_after_preview_blocks_apply_until_new_preview(self):
        self.run_refresh(True)
        controller=Controller(self.store,{'paradigm':self.source_dsn})
        with patch.dict(os.environ,{'ENERGY_AGG_TARGET_DSN':self.target_dsn}):
            profile=controller.dispatch('POST','/profiles',self.profile)
            payload={'profile_id':profile['id'],'from':'2026-10-15','to':'2026-10-16'}
            preview=controller.dispatch('POST','/jobs',payload)
            process_job(controller,preview)
            self.store.query("UPDATE adapter_control.jobs SET status='succeeded' WHERE id=%s",(preview['id'],))
            with psycopg.connect(self.source_dsn) as conn:
                conn.execute('ALTER TABLE public.review_readings DROP COLUMN pt')
            apply=controller.dispatch('POST','/jobs',{**payload,'kind':'apply','preview_id':preview['id']})
            with self.assertRaisesRegex(RefreshError,'после предварительного'):
                process_job(controller,apply)
            self.assertEqual(self.daily()['energy_kwh'],24)
            preview=controller.dispatch('POST','/jobs',payload)
            self.assertFalse(self.store.profile(profile['id'])['validated'])
            process_job(controller,preview)
            self.store.query("UPDATE adapter_control.jobs SET status='succeeded' WHERE id=%s",(preview['id'],))
            apply=controller.dispatch('POST','/jobs',{**payload,'kind':'apply','preview_id':preview['id']})
            process_job(controller,apply)
            self.assertEqual(self.daily()['energy_kwh'],24)

    def test_queued_apply_rechecks_completed_day_boundary(self):
        self.run_refresh(True)
        controller = Controller(self.store, {'paradigm': self.source_dsn})
        with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN': self.target_dsn}):
            profile = controller.dispatch('POST', '/profiles', self.profile)
            preview = controller.dispatch('POST', '/jobs', {
                'profile_id': profile['id'], 'from': '2026-10-15', 'to': '2026-10-16'})
            process_job(controller, preview)
            # A preview made on the 16th for [15,17) would be clipped to the
            # 16th. Reproduce that persisted state after the queued job has
            # crossed midnight: the worker must not add an unpreviewed day.
            self.store.query("UPDATE adapter_control.jobs SET status='succeeded', date_to='2026-10-17' WHERE id=%s", (preview['id'],))
            apply = self.store.enqueue(profile['id'], 'apply', date(2026, 10, 15), date(2026, 10, 17), preview['id'])
            with self.assertRaisesRegex(ValueError, 'граница завершённых суток'):
                process_job(controller, apply)
            self.assertEqual(self.daily()['energy_kwh'], 24)
