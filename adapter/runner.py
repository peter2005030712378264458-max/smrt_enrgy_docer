from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .model import DAILY_COLUMNS, HOURLY_COLUMNS, METRICS, Settings, build_rows, fill_missing_hours, day_windows, plan_window, table_parts
from .queries import SOURCE_COLUMNS, hourly_query, insert_query, window_query
from .mapping import column_types, source_capabilities, source_cte
from psycopg import sql


class RefreshError(Exception):
    pass


class RefreshInterrupted(RefreshError):
    pass


class DayQualityError(RefreshError):
    """A local data problem; no replacement is committed for this day."""


def fingerprint(settings):
    def public_connection(dsn):
        values = conninfo_to_dict(dsn)
        return {key: values.get(key) for key in ("host", "hostaddr", "port", "dbname", "user", "service")}
    value = {"version": 3, "source": public_connection(settings.source_dsn),
             "target": public_connection(settings.target_dsn), "tables": [settings.source_table, settings.hourly_table, settings.daily_table],
             "timezone": settings.timezone, "sample_seconds": str(settings.sample_seconds), "profile": settings.profile}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def directory_signature(sensors):
    return hashlib.sha256(json.dumps(sorted(sensors), ensure_ascii=False).encode()).hexdigest()


def read_checkpoint(path, expected_fingerprint):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 2 or value.get("fingerprint") != expected_fingerprint:
        raise RefreshError("Checkpoint belongs to different connections, tables, timezone or energy settings")
    result = datetime.fromisoformat(value["attempted_until"])
    if result.tzinfo is None:
        raise RefreshError("Checkpoint must contain a timezone")
    return result


