"""Actual consumer SQL against test aggregates, independent of SQL-string assertions."""
import os
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import psycopg


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
    print('Consumer SQL checks passed: summary, timeseries, top devices, room loads, heatmap, analytics, NULL propagation.')


if __name__ == '__main__':
    main()
