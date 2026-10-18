from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .model import DAILY_COLUMNS, HOURLY_COLUMNS, Settings, build_rows, day_windows, plan_window, table_parts
from .queries import SOURCE_COLUMNS, hourly_query, insert_query, window_query
from .mapping import column_types, source_cte
from psycopg import sql


class RefreshError(Exception):
    pass


class RefreshInterrupted(RefreshError):
    pass


def fingerprint(settings):
    def public_connection(dsn):
        values = conninfo_to_dict(dsn)
        return {key: values.get(key) for key in ("host", "hostaddr", "port", "dbname", "user", "service")}
    value = {"version": 1, "source": public_connection(settings.source_dsn),
             "target": public_connection(settings.target_dsn), "tables": [settings.source_table, settings.hourly_table, settings.daily_table],
             "timezone": settings.timezone, "sample_seconds": str(settings.sample_seconds), "profile": settings.profile}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read_checkpoint(path, expected_fingerprint):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 1 or value.get("fingerprint") != expected_fingerprint:
        raise RefreshError("Checkpoint belongs to different connections, tables, timezone or energy settings")
    result = datetime.fromisoformat(value["processed_until"])
    if result.tzinfo is None:
        raise RefreshError("Checkpoint must contain a timezone")
    return result


def save_checkpoint(path, signature, watermark):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"version": 1, "fingerprint": signature, "processed_until": watermark.isoformat()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def preflight(connection, name, columns, bucket_type=None):
    schema, relation = table_parts(name)
    rows = connection.execute("""
        SELECT a.attname, t.typname, c.relkind, a.attnotnull
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid JOIN pg_type t ON t.oid=a.atttypid
        WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped
    """, (schema, relation)).fetchall()
    available = {row["attname"]: row for row in rows}
    missing = set(columns) - available.keys()
    if missing:
        raise RefreshError(f"{name}: missing columns: {', '.join(sorted(missing))}. No schema changes were made.")
    if bucket_type:
        if available["bucket_start"]["typname"] != bucket_type or available["bucket_start"]["relkind"] not in ("r", "p"):
            raise RefreshError(f"{name}: expected a local table with bucket_start of type {bucket_type}")
        # The current schema's unique key protects one aggregate per sensor and bucket.
        keys = connection.execute("""
            SELECT array_agg(a.attname::text ORDER BY a.attname) AS names
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=ANY(k.conkey)
            WHERE n.nspname=%s AND c.relname=%s AND k.contype IN ('p','u')
            GROUP BY k.oid
        """, (schema, relation)).fetchall()
        if not any(row["names"] == ["bucket_start", "sensor_name"] for row in keys):
            raise RefreshError(f"{name}: UNIQUE(bucket_start, sensor_name) or equivalent primary key is required")
    elif available["ts"]["typname"] != "timestamptz":
        raise RefreshError(f"{name}: source ts must be timestamp with time zone")


@contextmanager
def target_lock(connection, settings):
    # Shared hourly OR daily table means shared lock, even if the other table differs.
    keys = sorted({int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
                   for name in (settings.hourly_table, settings.daily_table)})
    acquired = []
    try:
        for key in keys:
            if not connection.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,)).fetchone()["acquired"]:
                raise RefreshError("Another aggregate refresh is already running")
            acquired.append(key)
        yield
    finally:
        for key in reversed(acquired):
            connection.execute("SELECT pg_advisory_unlock(%s)", (key,))


def configure_session(connection, settings, readonly):
    connection.execute("SELECT set_config('default_transaction_read_only', %s, false)", ("on" if readonly else "off",))
    connection.execute("SELECT set_config('statement_timeout', %s, false)", (str(settings.statement_timeout_seconds * 1000),))
    connection.execute("SET TIME ZONE 'UTC'")


