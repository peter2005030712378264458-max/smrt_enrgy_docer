"""Exact metric weights and explicit, atomic target-schema upgrades."""
import os
import unittest

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from adapter.model import DAILY_METRICS, METRICS, Settings
from adapter.runner import RefreshError, target_lock
from adapter.schema import upgrade_aggregates
from adapter.tests import test_adapter as fixtures


@unittest.skipUnless(os.environ.get('ADAPTER_TEST_ADMIN_DSN'), 'Isolated test PostgreSQL not configured')
class MetricCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.IntegrationTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.IntegrationTests()
        self.fixture.setUp()

    def legacy_tables(self):
        settings = Settings('invalid-source-not-used', self.fixture.target_dsn,
            hourly_table='student_schema.review_upgrade_hourly', daily_table='student_schema.review_upgrade_daily')
        with psycopg.connect(settings.target_dsn) as connection:
            for destination, source, metrics in ((settings.hourly_table, self.fixture.settings.hourly_table, METRICS),
                                                  (settings.daily_table, self.fixture.settings.daily_table, DAILY_METRICS)):
                connection.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(sql.SQL(destination)))
                connection.execute(sql.SQL('CREATE TABLE {} (LIKE {} INCLUDING ALL)').format(sql.SQL(destination), sql.SQL(source)))
                for metric in metrics:
                    connection.execute(sql.SQL('ALTER TABLE {} DROP COLUMN {}').format(sql.SQL(destination), sql.Identifier(metric + '_count')))
                connection.execute(sql.SQL("INSERT INTO {}(bucket_start,sensor_name,points_count,active_power_w_avg,energy_kwh) VALUES('2026-10-15','Meter A',24,2000,48)").format(sql.SQL(destination)))
        return settings

    def test_upgrade_is_additive_idempotent_and_does_not_guess_legacy_counts(self):
        settings = self.legacy_tables()
        self.assertEqual(len(upgrade_aggregates(settings)), len(METRICS) + len(DAILY_METRICS))
        self.assertEqual(upgrade_aggregates(settings), [])
        with psycopg.connect(settings.target_dsn, row_factory=dict_row) as connection:
            row = connection.execute('SELECT * FROM student_schema.review_upgrade_hourly').fetchone()
            self.assertEqual(row['energy_kwh'], 48)
            self.assertEqual(row['active_power_w_avg'], 2000)
            self.assertIsNone(row['active_power_w_avg_count'])

    def test_upgrade_failure_rolls_back_both_tables(self):
        settings = self.legacy_tables()
        with psycopg.connect(settings.target_dsn) as connection:
            connection.execute('ALTER TABLE student_schema.review_upgrade_daily ADD COLUMN active_power_w_avg_count numeric')
        with self.assertRaisesRegex(RefreshError, 'expected bigint'):
            upgrade_aggregates(settings)
        with psycopg.connect(settings.target_dsn) as connection:
            columns = [row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='student_schema' AND table_name='review_upgrade_hourly'")]
            self.assertNotIn('active_power_w_avg_count', columns)
            self.assertEqual(connection.execute('SELECT energy_kwh FROM student_schema.review_upgrade_hourly').fetchone()[0], 48)

    def test_upgrade_obeys_refresh_lock(self):
        settings = self.legacy_tables()
        with psycopg.connect(settings.target_dsn, autocommit=True, row_factory=dict_row) as connection:
            with target_lock(connection, settings), self.assertRaisesRegex(RefreshError, 'already running'):
                upgrade_aggregates(settings)

    def test_counts_track_valid_measurements_and_daily_sums(self):
        with psycopg.connect(self.fixture.source_dsn) as connection:
            connection.execute("UPDATE public.customer SET voltage=NULL WHERE время < '2026-10-15 12:00'")
            connection.execute("UPDATE public.customer SET power_kw=NULL WHERE время='2026-10-15 00:00'")
        self.fixture.run_refresh(True)
        with psycopg.connect(self.fixture.target_dsn, row_factory=dict_row) as connection:
            daily = connection.execute('SELECT * FROM student_schema.electricity_sensor_readings_daily').fetchone()
            self.assertEqual(daily['points_count'], 24)
            self.assertEqual(daily['active_power_w_avg_count'], 23)
            self.assertEqual(daily['voltage_avg_v_count'], 12)
            self.assertEqual(daily['frequency_hz_avg_count'], 0)
            self.assertIsNone(daily['frequency_hz_avg'])
            self.assertIsNone(daily['energy_kwh'])
            hourly = connection.execute('SELECT SUM(active_power_w_avg_count) AS power_count,SUM(voltage_avg_v_count) AS voltage_count FROM student_schema.electricity_sensor_readings_hourly').fetchone()
            self.assertEqual(tuple(hourly.values()), (23, 12))
