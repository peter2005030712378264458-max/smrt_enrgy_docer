"""Read completed hours from a confirmed source without replacing aggregates."""
from datetime import datetime, time, timedelta
from decimal import Decimal
import os
import threading
import time as clock
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .mapping import column_types, source_capabilities, source_cte
from .queries import hourly_query
from .runner import fingerprint

_CACHE = {}
_LOCK = threading.Lock()


def live_hours(controller, now=None):
    now = now or datetime.now(ZoneInfo('Europe/Moscow'))
    local = now.astimezone(ZoneInfo('Europe/Moscow'))
    finish = local.replace(minute=0,second=0,microsecond=0)
    profile = controller.store.query("SELECT id,document,signature FROM profiles WHERE active AND validated",one=True)
    fallback = os.environ.get('ENERGY_NOTIFICATION_FALLBACK_SCHEMA')
    if profile is None and fallback:
        with controller.store.connection() as conn:
            profile = conn.execute(sql.SQL('SELECT id,document,signature FROM {}.profiles WHERE active AND validated').format(
                sql.Identifier(fallback))).fetchone()
    if not profile:
        raise ValueError('Для проверки текущего дня нужен активный проверенный профиль источника')
    from .service import build_settings
    settings = build_settings(profile['document'],controller.registry)
    if profile.get('signature') != fingerprint(settings):
        raise ValueError('Нужна новая версия профиля и проверка текущего алгоритма')
    key = (profile['id'], finish.isoformat())
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and clock.monotonic()-cached[0] < 300:
            return cached[1]
        document = profile['document']
        document = settings.profile
        if not finish.hour:
            return {'day':local.date().isoformat(),'closed_hours':0,'rows':[],'as_of':now.isoformat(),'profile_id':profile['id']}
        with psycopg.connect(settings.source_dsn,autocommit=True,row_factory=dict_row,connect_timeout=8) as conn:
            conn.execute('SET default_transaction_read_only=on')
            conn.execute("SET statement_timeout='15s'")
            conn.execute('SET jit=off')
            types = column_types(conn, document)
            caps = source_capabilities(document,types)
            if 'active_power_w_avg' not in caps['available_metrics']:
                raise ValueError('В профиле источника нет общей активной мощности')
            begin = datetime.combine(local.date(),time.min,local.tzinfo)
            rows = conn.execute(hourly_query(settings.source_table,source_cte(document,types),include_quality=True,
                sample_seconds=settings.sample_seconds, window_seconds=document.get('window_seconds',10)),
                {'start':begin-timedelta(seconds=float(settings.sample_seconds)),
                 'end':finish,'timezone':'Europe/Moscow'}).fetchall()
            rows = [row for row in rows if begin <= row['bucket_start'] < finish]
        output, seen = [], set()
        for row in rows:
            sensor = document.get('sensor_map',{}).get(row['sensor_name'],row['sensor_name'])
            hour = row['bucket_start'].astimezone(local.tzinfo).hour
            if (hour,sensor) in seen:
                raise ValueError('Соответствия ID объединили разные счётчики')
            seen.add((hour,sensor))
            power_seconds = row.get('power_seconds_sum')
            available = power_seconds/Decimal(3600000) if power_seconds is not None and row['points_count'] > 0 else None
            energy = available if row.get('power_seconds_count') == 3600 else None
            output.append({'hour':hour,'sensor_name':sensor,'energy_kwh':str(energy) if energy is not None else None,
                           'available_energy_kwh':str(available) if available is not None else None})
        result = {'day':local.date().isoformat(),'closed_hours':finish.hour,'rows':output,'as_of':now.isoformat(),'profile_id':profile['id']}
        _CACHE.clear()
        _CACHE[key] = (clock.monotonic(),result)
        return result
