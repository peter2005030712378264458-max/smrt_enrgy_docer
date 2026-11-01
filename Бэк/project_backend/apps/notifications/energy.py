"""Read only the existing daily aggregates; never recompute or mutate them."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, localcontext
import re

from django.conf import settings
from django.db import connections
from psycopg import sql

BASELINE_DAYS = 30
MIN_BASELINE_DAYS = 7


def load_days(begin, end):
    parts = settings.ENERGY_DAILY_TABLE.split('.')
    if not 1 <= len(parts) <= 2 or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', part) for part in parts):
        raise ValueError('Invalid configured daily aggregate table')
    days = defaultdict(dict)
    with connections[settings.ENERGY_ANALYTICS_DB_ALIAS].cursor() as cursor:
        cursor.execute(sql.SQL('''SELECT bucket_start, sensor_name, energy_kwh,
            points_count, active_power_w_avg_count FROM {}
            WHERE bucket_start >= %s AND bucket_start < %s
            ORDER BY bucket_start,sensor_name''').format(sql.Identifier(*parts)), [begin, end])
        for day, meter, energy, points, valid in cursor.fetchall():
            # An unexpected duplicate makes that meter unverifiable.
            if meter in days[day]:
                days[day][meter] = None
            else:
                number = Decimal(energy) if energy is not None else None
                days[day][meter] = number if (number is not None and number.is_finite()
                    and points is not None and points > 0 and valid == points) else None
    return dict(days)


def evaluate(day, days, threshold):
    current = days.get(day, {})
    result = {'status': 'insufficient', 'energy_kwh': None, 'average_kwh': None,
              'deviation_percent': None, 'baseline_days': 0, 'baseline_dates': [],
              'meter_count': len(current), 'explanation': ''}
    if not current or any(value is None for value in current.values()):
        result['explanation'] = 'Нет полной подтверждённой энергии всех счётчиков за сутки.'
        return result
    with localcontext() as context:
        context.prec = 50
        result['energy_kwh'] = sum(current.values(), Decimal(0))
        baseline = []
        for offset in range(BASELINE_DAYS, 0, -1):
            previous_day = day - timedelta(days=offset)
            previous = days.get(previous_day, {})
            if previous.keys() == current.keys() and all(value is not None for value in previous.values()):
                baseline.append(sum(previous.values(), Decimal(0)))
                result['baseline_dates'].append(previous_day.isoformat())
        result['baseline_days'] = len(baseline)
        if len(baseline) < MIN_BASELINE_DAYS:
            result['explanation'] = 'Нужно не менее 7 полных суток с тем же составом счётчиков за предыдущие 30 дней.'
            return result
        average = sum(baseline, Decimal(0)) / len(baseline)
        result['average_kwh'] = average
        actual = result['energy_kwh']
        if average == 0:
            critical = actual != 0
            result['deviation_percent'] = Decimal(0) if actual == 0 else None
            result['explanation'] = 'Среднее равно нулю; процент для ненулевого потребления не определён.' if critical else ''
        else:
            deviation = (actual - average) / abs(average) * 100
            result['deviation_percent'] = deviation
            critical = abs(deviation) >= Decimal(threshold)
        result['status'] = 'critical' if critical else 'normal'
        return result
