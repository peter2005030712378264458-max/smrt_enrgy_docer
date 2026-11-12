"""Read-only device diagnostics. Independent of aggregate writers and their queue."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from decimal import Decimal
import json
import hashlib
import logging
import os
from statistics import median
import threading
import time as clock
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .mapping import column_types, source_capabilities, source_cte, NUMERIC_TYPES
from .model import table_parts
from .runner import fingerprint

ZONE = ZoneInfo('Europe/Moscow')
PERIODS = {'day': 1, 'week': 7, 'month': 30}
KINDS = {'электросчетчик': 'electricity', 'климатический датчик': 'climate', 'метеостанция': 'meteo'}
FIELDS = {
    'climate': ('co2', 'humidity', 'light', 'motion', 'temperature'),
    'meteo': ('baropressure', 'dewpoint', 'humidity', 'temperature', 'winddirection', 'windspeed'),
}
LOG = logging.getLogger('adapter.availability')


class DailyCache:
    """Share verified daily reads between 1/7/30-day reports, for at most 10 min."""
    def __init__(self):
        self.entries = {}
        self.lock = threading.Lock()

    def clear(self):
        with self.lock:
            self.entries.clear()

    def read(self, key, loader, reuse=True):
        now = clock.monotonic()
        with self.lock:
            self.entries = {k: v for k, v in self.entries.items() if now-v[0] < 600}
            cached = self.entries.get(key) if reuse else None
        if cached:
            return cached[1], cached[2], True
        rows = loader()
        checked_at = datetime.now(ZONE)
        with self.lock:
            if len(self.entries) >= 400:
                self.entries.pop(min(self.entries, key=lambda k: self.entries[k][0]))
            self.entries[key] = (clock.monotonic(), rows, checked_at)
        return rows, checked_at, False


DAY_STATS = DailyCache()


def bounds(period, now=None):
    if period not in PERIODS:
        raise ValueError('Период: day, week или month')
    end = datetime.combine((now or datetime.now(ZONE)).astimezone(ZONE).date(), time.min, ZONE)
    start = end - timedelta(days=PERIODS[period])
    return start, end, start - timedelta(days=30)


def _days(start, end):
    return [(start + timedelta(days=i)).date() for i in range((end-start).days)]


def classify(devices, statistics, start, end, failures=None):
    """Pure classifier; zero is excluded here, never in consumption aggregates."""
    failures = failures or {}
    history = _days(start-timedelta(days=30), start)
    current = _days(start, end)
    norms = {}
    for device in devices:
        series = statistics.get(device['sensor_name'], {})
        counts = [series.get(day, {}).get('valid', 0) for day in history]
        norm = median(counts)
        stable = norm > 0 and sum(n > 0 and abs(n-norm) <= norm * .2 for n in counts) >= 21
        cadences = [series[day]['cadence'] for day in history if series.get(day, {}).get('cadence')]
        norms[device['id']] = {'norm': norm, 'stable': stable,
                               'cadence': median(cadences) if cadences else None}
    rows = []
    for device in devices:
        kind = device['kind']
        row = {**device, 'records': None, 'valid_records': None, 'reference_records': None,
               'coverage_percent': None, 'last_valid_at': None, 'reference_devices': 0,
               'reference_method': None}
        if kind not in ('electricity', 'climate', 'meteo') or kind in failures:
            row.update(status='unknown', reason=failures.get(kind, 'Тип устройства не поддерживается'))
            rows.append(row)
            continue
        series = statistics.get(device['sensor_name'], {})
        observed = [series.get(day, {}) for day in current]
        count = sum(item.get('valid', 0) for item in observed)
        row.update(records=sum(item.get('records', 0) for item in observed), valid_records=count,
                   last_valid_at=max((item['last_valid'] for item in observed if item.get('last_valid')), default=None))
        own = norms[device['id']]
        peers = [norms[other['id']]['norm'] for other in devices
                 if other['model_id'] == device['model_id'] and other['kind'] == kind
                 and norms[other['id']]['stable'] and own['cadence']
                 and norms[other['id']]['cadence']
                 and abs(norms[other['id']]['cadence']-own['cadence']) <= own['cadence']*.2]
        daily = None
        if len(peers) >= 3:
            daily = median(peers)
            row.update(reference_method='peers', reference_devices=len(peers))
        elif own['stable']:
            daily = own['norm']
            row.update(reference_method='own_history', reference_devices=1)
        if daily:
            expected = Decimal(str(daily)) * len(current)
            row['reference_records'] = float(expected)
            row['coverage_percent'] = float(Decimal(count) / expected * 100)
        if count == 0:
            row.update(status='no_data', reason='Нет записей за период' if row['records'] == 0 else
                       'Записи есть, но измерения отсутствуют, равны 0 или некорректны')
        elif daily is None:
            row.update(status='unknown', reason='Эталон не определён: недостаточно стабильной предшествующей истории')
        elif Decimal(count) < expected * Decimal('.9'):
            row.update(status='low_data', reason='Менее 90% эталонного количества пригодных записей')
        else:
            row.update(status='ok', reason='Не менее 90% эталона')
        rows.append(row)
    order = {'no_data': 0, 'low_data': 1, 'unknown': 2, 'ok': 3}
    rows.sort(key=lambda r: (order[r['status']], r['coverage_percent'] if r['coverage_percent'] is not None else -1,
                             r['sensor_name'], str(r['id'])))
    summary = {status: sum(r['status'] == status for r in rows) for status in order}
    return rows, {'total': len(rows), **summary}


def stats_query(source, usable):
    # Each device/timestamp gets one vote; conflicting retransmissions can only
    # supply one usable reading. Cadence uses ALL unique timestamps, including NULL/0.
    return sql.SQL('''WITH source AS ({source}), unique_times AS (
        SELECT sensor_name,ts,BOOL_OR({usable}) AS usable FROM source
        GROUP BY sensor_name,ts
    ), ordered AS (
        SELECT *, (ts AT TIME ZONE 'Europe/Moscow')::date AS day,
          EXTRACT(EPOCH FROM ts-LAG(ts) OVER (
            PARTITION BY sensor_name,(ts AT TIME ZONE 'Europe/Moscow')::date ORDER BY ts)) AS step
        FROM unique_times
    ) SELECT sensor_name,day,COUNT(*) AS records,COUNT(*) FILTER(WHERE usable) AS valid,
        MAX(ts) FILTER(WHERE usable) AS last_valid,
        percentile_cont(.5) WITHIN GROUP(ORDER BY step) FILTER(WHERE step>0) AS cadence
      FROM ordered GROUP BY sensor_name,day''').format(source=source, usable=usable)


def finite_nonzero(field):
    return sql.SQL("({} IS NOT NULL AND {}::text NOT IN ('0','NaN','Infinity','-Infinity') AND {} <> 0)").format(
        sql.Identifier(field), sql.Identifier(field), sql.Identifier(field))


def environmental_source(conn, config):
    table = config['table']
    types = column_types(conn, {'source_table': table})
    if types.get('ts') not in ('timestamp', 'timestamptz') or 'sensor_name' not in types:
        raise ValueError('Недоступны обязательные столбцы источника')
    fields = [field for field in config['fields'] if field in types]
    if not fields:
        raise ValueError('В источнике нет измеряемых показателей')
    expressions = []
    for field in fields:
        if types[field] not in NUMERIC_TYPES and types[field] != 'bool':
            raise ValueError('Некорректный тип измеряемого показателя')
        expression = sql.Identifier(field)
        if types[field] == 'bool':
            expression = sql.SQL('{}::integer').format(expression)
        expressions.append(sql.SQL('{} AS {}').format(expression, sql.Identifier(field)))
    timestamp = sql.Identifier('ts')
    lo, hi = sql.SQL('%(start)s'), sql.SQL('%(end)s')
    if types['ts'] == 'timestamp':
        zone = sql.Literal(config.get('source_timezone', 'Europe/Moscow'))
        timestamp = sql.SQL('(ts AT TIME ZONE {})').format(zone)
        lo = sql.SQL('(%(start)s::timestamptz AT TIME ZONE {})').format(zone)
        hi = sql.SQL('(%(end)s::timestamptz AT TIME ZONE {})').format(zone)
    source = sql.SQL('SELECT {} AS ts,sensor_name::text,{} FROM {} WHERE ts >= {} AND ts < {}').format(
        timestamp, sql.SQL(',').join(expressions), sql.Identifier(*table_parts(table)), lo, hi)
    return source, sql.SQL(' OR ').join(finite_nonzero(field) for field in fields)


def read_report(controller, period, now=None, progress=None, reuse_daily=True):
    from .service import build_settings, directory_alias
    start, end, history_start = bounds(period, now)
    profile_id = os.environ.get('ENERGY_AVAILABILITY_PROFILE_ID')
    profile = controller.store.profile(profile_id) if profile_id else controller.store.query(
        "SELECT * FROM profiles WHERE active AND validated", one=True)
    if not profile or not profile['validated']:
        raise ValueError('Для отчёта задайте проверенный профиль источника; включать расписание не требуется')
    settings = build_settings(profile['document'], controller.registry)
    if profile['signature'] != fingerprint(settings):
        raise ValueError('Профиль требует проверки текущей версии алгоритма')
    document = settings.profile
    devices = []
    with psycopg.connect(controller.registry[directory_alias(document, controller.registry)],
                         autocommit=True, row_factory=dict_row, connect_timeout=8) as conn:
        conn.execute('SET default_transaction_read_only=on')
        conn.execute("SET statement_timeout='15s'")
        devices = conn.execute('''SELECT sd.id,sd.sensor_name,sd.device_listid AS model_id,dl.device_name AS model,
            dt.type AS device_type,r.room_number AS room
            FROM public.sensor_directory sd
            LEFT JOIN public.device_list dl ON dl.id=sd.device_listid
            LEFT JOIN public.device_type dt ON dt.id=dl.device_typeid
            LEFT JOIN structure.room r ON r.id=sd.roomid ORDER BY sd.id''').fetchall()
    if not devices or len({d['id'] for d in devices}) != len(devices) or len({d['sensor_name'] for d in devices}) != len(devices):
        raise ValueError('Справочник пуст или содержит неоднозначные идентификаторы')
    for device in devices:
        device['kind'] = KINDS.get((device['device_type'] or '').casefold().replace('ё', 'е'), 'unsupported')
    configs = {kind: {'table': 'public.'+name, 'fields': FIELDS[kind]}
               for kind, name in (('climate', 'climate_sensor_data'), ('meteo', 'meteo_sensor_data'))}
    # Server-only overrides for other deployments; browser cannot choose tables/DSNs.
    for kind, override in json.loads(os.environ.get('ENERGY_AVAILABILITY_SOURCES', '{}')).items():
        if kind not in configs or set(override)-{'table', 'connection', 'source_timezone'}:
            raise ValueError('Некорректная серверная конфигурация отчёта')
        configs[kind].update(override)
    statistics, failures = {}, {}
    started = datetime.now(ZONE)
    checked_times, reused_days, queried_days = [], 0, 0
    deadline = clock.monotonic() + 600
    for kind in ('electricity', 'climate', 'meteo'):
        if not any(d['kind'] == kind for d in devices):
            continue
        try:
            config = configs.get(kind, {})
            dsn = controller.registry[config['connection']] if config.get('connection') else settings.source_dsn
            with psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=8) as conn:
                conn.execute('SET default_transaction_read_only=on')
                conn.execute("SET statement_timeout='120s'")
                # Daily queries do not benefit from compiling the same expressions
                # repeatedly; the existing live reader uses the same session setting.
                conn.execute('SET jit=off')
                if kind == 'electricity':
                    types = column_types(conn, document)
                    caps = source_capabilities(document, types)
                    if 'active_power_w_avg' not in caps['available_metrics']:
                        raise ValueError('В источнике недоступна активная мощность')
                    source = source_cte(document, types)
                    clean = []
                    for field in ('pt', 'p1', 'p2', 'p3'):
                        clean.append(sql.SQL("CASE WHEN {f}::text IN ('NaN','Infinity','-Infinity') THEN NULL ELSE {f} END AS {f}").format(f=sql.Identifier(field)))
                    source = sql.SQL('SELECT ts,sensor_name,{} FROM ({}) readings').format(sql.SQL(',').join(clean), source)
                    from .queries import phase_total
                    usable = sql.SQL('('+phase_total('pt', ('p1', 'p2', 'p3'))+') <> 0')
                else:
                    source, usable = environmental_source(conn, config)
                query = stats_query(source, usable)
                cache_key = hashlib.sha256((dsn + profile['signature'] + query.as_string(conn)).encode()).hexdigest()
                # Bounded day queries avoid a single monthly sort of tens of millions
                # of readings. No source row leaves PostgreSQL, only daily statistics.
                seen_names = {}
                expected_names = {d['sensor_name'] for d in devices if d['kind'] == kind}
                for day in _days(history_start, end):
                    remaining = int(deadline - clock.monotonic())
                    if remaining <= 0:
                        raise ValueError('Истекло время проверки источника')
                    conn.execute(sql.SQL('SET statement_timeout={}').format(sql.Literal(f'{min(120, remaining)}s')))
                    begin = datetime.combine(day, time.min, ZONE)
                    daily, checked, reused = DAY_STATS.read((cache_key, day), lambda: conn.execute(
                        query, {'start': begin, 'end': begin+timedelta(days=1)}).fetchall(), reuse=reuse_daily)
                    checked_times.append(checked)
                    reused_days += int(reused)
                    queried_days += int(not reused)
                    for record in daily:
                        raw = record['sensor_name']
                        name = document.get('sensor_map', {}).get(raw, raw) if kind == 'electricity' else raw
                        if name not in expected_names:
                            continue
                        if name in seen_names and seen_names[name] != raw:
                            raise ValueError('Соответствия объединили разные устройства')
                        seen_names[name] = raw
                        statistics.setdefault(name, {})[record['day']] = record
                    if progress:
                        progress(kind, day)
        except (psycopg.Error, ValueError, KeyError):
            failures[kind] = 'Источник недоступен или его схема не соответствует настройкам; проверка не завершена'
            LOG.warning('Availability source check failed: kind=%s', kind)
    rows, summary = classify(devices, statistics, start, end, failures)
    return {'state': 'partial' if failures else 'ready', 'period': period,
            'from': start.isoformat(), 'to_exclusive': end.isoformat(),
            'history_from': history_start.isoformat(), 'history_to_exclusive': start.isoformat(),
            'started_at': started.isoformat(), 'checked_at': datetime.now(ZONE).isoformat(),
            'data_checked_from': min(checked_times).isoformat() if checked_times else None,
            'reused_days': reused_days, 'queried_days': queried_days,
            'profile_id': profile['id'], 'threshold_percent': 90, 'rows': rows,
            'summary': summary, 'source_errors': failures}


class ReportCache:
    """One bounded reader, at most three periods queued; never uses writer jobs."""
    def __init__(self, reader=read_report):
        self.reader = reader
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='availability')
        self.lock = threading.Lock()
        self.entries = {}

    def get(self, controller, period, refresh=False):
        start, end, _ = bounds(period)
        key = (controller.store.schema, end.date(), period, os.environ.get('ENERGY_AVAILABILITY_PROFILE_ID'))
        with self.lock:
            # Do not discard work still in progress on a Moscow midnight boundary.
            self.entries = {k: v for k, v in self.entries.items() if k[1] == end.date() or not v['future'].done()}
            entry = self.entries.get(key)
            if entry and entry['future'].done():
                age = clock.monotonic()-entry['completed']
                ttl = 30 if entry['future'].exception() else 600
                if age >= ttl or (refresh and age >= 30):
                    entry = None
            if entry is None:
                if sum(not v['future'].done() for v in self.entries.values()) >= 3:
                    return {'state': 'busy', 'detail': 'Проверка других периодов ещё выполняется. Повторите позже.'}
                future = self.pool.submit(self.reader, controller, period, reuse_daily=not refresh)
                entry = {'future': future, 'completed': clock.monotonic()}
                self.entries[key] = entry
                # Timestamp is set on completion, not enqueue (long scans are valid).
                def completed(_future):
                    entry['completed'] = clock.monotonic()
                future.add_done_callback(completed)
            future = entry['future']
            if not future.done():
                return {'state': 'pending', 'period': period, 'from': start.isoformat(),
                        'to_exclusive': end.isoformat(), 'poll_after_seconds': 3}
            try:
                return future.result()
            except Exception:
                LOG.warning('Availability report could not be completed')
                return {'state': 'error', 'detail': 'Не удалось проверить данные. Проверьте доступность источников и проверенный профиль отчёта.'}


REPORTS = ReportCache()
