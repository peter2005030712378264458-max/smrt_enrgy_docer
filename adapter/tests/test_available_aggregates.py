"""Numeric regressions for available data, missing meters and day outcomes."""
from datetime import date, datetime, timezone
from decimal import Decimal
import os
import unittest
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from adapter.model import Settings
from adapter.runner import refresh
from adapter.tests import test_adapter as fixtures


@unittest.skipUnless(os.environ.get('ADAPTER_TEST_ADMIN_DSN'), 'Isolated PostgreSQL required')
class AvailableAggregateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.IntegrationTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.IntegrationTests()
        self.fixture.setUp()
        self.profile = self.fixture.profile
        self.profile['sample_seconds'] = 5
        self.settings = Settings(self.fixture.source_dsn, self.fixture.target_dsn,
            source_table='public.customer', sample_seconds=Decimal(5), profile=self.profile)
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute('TRUNCATE customer')

    def run_refresh(self, **kwargs):
        return refresh(self.settings, start=date(2026, 10, 15), end=date(2026, 10, 16),
            apply=True, now=datetime(2026, 10, 18, tzinfo=timezone.utc),
            allowed_sensors={'Meter A', 'Silent'}, **kwargs)

    def rows(self):
        with psycopg.connect(self.fixture.target_dsn, row_factory=dict_row) as conn:
            return conn.execute('SELECT * FROM student_schema.electricity_sensor_readings_daily ORDER BY sensor_name').fetchall()

    def test_duplicates_partial_data_and_silent_meter_are_written(self):
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("""INSERT INTO customer VALUES
                ('2026-10-15 00:00:00','42',1,230),
                ('2026-10-15 00:00:00','42',1,230),
                ('2026-10-15 00:00:00','42',3,230),
                ('2026-10-15 00:00:05','42',NULL,230)""")
        self.run_refresh()
        measured, silent = self.rows()
        self.assertEqual(measured['points_count'], 2)
        self.assertEqual(measured['active_power_w_avg_count'], 1)
        self.assertEqual(measured['active_power_w_avg'], 2000)
        self.assertAlmostEqual(measured['available_energy_kwh'], Decimal(10000) / Decimal(3600000), places=27)
        self.assertIsNone(measured['energy_kwh'])
        self.assertEqual(silent['points_count'], 0)
        self.assertIsNone(silent['active_power_w_avg'])
        self.assertIsNone(silent['available_energy_kwh'])
        with psycopg.connect(self.fixture.target_dsn) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM student_schema.electricity_sensor_readings_hourly').fetchone()[0], 48)
        before = self.rows()
        self.run_refresh()
        self.assertEqual(self.rows(), before)

    def test_empty_day_replaces_previous_values_with_null(self):
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("INSERT INTO customer VALUES ('2026-10-15 00:00','42',1,230)")
        self.fixture.run_refresh(True)
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute('TRUNCATE customer')
        self.run_refresh()
        for row in self.rows():
            self.assertEqual(row['points_count'], 0)
            self.assertIsNone(row['energy_kwh'])
            self.assertIsNone(row['available_energy_kwh'])

    def test_complete_zero_and_full_energy(self):
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("""INSERT INTO customer SELECT ts,'42',0,230 FROM generate_series(
                timestamp '2026-10-15 00:00',timestamp '2026-10-15 23:59:55',interval '5 seconds') ts""")
        self.run_refresh()
        measured = self.rows()[0]
        self.assertEqual(measured['points_count'], 17280)
        self.assertEqual(measured['energy_kwh'], 0)
        self.assertEqual(measured['available_energy_kwh'], 0)

    def test_no_coverage_threshold_for_zero_one_fifty_and_eighty_nine_percent(self):
        self.profile['sample_seconds'] = 864
        self.settings = Settings(self.fixture.source_dsn, self.fixture.target_dsn,
            source_table='public.customer', sample_seconds=Decimal(864), profile=self.profile)
        for valid in (0, 1, 50, 89):
            with self.subTest(valid=valid):
                with psycopg.connect(self.fixture.source_dsn) as conn:
                    conn.execute('TRUNCATE customer')
                    conn.execute("""INSERT INTO customer SELECT timestamp '2026-10-15 00:00'+n*interval '864 seconds',
                        '42',CASE WHEN n<%s THEN 1 ELSE NULL END,230 FROM generate_series(0,99)n""", (valid,))
                self.run_refresh()
                row = self.rows()[0]
                self.assertEqual(row['points_count'], 100)
                self.assertEqual(row['active_power_w_avg_count'], valid)
                self.assertEqual(row['active_power_w_avg'], 1000 if valid else None)
                self.assertIsNone(row['energy_kwh'])
                self.assertEqual(row['available_energy_kwh'], Decimal(valid)*Decimal('0.24') if valid else None)

    def test_bad_day_does_not_block_following_day(self):
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("""INSERT INTO customer VALUES
                ('2026-10-15 00:00','unknown',1,230),
                ('2026-10-16 00:00','42',1,230)""")
        events = refresh(self.settings, start=date(2026, 10, 15), end=date(2026, 10, 17),
            apply=True, now=datetime(2026, 10, 18, tzinfo=timezone.utc), allowed_sensors={'Meter A'})
        self.assertEqual([e['date'] for e in events if e['event'] == 'day_skipped'], ['2026-10-15'])
        self.assertEqual([e['date'] for e in events if e['event'] == 'day'], ['2026-10-16'])

    def test_scheduled_skip_progress_and_manual_repair(self):
        from adapter.service import process_job, schedule_ranges
        controller = self.fixture.controller()
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("INSERT INTO customer VALUES ('2026-10-15 00:00','unknown',1,230)")
        with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN': self.fixture.target_dsn}):
            profile = controller.dispatch('POST', '/profiles', self.profile)
            job = self.fixture.store.enqueue(profile['id'], 'scheduled', date(2026,10,15), date(2026,10,17))
            process_job(controller, job)
            current = self.fixture.store.profile(profile['id'])
            self.assertEqual(current['processed_until'], date(2026,10,15))
            self.assertEqual(current['attempted_until'], date(2026,10,17))
            outcomes = controller.dispatch('GET', '/day-results?profile_id='+profile['id'], {})
            self.assertEqual([r['outcome'] for r in outcomes], ['written','skipped'])
            self.assertEqual(next(schedule_ranges(current, datetime(2026,10,18,tzinfo=timezone.utc),0))[0], date(2026,10,17))
            with psycopg.connect(self.fixture.source_dsn) as conn:
                conn.execute("UPDATE customer SET meter_id='42'")
            preview = self.fixture.store.enqueue(profile['id'], 'preview', date(2026,10,15), date(2026,10,16))
            process_job(controller, preview)
            self.fixture.store.query("UPDATE jobs SET status='succeeded' WHERE id=%s", (preview['id'],))
            apply = self.fixture.store.enqueue(profile['id'], 'apply', date(2026,10,15), date(2026,10,16), preview['id'])
            process_job(controller, apply)
            self.assertEqual(self.fixture.store.profile(profile['id'])['processed_until'], date(2026,10,17))

    def test_time_support_does_not_fill_a_completely_empty_next_hour(self):
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("INSERT INTO customer VALUES ('2026-10-15 00:59:58','42',1,230)")
        self.run_refresh()
        with psycopg.connect(self.fixture.target_dsn, row_factory=dict_row) as conn:
            hours = conn.execute("SELECT * FROM student_schema.electricity_sensor_readings_hourly WHERE sensor_name='Meter A' ORDER BY bucket_start").fetchall()
        self.assertAlmostEqual(hours[0]['available_energy_kwh'], Decimal(2000)/Decimal(3600000), places=27)
        self.assertIsNone(hours[1]['available_energy_kwh'])
        self.assertEqual(hours[1]['points_count'], 0)
        self.assertIsNone(hours[1]['energy_kwh'])

    def test_faster_arrivals_never_multiply_duration(self):
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("""INSERT INTO customer SELECT ts,'42',1,230 FROM generate_series(
                timestamp '2026-10-15 00:00',timestamp '2026-10-15 00:00:09',interval '1 second') ts""")
        self.run_refresh()
        # Nine one-second supports followed by the confirmed five-second last support.
        self.assertAlmostEqual(self.rows()[0]['available_energy_kwh'], Decimal(14000)/Decimal(3600000), places=27)

    def test_midnight_carry_does_not_import_previous_day_average(self):
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("""INSERT INTO customer VALUES
                ('2026-10-14 23:59:58','42',2,230),
                ('2026-10-15 00:00:03','42',1,230)""")
        self.run_refresh()
        row = self.rows()[0]
        self.assertEqual(row['points_count'], 1)
        self.assertEqual(row['active_power_w_avg'], 1000)
        self.assertAlmostEqual(row['available_energy_kwh'], Decimal(11000)/Decimal(3600000), places=27)

    def test_entirely_silent_day_stays_null_even_with_previous_day_reading(self):
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("INSERT INTO customer VALUES ('2026-10-14 23:59:58','42',1,230)")
        self.run_refresh()
        self.assertTrue(all(row['points_count']==0 and row['available_energy_kwh'] is None for row in self.rows()))

    def test_directory_includes_silent_meters_and_rejects_invalid_names(self):
        from adapter.service import directory_names
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute("INSERT INTO sensor_directory VALUES ('Silent'),('Meter A')")
        self.assertEqual(directory_names(self.profile, {'paradigm':self.fixture.source_dsn}), {'Meter A','Silent'})
        with psycopg.connect(self.fixture.source_dsn) as conn:
            conn.execute('INSERT INTO sensor_directory VALUES (NULL)')
        with self.assertRaises(ValueError):
            directory_names(self.profile, {'paradigm':self.fixture.source_dsn})

    def test_changed_directory_invalidates_preview(self):
        from adapter.service import process_job
        controller = self.fixture.controller()
        with patch.dict(os.environ, {'ENERGY_AGG_TARGET_DSN':self.fixture.target_dsn}):
            profile = controller.dispatch('POST','/profiles',self.profile)
            preview = self.fixture.store.enqueue(profile['id'],'preview',date(2026,10,15),date(2026,10,16))
            process_job(controller, preview)
            self.fixture.store.query("UPDATE jobs SET status='succeeded' WHERE id=%s",(preview['id'],))
            with psycopg.connect(self.fixture.source_dsn) as conn:
                conn.execute("INSERT INTO sensor_directory VALUES('Silent')")
            apply = self.fixture.store.enqueue(profile['id'],'apply',date(2026,10,15),date(2026,10,16),preview['id'])
            with self.assertRaisesRegex(ValueError,'Состав справочника'):
                process_job(controller, apply)
            self.assertEqual(self.rows(), [])

    def test_explicit_control_upgrade_is_idempotent_in_separate_schema(self):
        from adapter.store import Store
        with patch.dict(os.environ, {'ENERGY_ADAPTER_CONTROL_SCHEMA':'adapter_workstation_test'}):
            store = Store(self.fixture.target_dsn)
            store.install()
            with store.connection() as conn:
                conn.execute('UPDATE settings SET enabled=true,schema_version=1')
                conn.execute('DROP TABLE day_results')
                conn.execute('ALTER TABLE profiles DROP COLUMN attempted_until')
            store.install()
            store.install()
            state = store.query('SELECT * FROM settings WHERE id=1', one=True)
            self.assertEqual(state['schema_version'], 2)
            self.assertTrue(state['enabled'])
            self.assertEqual(store.query('SELECT COUNT(*) AS n FROM day_results', one=True)['n'], 0)
        with psycopg.connect(self.fixture.target_dsn) as conn:
            conn.execute('DROP SCHEMA adapter_workstation_test CASCADE')

    def test_local_setup_preserves_profiles_without_copying_old_execution_state(self):
        import uuid
        from psycopg import sql
        from psycopg.types.json import Jsonb
        from adapter import local_setup
        from adapter.service import build_settings
        from adapter.runner import fingerprint
        from adapter.store import Store
        schema = 'adapter_ws_v3_' + uuid.uuid4().hex[:12]
        registry = {'paradigm':self.fixture.source_dsn}
        try:
            with patch.dict(os.environ, {'ENERGY_ADAPTER_CONTROL_SCHEMA':'adapter_workstation'}):
                legacy = Store(self.fixture.target_dsn)
            legacy.install()
            legacy.query('INSERT INTO profiles(id,document,active,validated) VALUES(%s,%s,true,true)',
                         ('old-profile',Jsonb(self.profile)))
            legacy.query('UPDATE settings SET enabled=true')
            legacy.enqueue('old-profile', 'inspect')
            with patch.dict(os.environ, {'ENERGY_ADAPTER_CONTROL_SCHEMA':schema,
                                         'ENERGY_AGG_TARGET_DSN':self.fixture.target_dsn}), \
                 patch('adapter.local_setup.connections', return_value=registry):
                local_setup.main()
                local_setup.main()
                current = Store(self.fixture.target_dsn)
                expected_signature = fingerprint(build_settings(self.profile, registry))
            profiles = current.query('SELECT * FROM profiles')
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]['document'], self.profile)
            self.assertFalse(profiles[0]['active'])
            self.assertFalse(profiles[0]['validated'])
            self.assertEqual(profiles[0]['signature'], expected_signature)
            self.assertFalse(current.query('SELECT enabled FROM settings',one=True)['enabled'])
            self.assertEqual(current.query('SELECT * FROM jobs'), [])
            self.assertTrue(legacy.profile('old-profile')['active'])
            self.assertTrue(legacy.query('SELECT enabled FROM settings',one=True)['enabled'])
            self.assertEqual(len(legacy.query('SELECT * FROM jobs')), 1)
        finally:
            with psycopg.connect(self.fixture.target_dsn) as conn:
                for name in (schema, 'adapter_workstation'):
                    conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(name)))


class CheckpointTests(unittest.TestCase):
    def test_v2_retains_skips_and_old_format_is_not_reinterpreted(self):
        import json
        from pathlib import Path
        import tempfile
        from zoneinfo import ZoneInfo
        from adapter.runner import fingerprint, save_checkpoint, read_checkpoint, RefreshError
        signature = fingerprint(Settings('dbname=source', 'dbname=target'))
        watermark = datetime(2026,10,17,tzinfo=ZoneInfo('Europe/Moscow'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'checkpoint.json'
            save_checkpoint(path, signature, watermark, ['2026-10-15'])
            self.assertEqual(read_checkpoint(path, signature), watermark)
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['skipped_dates'], ['2026-10-15'])
            self.assertTrue(data['processed_until'].startswith('2026-10-15'))
            path.write_text(json.dumps({'version':1,'fingerprint':signature,'processed_until':watermark.isoformat()}))
            with self.assertRaises(RefreshError):
                read_checkpoint(path, signature)
