"""Read-only hourly data for curve analysis."""
from datetime import datetime, time, timedelta
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connections
from psycopg import sql

from apps.aggregation.client import request_adapter

ZONE = ZoneInfo('Europe/Moscow')


def hourly_table():
    parts = settings.ENERGY_HOURLY_TABLE.split('.')
    if not 1 <= len(parts) <= 2 or any(not part or not part.replace('_','a').isalnum() for part in parts):
        raise ValueError('Некорректная таблица агрегатов')
    return sql.Identifier(*parts)


def available_dates():
    with connections[settings.ENERGY_ANALYTICS_DB_ALIAS].cursor() as cursor:
        cursor.execute(sql.SQL('SELECT MIN(bucket_start),MAX(bucket_start) FROM {} WHERE bucket_start < %s').format(hourly_table()),
                       [datetime.now(ZONE).replace(hour=0,minute=0,second=0,microsecond=0)])
        first,last = cursor.fetchone()
    return {'first_day':first.astimezone(ZONE).date() if first else None,
            'last_day':last.astimezone(ZONE).date() if last else None}


def load_hours(begin, end):
    if begin >= end:
        return {}
    result = {}
    with connections[settings.ENERGY_ANALYTICS_DB_ALIAS].cursor() as cursor:
        cursor.execute(sql.SQL('SELECT bucket_start,sensor_name,energy_kwh,points_count,active_power_w_avg_count FROM {} '
            'WHERE bucket_start >= %s AND bucket_start < %s ORDER BY bucket_start,sensor_name').format(hourly_table()),
            [datetime.combine(begin,time.min,ZONE),datetime.combine(end,time.min,ZONE)])
        for bucket,meter,energy,points,valid in cursor.fetchall():
            local = bucket.astimezone(ZONE)
            values = result.setdefault(local.date(),{}).setdefault(local.hour,{})
            number = Decimal(energy) if energy is not None else None
            if meter in values:
                values[meter] = None
            else:
                values[meter] = number if number is not None and number.is_finite() and points and points>0 and valid==points else None
    return result


def load_live_hours():
    data,status = request_adapter('GET','live-hours')
    if status != 200:
        raise ValueError(data.get('detail','Оперативный источник недоступен'))
    from datetime import date
    day = date.fromisoformat(data['day'])
    hours = {day:{}}
    for row in data['rows']:
        values = hours[day].setdefault(row['hour'],{})
        values[row['sensor_name']] = Decimal(str(row['energy_kwh'])) if row['energy_kwh'] is not None else None
    return hours, data