def save_checkpoint(path, signature, watermark, skipped_dates=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            processed = min([watermark, *(datetime.fromisoformat(d).replace(tzinfo=watermark.tzinfo)
                                         for d in skipped_dates)])
            json.dump({"version": 2, "fingerprint": signature,
                       "processed_until": processed.isoformat(),
                       'attempted_until': watermark.isoformat(),
                       'skipped_dates': sorted(skipped_dates)}, stream)
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
    keys = sorted({int.from_bytes(hashlib.sha256(json.dumps(table_parts(name)).encode()).digest()[:8], "big", signed=True)
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


def quality_report(source, normalized, settings, begin, finish, stats, target, allowed_sensors, power_available=True):
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
    if rows['duplicates']:
        warnings.append('Повторения времени объединены до расчёта; повторный учёт исключён')
    if rows['matched'] < rows['intervals']:
        warnings.append('Неравномерные интервалы: энергия ограничена известной длительностью измерений')
    expected = Decimal(str((finish.timestamp()-begin.timestamp()))) / sample
    counts = {}
    for row in stats:
        name = row['sensor_name']
        counts[name] = counts.get(name, 0) + row['active_power_w_avg_count']
    if not counts:
        warnings.append('Нет исходных показаний: ожидаемые счётчики будут записаны с NULL')
    point_counts = {}
    for row in stats:
        point_counts[row['sensor_name']] = point_counts.get(row['sensor_name'], 0) + row['points_count']
    coverage_counts = counts if power_available else point_counts
    coverage = {name: float(Decimal(count) / expected) for name, count in coverage_counts.items()}
    missing_power = sorted(name for name, count in counts.items() if count == 0)
    if missing_power:
        warnings.append('Нет корректной активной мощности; мощность и энергия останутся NULL (нет данных): '
                        + ', '.join(missing_power)
                        + '. Полное потребление по всем счётчикам определить нельзя.')
    insufficient_power = sorted(name for name, value in coverage.items()
                                if coverage_counts[name] > 0 and value < 1)
    if insufficient_power:
        warnings.append(('Неполный день или недостаточно корректных показаний мощности: ' if power_available
                         else 'Неполный день или недостаточно исходных показаний: ')
                        + ', '.join(insufficient_power))
    if power_available and counts and not any(counts.values()):
        warnings.append('Нет корректной активной мощности: сохраняются NULL и остальные доступные показатели')
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
    if any(point_counts.get(r['sensor_name'], 0) < r['points'] for r in previous):
        warnings.append('Часть ожидаемых показаний отсутствует; прежние значения будут заменены, в том числе на NULL')
    available_metrics = [metric for metric in METRICS if any(row[metric + '_count'] for row in stats)]
    return {**dict(rows), 'coverage': coverage, 'valid_counts': counts, 'point_counts': point_counts,
            'missing_power_sensors': missing_power,
            'coverage_basis': 'active_power' if power_available else 'readings',
            'available_metrics': available_metrics,
            'unavailable_metrics': [metric for metric in METRICS if metric not in available_metrics],
            'warnings': warnings, 'errors': failures}


def validate_required_rows(source, profile, normalized, begin, finish):
    # A row without a timestamp cannot be assigned to any requested day. Do not
    # silently drop it and certify the replacement as complete (including CLI).
    missing_time = source.execute(sql.SQL('SELECT 1 FROM {} WHERE {} IS NULL LIMIT 1').format(
        sql.Identifier(*table_parts(profile['source_table'])), sql.Identifier(profile['columns']['ts']))).fetchone()
    if missing_time:
        raise RefreshError('В источнике есть строки без времени (NULL). Их нельзя отнести к суткам. Исправьте время или выберите подготовленное представление; прежние агрегаты сохранены.')



def refresh(settings: Settings, *, start: date | None = None, end: date | None = None,
            apply=False, checkpoint_path=Path("energy_aggregation/local.checkpoint.json"),
            allow_empty=False, now=None, emit=None, allowed_sensors=None, stop_requested=None,
            expected_schema_signature=None):
    """No DDL, scheduler installation or writes to the source. Dry-run is the default."""
    checkpoint_path = Path(checkpoint_path)
    if settings.profile and not allowed_sensors:
        raise RefreshError('Нужен непустой полный справочник ожидаемых счётчиков')
    signature = fingerprint(settings)
    now = now or datetime.now(timezone.utc)
    events = []

    def report(event):
        events.append(event)
        if emit:
            try:
                emit(event)
            except (psycopg.DataError, psycopg.IntegrityError, psycopg.errors.QueryCanceled) as exc:
                raise RefreshError('Не удалось сохранить журнал или прогресс; повторите задание после проверки служебной БД') from exc

    # Checkpoint must be reread after acquiring the target lock.
    with psycopg.connect(settings.source_dsn, autocommit=True, row_factory=dict_row, connect_timeout=8) as source, \
            psycopg.connect(settings.target_dsn, autocommit=True, row_factory=dict_row, connect_timeout=8) as target:
        configure_session(source, settings, readonly=True)
        # These generated expressions are cheaper to interpret than to JIT
        # compile on large days; this changes only the reader's own session.
        source.execute('SET jit=off')
        configure_session(target, settings, readonly=not apply)
        source_identity = source.execute("SELECT current_database() AS name").fetchone()["name"]
        target_identity = target.execute("SELECT current_database() AS name").fetchone()["name"]
        if source_identity == target_identity:
            source_endpoint = source.execute('SELECT inet_server_addr()::text AS host, inet_server_port() AS port').fetchone()
            target_endpoint = target.execute('SELECT inet_server_addr()::text AS host, inet_server_port() AS port').fetchone()
            if source_endpoint == target_endpoint:
                raise RefreshError("Use separate source and target databases")
        source_profile = settings.profile or {
            'source_table': settings.source_table, 'columns': {field: field for field in SOURCE_COLUMNS}}
        types = column_types(source, source_profile)
        capabilities = source_capabilities(source_profile, types)
        if not settings.profile and types.get('ts') != 'timestamptz':
            raise ValueError('Для timestamp без часового пояса нужен подтверждённый профиль с source_timezone')
        normalized = source_cte(source_profile, types)
        if expected_schema_signature is not None and expected_schema_signature != capabilities['signature']:
            raise RefreshError('Структура источника изменилась после предварительного расчёта. Выполните новый предварительный расчёт; прежние агрегаты сохранены.')
        preflight(target, settings.hourly_table, HOURLY_COLUMNS, "timestamptz")
        preflight(target, settings.daily_table, DAILY_COLUMNS, "date")
        with target_lock(target, settings):
            automatic = start is None
            watermark = read_checkpoint(checkpoint_path, signature) if automatic else None
            begin, finish = plan_window(settings, now, start, end, watermark, apply)
            report({"event": "plan", "mode": "apply" if apply else "dry-run", "from": begin.isoformat(),
                    "to_exclusive": finish.isoformat(), "timezone": settings.timezone,
                    'algorithm_version': 3,
                    "energy": "fixed-interval estimate" if settings.sample_seconds is not None else "not calculated"})
            report({'event': 'schema', **capabilities,
                    'directory_signature': directory_signature(allowed_sensors) if allowed_sensors is not None else None})
            if end is None or finish.date() < end:
                report({'event': 'notice', 'detail': 'Сегодняшние и будущие незавершённые сутки исключены',
                        'to_exclusive': finish.isoformat()})
            if begin < finish:
                validate_required_rows(source, source_profile, normalized, begin, finish)
            completed = skipped = 0
            skipped_dates = set()
            if automatic and checkpoint_path.exists():
                skipped_dates.update(json.loads(checkpoint_path.read_text(encoding='utf-8')).get('skipped_dates', []))
            for day_start, day_end in day_windows(begin, finish, settings.timezone):
                if stop_requested and stop_requested():
                    raise RefreshInterrupted('Остановка на границе суток; задание будет возобновлено')
                try:
                    with source.transaction():
                        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                        if not settings.profile:
                            invalid = source.execute(sql.SQL('''WITH readings AS ({}) SELECT 1 FROM readings
                                WHERE sensor_name IS NULL OR BTRIM(sensor_name)='' LIMIT 1''').format(normalized),
                                {'start':day_start,'end':day_end}).fetchone()
                            if invalid:
                                raise DayQualityError('В выбранном дне есть строки без идентификатора счётчика')
                        read_start = day_start - timedelta(seconds=float(settings.sample_seconds or 0))
                        stats = source.execute(hourly_query(settings.source_table, normalized,
                            sample_seconds=settings.sample_seconds,
                            window_seconds=(settings.profile or {}).get('window_seconds', 10)),
                            {'start': read_start, 'end': day_end, 'timezone': settings.timezone}).fetchall()
                        # Carry the confirmed interval across midnight without
                        # importing the preceding day's measurement counts/means.
                        stats = [row for row in stats if day_start <= row['bucket_start'] < day_end]
                        if settings.profile:
                            mapping = settings.profile.get('sensor_map', {})
                            raw_names = {r['sensor_name'] for r in stats}
                            if len({mapping.get(n, n) for n in raw_names}) != len(raw_names):
                                raise DayQualityError('Sensor mappings merge distinct source meters')
                            for row in stats:
                                row['sensor_name'] = mapping.get(row['sensor_name'], row['sensor_name'])
                            quality = quality_report(source, normalized, settings, day_start, day_end,
                                stats, target, allowed_sensors,
                                power_available='active_power_w_avg' in capabilities['available_metrics'])
                            report({'event': 'quality', 'date': day_start.date().isoformat(), **quality})
                            if quality['errors']:
                                raise DayQualityError('; '.join(quality['errors']))
                    sensors = allowed_sensors if allowed_sensors is not None else {r['sensor_name'] for r in stats}
                    if not sensors:
                        raise DayQualityError('Expected meter list is empty; existing aggregates preserved')
                    stats = fill_missing_hours(stats, sensors, day_start, day_end)
                    hourly, daily = build_rows(stats, settings)
                    day = day_start.date()
                    if apply:
                        with target.transaction():
                            target.execute("SET LOCAL lock_timeout='5s'")
                            target.execute(window_query(settings.hourly_table), (day_start, day_end))
                            target.execute(window_query(settings.daily_table), (day, day+timedelta(days=1)))
                            with target.cursor() as cursor:
                                cursor.executemany(insert_query(settings.hourly_table, HOURLY_COLUMNS),
                                    [tuple(row[c] for c in HOURLY_COLUMNS) for row in hourly])
                                cursor.executemany(insert_query(settings.daily_table, DAILY_COLUMNS),
                                    [tuple(row[c] for c in DAILY_COLUMNS) for row in daily])
                    skipped_dates.discard(day.isoformat())
                    warnings = []
                    incomplete = sum(r['energy_kwh'] is None for r in daily)
                    if incomplete:
                        warnings.append(f'Полная энергия неизвестна у {incomplete} счётчиков; энергия по доступным измерениям сохранена отдельно')
                    report({'event': 'day', 'date': day.isoformat(), 'hourly_rows': len(hourly),
                        'daily_rows': len(daily), 'source_points': sum(r['points_count'] for r in hourly),
                        'partial_day': False, 'warnings': warnings, 'written': apply})
                    completed += 1
                except (DayQualityError, psycopg.DataError, psycopg.IntegrityError, psycopg.errors.QueryCanceled) as exc:
                    day = day_start.date()
                    skipped_dates.add(day.isoformat())
                    reason = str(exc) if isinstance(exc, DayQualityError) else f'Ошибка записи дня (SQLSTATE {exc.sqlstate}); прежние агрегаты сохранены'
                    report({'event': 'day_skipped', 'date': day.isoformat(), 'reason': reason,
                            'warnings': [reason], 'written': False})
                    skipped += 1
                if apply and automatic:
                    watermark = max(watermark, day_end) if watermark else day_end
                    save_checkpoint(checkpoint_path, signature, watermark, skipped_dates)
            report({'event': 'summary', 'completed_days': completed, 'skipped_days': skipped,
                    'warnings': ['Некоторые дни пропущены; причины сохранены в результатах дней'] if skipped else []})
            if skipped and not completed:
                raise RefreshError('Не удалось обработать ни одного дня: ' + '; '.join(
                    e['reason'] for e in events if e['event'] == 'day_skipped'))
    return events
