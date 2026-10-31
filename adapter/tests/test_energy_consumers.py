"""Actual consumer SQL against test aggregates, independent of SQL-string assertions."""
import os
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import psycopg


def check_missing_measurements(q, metadata, request, settings_class, period_class, fetch_stats):
    from django.test import override_settings
    from adapter.tests.test_source_schema import SourceSchemaTests
    fixture = SourceSchemaTests()
    fixture.setUp()
    fixture.reset_source(('ts', 'sensor_name', 'u1', 'frequency'))
    fixture.run_refresh(True)
    request.query_params['granularity'] = 'hour'
    for stored in (False, True):
        with override_settings(ENERGY_USE_STORED_ENERGY=stored), \
                patch.object(q, '_matching_data_names', return_value=['Meter A']), \
                patch.object(q, '_with_metadata', return_value=metadata), \
                patch.object(q, '_device_labels', return_value={}):
            summary = q.get_summary(request)
            assert summary['total_energy_kwh'] is None
            assert summary['avg_power_kw'] is None
            assert summary['current_power_kw'] is None
            assert summary['avg_voltage_v'] == 230 and summary['avg_frequency_hz'] == 50
            points = q.get_timeseries(request)['points']
            assert points and all(point['value'] is None and point['energy_kwh'] is None for point in points)
            assert q.get_top_devices(request)[0]['energy_kwh'] is None
            assert q.get_room_loads(request)[0]['energy_kwh'] is None
            assert q.get_room_loads(request)[0]['avg_power_kw'] is None
            assert q._aggregate_room_load_rows(q.get_room_loads(request), 'Other')['avg_power_kw'] is None
            assert all(day['energy_kwh'] is None for day in q.get_weekday_heatmap(request)['meters'][0]['days'])
            request.query_params['granularity'] = 'day'
            assert q.get_timeseries(request)['points'][0]['value'] is None
            request.query_params['granularity'] = 'hour'
        settings = settings_class(ANALYTICS_POSTGRES_HOST='db', ANALYTICS_POSTGRES_PORT=5432,
                                  ANALYTICS_POSTGRES_DB='test_adapter_target', ANALYTICS_POSTGRES_USER='postgres',
                                  ANALYTICS_POSTGRES_PASSWORD='adapter-test-only', ENERGY_USE_STORED_ENERGY=stored)
        stats = fetch_stats(period_class(date_from=date(2026, 10, 15), date_to=date(2026, 10, 15)), 'period_1', ['Meter A'], settings)
        assert stats.mean_kw is None and stats.observations == 0 and stats.total_energy_kwh is None

    # A known zero must still be returned as zero, in either energy mode.
    fixture.reset_source(('ts', 'sensor_name', 'pt'))
    with psycopg.connect(fixture.source_dsn) as connection:
        connection.execute('UPDATE public.review_readings SET pt=0')
    fixture.run_refresh(True)
    for stored in (False, True):
        with override_settings(ENERGY_USE_STORED_ENERGY=stored), patch.object(q, '_matching_data_names', return_value=['Meter A']):
            assert q.get_summary(request)['total_energy_kwh'] == 0
            assert all(point['value'] == 0 and point['energy_kwh'] == 0 for point in q.get_timeseries(request)['points'])

    # An unknown meter is not silently omitted from total power or the z-test.
    fixture.fixture.setUp()
    fixture.fixture.add_meter_without_power()
    fixture.fixture.run_refresh(True, allowed_sensors={'Meter A', 'Meter B'})
    for stored in (False, True):
        with override_settings(ENERGY_USE_STORED_ENERGY=stored), patch.object(q, '_matching_data_names', return_value=['Meter A', 'Meter B']):
            assert q.get_summary(request)['current_power_kw'] is None
            assert q.get_summary(request)['total_energy_kwh'] is None
            assert all(point['value'] is None for point in q.get_timeseries(request)['points'])


def main():
    from adapter.tests.test_adapter import IntegrationTests
    IntegrationTests.setUpClass()
    fixture = IntegrationTests()
    fixture.setUp()
    fixture.run_refresh(True)
    os.environ.update({
        'POSTGRES_HOST': 'db', 'POSTGRES_PORT': '5432', 'POSTGRES_USER': 'postgres',
        'POSTGRES_PASSWORD': 'adapter-test-only', 'DJANGO_POSTGRES_DB': 'test_adapter_target',
        'ENERGY_AGG_POSTGRES_DB': 'test_adapter_target', 'SOURCE_POSTGRES_DB': 'test_adapter_source',
        'ENERGY_USE_STORED_ENERGY': '1', 'DJANGO_SETTINGS_MODULE': 'config.settings',
    })
    sys.path.insert(0, '/app/backend')
    import django
    django.setup()
    from apps.consumption import dashboard_queries as q
    request = SimpleNamespace(query_params={'from': '2026-10-15T00:00:00+03:00', 'to': '2026-10-15T23:59:59+03:00', 'granularity': 'hour'})
    metadata = "WITH devices AS (SELECT 'Meter A'::text data_name, 'Meter A'::text dashboard_label, '101'::text power_location), breakers AS (SELECT 'Meter A'::text data_name, '101'::text room)"
    with patch.object(q, '_matching_data_names', return_value=['Meter A']), patch.object(q, '_with_metadata', return_value=metadata), patch.object(q, '_device_labels', return_value={}):
        assert q.get_summary(request)['total_energy_kwh'] == 48
        assert sum(p['energy_kwh'] for p in q.get_timeseries(request)['points']) == 48
        assert q.get_top_devices(request)[0]['energy_kwh'] == 48
        assert q.get_room_loads(request)[0]['energy_kwh'] == 48
        assert sum(d['energy_kwh'] or 0 for d in q.get_weekday_heatmap(request)['meters'][0]['days']) == 48
        request.query_params['granularity'] = 'day'
        assert q.get_timeseries(request)['points'][0]['energy_kwh'] == 48
        with psycopg.connect(fixture.target_dsn) as conn:
            conn.execute("UPDATE student_schema.electricity_sensor_readings_hourly SET energy_kwh=NULL WHERE bucket_start='2026-10-15 00:00+03'")
        assert q.get_summary(request)['total_energy_kwh'] is None
        assert q.get_top_devices(request)[0]['energy_kwh'] is None
        assert q.get_room_loads(request)[0]['energy_kwh'] is None
    sys.path.insert(0, '/app/analytics')
    from app.core.config import Settings
    from app.schemas.period_comparison import PeriodInput
    from app.services.period_comparison import fetch_period_stats
    settings = Settings(ANALYTICS_POSTGRES_HOST='db', ANALYTICS_POSTGRES_PORT=5432,
                        ANALYTICS_POSTGRES_DB='test_adapter_target', ANALYTICS_POSTGRES_USER='postgres',
                        ANALYTICS_POSTGRES_PASSWORD='adapter-test-only', ENERGY_USE_STORED_ENERGY=True)
    result = fetch_period_stats(PeriodInput(date_from=date(2026, 10, 15), date_to=date(2026, 10, 15)), 'period_1', ['Meter A'], settings)
    assert result.total_energy_kwh is None
    assert result.mean_kw == 2 and result.observations == 24
    check_missing_measurements(q, metadata, request, Settings, PeriodInput, fetch_period_stats)
    print('Consumer SQL checks passed: summary, timeseries, top devices, room loads, heatmap, analytics, missing columns, real zero, both energy modes.')


if __name__ == '__main__':
    main()
