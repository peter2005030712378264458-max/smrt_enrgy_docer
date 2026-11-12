"""Explicit benchmark against disposable PostgreSQL only; never loads .env."""
import importlib.util
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from time import perf_counter

import psycopg
from psycopg.rows import dict_row

from adapter.mapping import column_types, source_cte
from adapter.model import Settings
from adapter.queries import hourly_query
from adapter.runner import refresh
from adapter.tests.test_adapter import IntegrationTests


def main():
    if not os.environ.get('ADAPTER_TEST_ADMIN_DSN'):
        raise SystemExit('Requires isolated ADAPTER_TEST_ADMIN_DSN')
    IntegrationTests.setUpClass()
    fixture = IntegrationTests()
    fixture.setUp()
    profile = {**fixture.profile, 'sample_seconds': 5,
        'sensor_map': {str(i): f'Meter {i}' for i in range(1, 50)}}
    settings = Settings(fixture.source_dsn, fixture.target_dsn, source_table='public.customer',
                        sample_seconds=Decimal(5), profile=profile)
    with psycopg.connect(fixture.source_dsn) as conn:
        conn.execute('TRUNCATE customer,sensor_directory')
        conn.execute("INSERT INTO sensor_directory SELECT 'Meter '||n FROM generate_series(1,49)n")
        conn.execute('''INSERT INTO customer SELECT ts,m::text,
            CASE WHEN EXTRACT(second FROM ts)=0 THEN NULL ELSE 1 END,230
            FROM generate_series(timestamp '2026-10-15 00:00',timestamp '2026-10-15 23:59:55',interval '5 seconds') ts
            CROSS JOIN generate_series(1,48)m''')
        conn.execute('INSERT INTO customer SELECT * FROM customer WHERE EXTRACT(EPOCH FROM "время")::bigint % 50=0')
    result = {}
    with psycopg.connect(fixture.source_dsn, autocommit=True, row_factory=dict_row) as conn:
        conn.execute('SET default_transaction_read_only=on')
        conn.execute('SET jit=off')
        normalized = source_cte(profile, column_types(conn, profile))
        params = {'start':datetime(2026,10,14,21,tzinfo=timezone.utc),
                  'end':datetime(2026,10,15,21,tzinfo=timezone.utc),'timezone':'Europe/Moscow'}
        result['source_rows'] = conn.execute('SELECT COUNT(*) AS n FROM customer').fetchone()['n']
        baseline_path = os.environ.get('AGGREGATION_BASELINE_FILE')
        if baseline_path:
            spec = importlib.util.spec_from_file_location('adapter._baseline_queries', baseline_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            start = perf_counter()
            conn.execute(module.hourly_query(settings.source_table, normalized), params).fetchall()
            result['original_query_seconds'] = round(perf_counter()-start, 3)
        start = perf_counter()
        rows = conn.execute(hourly_query(settings.source_table, normalized, sample_seconds=Decimal(5)), params).fetchall()
        result['new_query_seconds'] = round(perf_counter()-start, 3)
        result['sql_output_rows'] = len(rows)
    start = perf_counter()
    events = refresh(settings, start=date(2026,10,15), end=date(2026,10,16), apply=False,
        now=datetime(2026,10,18,tzinfo=timezone.utc), allowed_sensors={f'Meter {i}' for i in range(1,50)})
    result['dry_run_seconds'] = round(perf_counter()-start, 3)
    day = next(e for e in events if e['event']=='day')
    result['hourly_rows'] = day['hourly_rows']
    result['daily_rows'] = day['daily_rows']
    assert day['hourly_rows']==1176 and day['daily_rows']==49
    print(json.dumps(result))


if __name__ == '__main__':
    main()
