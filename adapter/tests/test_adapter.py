from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from adapter.mapping import validate_profile, inspect_source, source_cte
from adapter.model import Settings, plan_window, METRICS
from adapter.runner import refresh, RefreshError, RefreshInterrupted, target_lock, fingerprint
from adapter.service import Controller, process_job, schedule_ranges, connections, directory_alias, build_settings
from adapter.store import Store

PROFILE = json.loads((Path(__file__).parents[1] / 'profiles/paradigm.json').read_text(encoding='utf-8'))


class ModelTests(unittest.TestCase):
    def test_directory_default_and_explicit_choice_are_independent_of_source(self):
        registry = {'paradigm': 'dbname=source', 'directory': 'dbname=metadata'}
        self.assertEqual(directory_alias(PROFILE, registry), 'directory')
        self.assertEqual(directory_alias({**PROFILE, 'directory_connection': 'paradigm'}, registry), 'paradigm')
        self.assertEqual(directory_alias(PROFILE, {'paradigm': 'dbname=source'}), 'paradigm')
        with self.assertRaises(ValueError):
            directory_alias({**PROFILE, 'directory_connection': 'missing'}, registry)
        controller = Controller(None, registry)
        self.assertEqual(controller.dispatch('GET', '/connections', {}), ['paradigm'])
        with self.assertRaises(ValueError):
            controller.dispatch('GET', '/tables?connection=directory', {})
        with self.assertRaises(ValueError):
            controller.dispatch('POST', '/inspect', {'connection': 'directory', 'source_table': 'public.sensor_directory'})

    def test_directory_dsn_is_registered_without_replacing_source(self):
        with patch.dict(os.environ, {'ENERGY_ADAPTER_CONNECTIONS': '{}',
                                    'ENERGY_AGG_SOURCE_DSN': 'dbname=raw user=raw_reader',
                                    'ENERGY_AGG_DIRECTORY_DSN': 'dbname=raw user=directory_reader'}):
            registry = connections()
            self.assertEqual(registry['paradigm'], 'dbname=raw user=raw_reader')
            self.assertEqual(registry['directory'], 'dbname=raw user=directory_reader')
            with patch.dict(os.environ, {'ENERGY_ADAPTER_CONNECTIONS': '{"directory":"dbname=other"}'}):
                with self.assertRaises(ValueError):
                    connections()

    def test_profile_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):
            validate_profile({**PROFILE, 'confirmed': False})

    def test_power_required_and_optional_fields_can_be_missing(self):
        profile = {**PROFILE, 'columns': {'ts': 'time', 'sensor_name': 'id'}}
        with self.assertRaises(ValueError):
            validate_profile(profile)
        profile['columns']['pt'] = 'kw'
        self.assertEqual(validate_profile(profile)['columns']['pt'], 'kw')

    def test_units_timezone_and_duplicate_sensor_mapping(self):
        for changes in ({'scales': {'pt': 0}}, {'source_timezone': 'Bad/Zone'},
                        {'sensor_map': {'1': 'a', '2': 'a'}}, {'quality': {'min_coverage': 2}}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, KeyError)):
                validate_profile({**PROFILE, **changes})

    def test_manual_old_and_gap_ranges_are_independent(self):
        settings = Settings('dbname=a', 'dbname=b')
        begin, end = plan_window(settings, datetime(2026, 10, 18, 10, tzinfo=timezone.utc),
                                 date(2026, 10, 15), date(2026, 10, 16), datetime(2026, 9, 1, tzinfo=timezone.utc), True)
        self.assertEqual(begin.date(), date(2026, 10, 15))
        self.assertEqual(end.date(), date(2026, 10, 16))

    def test_unfinished_day_is_excluded(self):
        settings = Settings('dbname=a', 'dbname=b')
        begin, end = plan_window(settings, datetime(2026, 10, 18, 10, tzinfo=timezone.utc),
                                 date(2026, 10, 17), date(2026, 10, 20), None, True)
        self.assertEqual(end.date(), date(2026, 10, 18))
        self.assertEqual(begin.date(), date(2026, 10, 17))

    def test_schedule_yesterday_and_catchup_and_repeat(self):
        profile = {'id': 'p', 'document': PROFILE, 'processed_until': None}
        ranges = list(schedule_ranges(profile, datetime(2026, 10, 17, 21, tzinfo=timezone.utc), 15))
        self.assertEqual(ranges[0][:2], (date(2026, 10, 17), date(2026, 10, 18)))
        self.assertEqual(len(ranges), 1)
        profile['processed_until'] = date(2026, 10, 15)
        ranges = list(schedule_ranges(profile, datetime(2026, 10, 18, tzinfo=timezone.utc), 15))
        self.assertEqual(ranges[0][0], date(2026, 10, 15))
        self.assertEqual(len(ranges), 2)