def quality_report(source, normalized, settings, begin, finish, stats, target, allowed_sensors):
    """Reject unsafe replacement before deleting anything; retain diagnostics in dry-run."""
    profile = settings.profile
    sample = settings.sample_seconds
    params = {'start': begin, 'end': finish}
    rows = source.execute(sql.SQL('''
        WITH readings AS ({}), ordered AS (
            SELECT sensor_name, ts, EXTRACT(EPOCH FROM ts - LAG(ts) OVER
                (PARTITION BY sensor_name ORDER BY ts)) AS step FROM readings
        ) SELECT COUNT(*) AS points,
          COUNT(*) FILTER (WHERE sensor_name IS NULL OR BTRIM(sensor_name)='') AS invalid_names,
          COUNT(*) FILTER (WHERE step=0) AS duplicates,
          COUNT(step) AS intervals,
          COUNT(*) FILTER (WHERE step BETWEEN %(low)s AND %(high)s) AS matched,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY step) FILTER (WHERE step>0) AS median_seconds
        FROM ordered
    ''').format(normalized), {**params, 'low': sample * Decimal(str(1-profile['quality']['interval_tolerance'])),
                              'high': sample * Decimal(str(1+profile['quality']['interval_tolerance']))}).fetchone()
    failures = []
    warnings = []
    if rows['invalid_names']:
        failures.append('Есть показания без идентификатора счётчика')
    if rows['points'] and rows['duplicates'] / rows['points'] > profile['quality']['max_duplicate_fraction']:
        failures.append('Слишком много дубликатов времени')
    if rows['intervals'] and rows['matched'] / rows['intervals'] < profile['quality']['min_interval_match']:
        failures.append('Интервалы показаний не соответствуют профилю')
    expected = Decimal(str((finish.timestamp()-begin.timestamp()))) / sample
    counts = {}
    for row in stats:
        name = row['sensor_name']
        counts[name] = counts.get(name, 0) + row['active_power_w_avg_count']
    if not counts:
        failures.append('Нет исходных показаний: день не отмечен обработанным')
    coverage = {name: float(Decimal(count) / expected) for name, count in counts.items()}
    missing_power = sorted(name for name, count in counts.items() if count == 0)
    if missing_power:
        warnings.append('Нет корректной активной мощности; мощность и энергия останутся NULL (нет данных): '
                        + ', '.join(missing_power)
                        + '. Полное потребление по всем счётчикам определить нельзя.')
    insufficient_power = sorted(name for name, value in coverage.items()
                                if counts[name] > 0 and value < profile['quality']['min_coverage'])
    if insufficient_power:
        failures.append('Неполный день или недостаточно корректных показаний мощности: '
                        + ', '.join(insufficient_power))
    if counts and not any(counts.values()):
        failures.append('Нет корректной активной мощности ни у одного счётчика')
    if allowed_sensors is not None and set(counts) - set(allowed_sensors):
        failures.append('Имена счётчиков отсутствуют в справочнике приложения')
    previous = target.execute(sql.SQL('''SELECT sensor_name, SUM(points_count) AS points
        FROM {} WHERE bucket_start >= %s AND bucket_start < %s GROUP BY sensor_name''').format(
            sql.Identifier(*table_parts(settings.hourly_table))), (begin, finish)).fetchall()
    if not previous:
        from datetime import timedelta
        previous = target.execute(sql.SQL('''SELECT sensor_name, SUM(points_count) AS points
            FROM {} WHERE bucket_start >= %s AND bucket_start < %s GROUP BY sensor_name''').format(
                sql.Identifier(*table_parts(settings.hourly_table))), (begin-timedelta(days=1), begin)).fetchall()
    point_counts = {}
    for row in stats:
        point_counts[row['sensor_name']] = point_counts.get(row['sensor_name'], 0) + row['points_count']
    if any(point_counts.get(r['sensor_name'], 0) < float(r['points']) * profile['quality']['min_existing_points_ratio'] for r in previous):
        failures.append('Исчезли счётчики или существенно уменьшилось число показаний')
    return {**dict(rows), 'coverage': coverage, 'missing_power_sensors': missing_power,
            'warnings': warnings, 'errors': failures}