@unittest.skipUnless(os.environ.get('ADAPTER_TEST_ADMIN_DSN'), 'Isolated test PostgreSQL not configured')
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        admin = os.environ['ADAPTER_TEST_ADMIN_DSN']
        with psycopg.connect(admin, autocommit=True) as conn:
            for name in ('test_adapter_source', 'test_adapter_target'):
                if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone():
                    conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        cls.source_dsn = make_conninfo(admin, dbname='test_adapter_source')
        cls.target_dsn = make_conninfo(admin, dbname='test_adapter_target')
        with psycopg.connect(cls.source_dsn, autocommit=True) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS public.customer (
                "время" timestamp, meter_id text, power_kw numeric, voltage numeric);
                CREATE TABLE IF NOT EXISTS public.sensor_directory(sensor_name text);
                TRUNCATE sensor_directory;
                INSERT INTO sensor_directory VALUES('Meter A');''')
        with psycopg.connect(cls.target_dsn, autocommit=True) as conn:
            conn.execute('CREATE SCHEMA IF NOT EXISTS student_schema')
            for table, columns, bucket_type in (
                ('electricity_sensor_readings_hourly', __import__('adapter.model', fromlist=['HOURLY_COLUMNS']).HOURLY_COLUMNS, 'timestamptz'),
                ('electricity_sensor_readings_daily', __import__('adapter.model', fromlist=['DAILY_COLUMNS']).DAILY_COLUMNS, 'date')):
                definitions = [sql.SQL('{} {}').format(sql.Identifier(c), sql.SQL(
                    bucket_type if c == 'bucket_start' else 'text' if c == 'sensor_name' else 'bigint' if c == 'points_count' else 'numeric')) for c in columns]
                conn.execute(sql.SQL('CREATE TABLE IF NOT EXISTS student_schema.{} ({}, PRIMARY KEY(bucket_start,sensor_name))').format(sql.Identifier(table), sql.SQL(',').join(definitions)))
        cls.store = Store(cls.target_dsn)
        cls.store.install()

    def setUp(self):
        self.profile = validate_profile({**PROFILE, 'source_table': 'public.customer',
            'columns': {'ts': 'время', 'sensor_name': 'meter_id', 'pt': 'power_kw', 'u1': 'voltage'},
            'scales': {'pt': 1000}, 'sensor_map': {'42': 'Meter A'}, 'sample_seconds': 3600})
        self.settings = Settings(self.source_dsn, self.target_dsn, source_table='public.customer',
                                 sample_seconds=Decimal(3600), profile=self.profile)
        with psycopg.connect(self.source_dsn, autocommit=True) as conn:
            conn.execute('TRUNCATE customer')
            conn.execute("TRUNCATE sensor_directory; INSERT INTO sensor_directory VALUES('Meter A')")
            conn.execute("INSERT INTO customer SELECT ts,'42', CASE WHEN EXTRACT(hour FROM ts)<12 THEN 1 ELSE 3 END,230 FROM generate_series(timestamp '2026-10-15 00:00', timestamp '2026-10-15 23:00', interval '1 hour') ts")
        with psycopg.connect(self.target_dsn, autocommit=True) as conn:
            conn.execute('TRUNCATE student_schema.electricity_sensor_readings_hourly,student_schema.electricity_sensor_readings_daily,adapter_control.jobs,adapter_control.profiles CASCADE')
            conn.execute('UPDATE adapter_control.settings SET enabled=false')

    def run_refresh(self, apply=False, **kwargs):
        return refresh(self.settings, start=date(2026, 10, 15), end=date(2026, 10, 16), apply=apply,
                       now=datetime(2026, 10, 18, tzinfo=timezone.utc),
                       allowed_sensors=kwargs.pop('allowed_sensors', {'Meter A'}), **kwargs)

    def totals(self):
        with psycopg.connect(self.target_dsn, row_factory=dict_row) as conn:
            return conn.execute('SELECT COUNT(*) AS rows,SUM(energy_kwh) AS energy FROM student_schema.electricity_sensor_readings_hourly').fetchone()

    def test_dry_run_and_normalization_and_daily_energy(self):
        self.run_refresh()
        self.assertEqual(self.totals()['rows'], 0)
        self.run_refresh(True)
        self.assertEqual(self.totals(), {'rows': 24, 'energy': Decimal(48)})
        with psycopg.connect(self.target_dsn) as conn:
            avg, energy, voltage = conn.execute('SELECT active_power_w_avg,energy_kwh,voltage_avg_v FROM student_schema.electricity_sensor_readings_daily').fetchone()
            self.assertEqual((avg, energy, voltage), (Decimal(2000), Decimal(48), Decimal(230)))

    def test_idempotent_backfill_and_preserves_other_days_checkpoint(self):
        self.run_refresh(True)
        with psycopg.connect(self.target_dsn) as conn:
            conn.execute("INSERT INTO student_schema.electricity_sensor_readings_hourly(bucket_start,sensor_name,points_count,energy_kwh) VALUES('2026-10-20','Later',1,9)")
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory)/'checkpoint.json'
            checkpoint.write_text('old checkpoint unchanged')
            self.run_refresh(True, checkpoint_path=checkpoint)
            self.assertEqual(checkpoint.read_text(), 'old checkpoint unchanged')
        self.assertEqual(self.totals(), {'rows': 25, 'energy': Decimal(57)})

    def test_empty_and_partial_source_preserve_target(self):
        self.run_refresh(True)
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute("DELETE FROM customer WHERE время >= '2026-10-15 10:00'")
        with self.assertRaises(RefreshError):
            self.run_refresh(True)
        self.assertEqual(self.totals()['energy'], 48)
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('TRUNCATE customer')
        with self.assertRaises(RefreshError):
            self.run_refresh(True)
        self.assertEqual(self.totals()['rows'], 24)

    def test_duplicate_and_unknown_sensors_fail(self):
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('INSERT INTO customer SELECT * FROM customer LIMIT 1')
        with self.assertRaises(RefreshError):
            self.run_refresh(True)
        self.assertEqual(self.totals()['rows'], 0)

    def test_atomic_pair_rollback(self):
        self.run_refresh(True)
        with psycopg.connect(self.target_dsn) as conn:
            conn.execute('ALTER TABLE student_schema.electricity_sensor_readings_daily ADD CONSTRAINT force_failure CHECK(energy_kwh < 0) NOT VALID')
        try:
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.run_refresh(True)
            self.assertEqual(self.totals()['energy'], 48)
        finally:
            with psycopg.connect(self.target_dsn) as conn:
                conn.execute('ALTER TABLE student_schema.electricity_sensor_readings_daily DROP CONSTRAINT force_failure')

    def test_parallel_target_lock(self):
        with psycopg.connect(self.target_dsn, autocommit=True, row_factory=dict_row) as conn:
            with target_lock(conn, self.settings):
                with self.assertRaises(RefreshError):
                    self.run_refresh(True)

    def test_source_is_read_only_and_shutdown_does_not_write(self):
        with patch('adapter.runner.hourly_query', return_value=sql.SQL('DELETE FROM public.customer RETURNING *')):
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                self.run_refresh(True)
        with psycopg.connect(self.source_dsn) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM customer').fetchone()[0], 24)
        with self.assertRaises(RefreshInterrupted):
            self.run_refresh(True, stop_requested=lambda: True)
        self.assertEqual(self.totals()['rows'], 0)

    def test_null_energy_remains_null_in_daily_aggregate(self):
        self.profile['quality']['min_coverage'] = 0
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute("UPDATE customer SET power_kw=NULL WHERE время='2026-10-15 00:00'")
        self.run_refresh(True)
        with psycopg.connect(self.target_dsn) as conn:
            self.assertIsNone(conn.execute('SELECT energy_kwh FROM student_schema.electricity_sensor_readings_daily').fetchone()[0])

    def add_meter_without_power(self, value=None):
        self.profile['sensor_map']['43'] = 'Meter B'
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute("INSERT INTO customer SELECT время,'43',%s,voltage FROM customer WHERE meter_id='42'", (value,))
            conn.execute("INSERT INTO sensor_directory VALUES('Meter B')")

    def test_missing_power_warns_and_preserves_other_metrics_and_valid_energy(self):
        self.add_meter_without_power()
        preview = self.run_refresh(allowed_sensors={'Meter A', 'Meter B'})
        quality = next(event for event in preview if event['event'] == 'quality')
        self.assertEqual(quality['errors'], [])
        self.assertEqual(quality['missing_power_sensors'], ['Meter B'])
        self.assertEqual(quality['coverage'], {'Meter A': 1, 'Meter B': 0})
        self.assertIn('NULL', quality['warnings'][0])
        self.assertEqual(self.totals()['rows'], 0)
        for _ in range(2):
            self.run_refresh(True, allowed_sensors={'Meter A', 'Meter B'})
        self.assertEqual(self.totals(), {'rows': 48, 'energy': Decimal(48)})
        with psycopg.connect(self.target_dsn) as conn:
            missing = conn.execute("""SELECT points_count,active_power_w_avg,active_power_w_max,
                energy_kwh,voltage_avg_v FROM student_schema.electricity_sensor_readings_daily
                WHERE sensor_name='Meter B'""").fetchone()
            self.assertEqual(missing, (24, None, None, None, Decimal(230)))
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM student_schema.electricity_sensor_readings_hourly
                WHERE sensor_name='Meter B' AND energy_kwh IS NULL AND active_power_w_avg IS NULL
                AND voltage_avg_v=230""").fetchone()[0], 24)
            # A complete total must remain unknown, rather than silently omitting Meter B.
            self.assertIsNone(conn.execute('''SELECT CASE WHEN COUNT(*)=COUNT(energy_kwh)
                THEN SUM(energy_kwh) ELSE NULL END FROM student_schema.electricity_sensor_readings_daily''').fetchone()[0])

    def test_real_zero_power_is_valid_and_has_zero_energy(self):
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('UPDATE customer SET power_kw=0')
        events = self.run_refresh(True)
        quality = next(event for event in events if event['event'] == 'quality')
        self.assertEqual(quality['warnings'], [])
        self.assertEqual(quality['missing_power_sensors'], [])
        self.assertEqual(quality['coverage'], {'Meter A': 1})
        self.assertEqual(self.totals(), {'rows': 24, 'energy': Decimal(0)})

    def test_no_valid_power_anywhere_still_blocks_and_preserves_target(self):
        self.run_refresh(True)
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute('UPDATE customer SET power_kw=NULL')
        with self.assertRaisesRegex(RefreshError, 'ни у одного счётчика'):
            self.run_refresh(True)
        self.assertEqual(self.totals(), {'rows': 24, 'energy': Decimal(48)})

    def test_partial_power_loss_still_blocks_despite_other_missing_sensor(self):
        self.run_refresh(True)
        self.add_meter_without_power()
        with psycopg.connect(self.source_dsn) as conn:
            conn.execute("UPDATE customer SET power_kw=NULL WHERE meter_id='42' AND время>='2026-10-15 02:00'")
        with self.assertRaisesRegex(RefreshError, 'недостаточно корректных показаний мощности: Meter A'):
            self.run_refresh(True, allowed_sensors={'Meter A', 'Meter B'})
        self.assertEqual(self.totals(), {'rows': 24, 'energy': Decimal(48)})

    def test_nonfinite_power_warns_without_writing_nonfinite_aggregates(self):
        self.add_meter_without_power(Decimal('NaN'))
        events = self.run_refresh(True, allowed_sensors={'Meter A', 'Meter B'})
        self.assertEqual(next(e for e in events if e['event'] == 'quality')['missing_power_sensors'], ['Meter B'])
        with psycopg.connect(self.target_dsn) as conn:
            self.assertIsNone(conn.execute("SELECT energy_kwh FROM student_schema.electricity_sensor_readings_daily WHERE sensor_name='Meter B'").fetchone()[0])

    def test_preview_with_warning_can_be_applied_through_service(self):
        self.add_meter_without_power()
        controller = self.controller()
        with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN': self.target_dsn}):
            profile = controller.dispatch('POST', '/profiles', self.profile)
            request = {'profile_id': profile['id'], 'from': '2026-10-15', 'to': '2026-10-16'}
            preview = controller.dispatch('POST', '/jobs', request)
            process_job(controller, preview)
            self.assertTrue(self.store.profile(profile['id'])['validated'])
            self.assertTrue(next(e for e in self.store.job(preview['id'])['events'] if e['event'] == 'quality')['warnings'])
            self.store.query("UPDATE adapter_control.jobs SET status='succeeded' WHERE id=%s", (preview['id'],))
            apply = controller.dispatch('POST', '/jobs', {**request, 'kind': 'apply', 'preview_id': preview['id']})
            process_job(controller, apply)
            self.assertEqual(self.totals(), {'rows': 48, 'energy': Decimal(48)})

    def test_inspection_and_schema_change(self):
        with psycopg.connect(self.source_dsn, row_factory=dict_row) as conn:
            report = inspect_source(conn, 'public.customer')
            self.assertEqual(report['suggestions']['ts']['column'], 'время')
            self.assertEqual(report['suggestions']['sensor_name']['column'], 'meter_id')
            with self.assertRaises(ValueError):
                source_cte(self.profile, {'время': 'timestamp'})

    def controller(self):
        return Controller(self.store, {'paradigm': self.source_dsn})

    def test_control_workflow_preview_apply_activate_and_progress(self):
        controller = self.controller()
        with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN': self.target_dsn}):
            profile = controller.dispatch('POST', '/profiles', self.profile)
            request = {'profile_id': profile['id'], 'from': '2026-10-15', 'to': '2026-10-16'}
            with self.assertRaises(ValueError):
                controller.dispatch('POST', '/jobs', {**request, 'kind': 'apply'})
            preview = controller.dispatch('POST', '/jobs', request)
            process_job(controller, preview)
            self.store.query("UPDATE adapter_control.jobs SET status='succeeded' WHERE id=%s", (preview['id'],))
            apply = controller.dispatch('POST', '/jobs', {**request, 'kind': 'apply', 'preview_id': preview['id']})
            process_job(controller, apply)
            self.assertEqual(self.totals()['energy'], 48)
            self.assertIsNone(self.store.profile(profile['id'])['processed_until'])
            controller.dispatch('POST', f"/profiles/{profile['id']}/activate", {})
            self.assertTrue(self.store.profile(profile['id'])['active'])
            auto = self.store.enqueue(profile['id'], 'scheduled', date(2026, 10, 15), date(2026, 10, 16))
            process_job(controller, auto)
            self.assertEqual(self.store.profile(profile['id'])['processed_until'], date(2026, 10, 16))
            with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN': 'dbname=unavailable'}):
                with self.assertRaises(ValueError):
                    process_job(controller, apply)
            self.assertEqual(self.totals()['energy'], 48)

    def test_job_event_order_and_deduplication(self):
        from psycopg.types.json import Jsonb
        profile = self.store.query('INSERT INTO adapter_control.profiles(id,document) VALUES(%s,%s) RETURNING *', ('test', Jsonb(self.profile)), one=True)
        job = self.store.enqueue(profile['id'], 'scheduled', slot='daily')
        self.assertIsNone(self.store.enqueue(profile['id'], 'scheduled', slot='daily'))
        self.store.event(job['id'], {'event': 'first'})
        self.store.event(job['id'], {'event': 'second'})
        self.assertEqual([e['event'] for e in self.store.job(job['id'])['events']], ['first', 'second'])

    def test_commit_before_control_failure_can_be_retried(self):
        controller = self.controller()
        with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN': self.target_dsn}):
            profile = controller.dispatch('POST', '/profiles', self.profile)
            job = self.store.enqueue(profile['id'], 'scheduled', date(2026, 10, 15), date(2026, 10, 16))
            original_query = self.store.query
            def fail_progress(statement, *args, **kwargs):
                if isinstance(statement, str) and statement.startswith('UPDATE adapter_control.profiles SET processed_until'):
                    raise psycopg.OperationalError('simulated control disconnect')
                return original_query(statement, *args, **kwargs)
            with patch.object(self.store, 'query', side_effect=fail_progress):
                with self.assertRaises(psycopg.OperationalError):
                    process_job(controller, job)
            self.assertEqual(self.totals(), {'rows': 24, 'energy': Decimal(48)})
            self.assertIsNone(self.store.profile(profile['id'])['processed_until'])
            process_job(controller, job)
            self.assertEqual(self.totals()['rows'], 24)
            self.assertEqual(self.store.profile(profile['id'])['processed_until'], date(2026, 10, 16))

    def test_repeat_cannot_skip_a_failed_earlier_day(self):
        controller = self.controller()
        with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN': self.target_dsn}):
            profile = controller.dispatch('POST', '/profiles', self.profile)
            self.store.query('UPDATE adapter_control.profiles SET processed_until=%s WHERE id=%s',
                             (date(2026, 10, 14), profile['id']))
            repeat = self.store.enqueue(profile['id'], 'scheduled', date(2026, 10, 15), date(2026, 10, 16), slot='repeat')
            process_job(controller, repeat)
            self.assertEqual(self.totals()['rows'], 24)
            self.assertEqual(self.store.profile(profile['id'])['processed_until'], date(2026, 10, 14))

    def test_separate_directory_reader_with_real_restricted_roles(self):
        raw_role, metadata_role = 'adapter_test_raw_reader', 'adapter_test_directory_reader'
        with psycopg.connect(self.source_dsn, autocommit=True) as conn:
            for role in (raw_role, metadata_role):
                if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                    conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'test-only-reader'").format(sql.Identifier(role)))
                conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT SELECT ON public.customer TO {}').format(sql.Identifier(raw_role)))
            conn.execute(sql.SQL('GRANT SELECT ON public.sensor_directory TO {}').format(sql.Identifier(metadata_role)))
        raw_dsn = make_conninfo(self.source_dsn, user=raw_role, password='test-only-reader')
        directory_dsn = make_conninfo(self.source_dsn, user=metadata_role, password='test-only-reader')
        with psycopg.connect(raw_dsn) as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute('SELECT * FROM public.sensor_directory')
        with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN': self.target_dsn}):
            controller = Controller(self.store, {'paradigm': raw_dsn, 'directory': directory_dsn})
            profile = controller.dispatch('POST', '/profiles', self.profile)
            stored_signature = self.store.profile(profile['id'])['signature']
            # An already saved source-only profile remains valid when a metadata reader is added.
            self.assertEqual(stored_signature, fingerprint(build_settings(self.profile, {'paradigm': raw_dsn})))
            preview = self.store.enqueue(profile['id'], 'preview', date(2026, 10, 15), date(2026, 10, 16))
            process_job(controller, preview)
            self.assertTrue(self.store.profile(profile['id'])['validated'])
            self.assertEqual(self.totals()['rows'], 0)
            apply = self.store.enqueue(profile['id'], 'apply', date(2026, 10, 15), date(2026, 10, 16))
            process_job(controller, apply)
            self.assertEqual(self.totals(), {'rows': 24, 'energy': Decimal(48)})
            with psycopg.connect(directory_dsn) as conn:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    conn.execute('SELECT * FROM public.customer')