def refresh(settings: Settings, *, start: date | None = None, end: date | None = None,
            apply=False, checkpoint_path=Path("energy_aggregation/local.checkpoint.json"),
            allow_empty=False, now=None, emit=None, allowed_sensors=None, stop_requested=None):
    """No DDL, scheduler installation or writes to the source. Dry-run is the default."""
    checkpoint_path = Path(checkpoint_path)
    signature = fingerprint(settings)
    now = now or datetime.now(timezone.utc)
    events = []

    def report(event):
        events.append(event)
        if emit:
            emit(event)

    # Checkpoint must be reread after acquiring the target lock.
    with psycopg.connect(settings.source_dsn, autocommit=True, row_factory=dict_row, connect_timeout=8) as source, \
            psycopg.connect(settings.target_dsn, autocommit=True, row_factory=dict_row, connect_timeout=8) as target:
        configure_session(source, settings, readonly=True)
        configure_session(target, settings, readonly=not apply)
        source_identity = source.execute("SELECT current_database() AS name").fetchone()["name"]
        target_identity = target.execute("SELECT current_database() AS name").fetchone()["name"]
        if source_identity == target_identity:
            source_endpoint = source.execute('SELECT inet_server_addr()::text AS host, inet_server_port() AS port').fetchone()
            target_endpoint = target.execute('SELECT inet_server_addr()::text AS host, inet_server_port() AS port').fetchone()
            if source_endpoint == target_endpoint:
                raise RefreshError("Use separate source and target databases")
        normalized = None
        if settings.profile:
            normalized = source_cte(settings.profile, column_types(source, settings.profile))
        else:
            preflight(source, settings.source_table, SOURCE_COLUMNS)
        preflight(target, settings.hourly_table, HOURLY_COLUMNS, "timestamptz")
        preflight(target, settings.daily_table, DAILY_COLUMNS, "date")
        with target_lock(target, settings):
            automatic = start is None
            watermark = read_checkpoint(checkpoint_path, signature) if automatic else None
            begin, finish = plan_window(settings, now, start, end, watermark, apply)
            report({"event": "plan", "mode": "apply" if apply else "dry-run", "from": begin.isoformat(),
                    "to_exclusive": finish.isoformat(), "timezone": settings.timezone,
                    "energy": "fixed-interval estimate" if settings.sample_seconds is not None else "not calculated"})
            if end is None or finish.date() < end:
                report({'event': 'notice', 'detail': 'Сегодняшние и будущие незавершённые сутки исключены',
                        'to_exclusive': finish.isoformat()})
            for day_start, day_end in day_windows(begin, finish, settings.timezone):
                if stop_requested and stop_requested():
                    raise RefreshInterrupted('Остановка на границе суток; задание будет возобновлено')
                with source.transaction():
                    source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                    stats = source.execute(hourly_query(settings.source_table, normalized),
                                           {"start": day_start, "end": day_end, "timezone": settings.timezone}).fetchall()
                    if settings.profile:
                        mapping = settings.profile.get('sensor_map', {})
                        raw_names = {row['sensor_name'] for row in stats}
                        if len({mapping.get(name, name) for name in raw_names}) != len(raw_names):
                            raise RefreshError('Преобразование имён объединило разные счётчики')
                        for row in stats:
                            row['sensor_name'] = mapping.get(row['sensor_name'], row['sensor_name'])
                        if len({(r['bucket_start'], r['sensor_name']) for r in stats}) != len(stats):
                            raise RefreshError('Преобразование имён объединило разные счётчики')
                        quality = quality_report(source, normalized, settings, day_start, day_end, stats, target, allowed_sensors)
                        report({'event': 'quality', 'date': day_start.date().isoformat(), **quality})
                        if apply and quality['errors']:
                            raise RefreshError('; '.join(quality['errors']))
                hourly, daily = build_rows(stats, settings)
                day = day_start.date()
                next_day = day.fromordinal(day.toordinal() + 1)
                if not hourly and not allow_empty:
                    existing_hours = target.execute(window_query(settings.hourly_table, exists=True), (day_start, day_end)).fetchone()["exists"]
                    existing_days = target.execute(window_query(settings.daily_table, exists=True), (day, next_day)).fetchone()["exists"]
                    if existing_hours or existing_days:
                        raise RefreshError(f"{day}: source is empty but target has data; inspect the source or explicitly use --allow-empty")
                if apply and not hourly and not allow_empty:
                    raise RefreshError(f'{day}: empty source; checkpoint was not advanced')
                if apply:
                    # Readers see either the previous complete pair or the refreshed pair.
                    with target.transaction():
                        target.execute("SET LOCAL lock_timeout='5s'")
                        target.execute(window_query(settings.hourly_table), (day_start, day_end))
                        target.execute(window_query(settings.daily_table), (day, next_day))
                        with target.cursor() as cursor:
                            if hourly:
                                cursor.executemany(insert_query(settings.hourly_table, HOURLY_COLUMNS),
                                                   [tuple(row[c] for c in HOURLY_COLUMNS) for row in hourly])
                            if daily:
                                cursor.executemany(insert_query(settings.daily_table, DAILY_COLUMNS),
                                                   [tuple(row[c] for c in DAILY_COLUMNS) for row in daily])
                    watermark = max(watermark, day_end) if watermark else day_end
                    if automatic:
                        save_checkpoint(checkpoint_path, signature, watermark)
                report({"event": "day", "date": day.isoformat(), "hourly_rows": len(hourly),
                        "daily_rows": len(daily), "source_points": sum(row["points_count"] for row in hourly),
                        "partial_day": day_end.hour != 0 or day_end.minute != 0 or day_end.second != 0 or day_end.microsecond != 0,
                        "written": apply})
    return events
