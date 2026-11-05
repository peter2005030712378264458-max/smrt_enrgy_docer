from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .curve import evaluate_curve, history_bounds, normalize_rule
from .hourly import load_hours, load_live_hours

ZONE = ZoneInfo('Europe/Moscow')


def preview_curve(day, rule, threshold, now=None):
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    rule = normalize_rule(rule)
    if day > now.date():
        raise ValueError('Будущие дни нельзя проверять')
    if rule['mode']=='daily' and day >= now.date():
        raise ValueError('Выберите завершённые сутки или режим проверки в течение дня')
    if rule['mode']=='live' and day != now.date():
        raise ValueError('Оперативный режим проверяет текущий день')
    begin, finish = history_bounds(day, rule)
    hours = load_hours(begin,finish) if begin < finish else {}
    source = {}
    if rule['mode']=='live':
        current, source = load_live_hours()
        if day not in current or source['closed_hours'] != now.hour:
            raise ValueError('Изменилась граница часа. Повторите проверку')
        hours.update(current)
        closed = source['closed_hours']
    else:
        hours.update(load_hours(day,day+timedelta(days=1)))
        closed = 24
    result = evaluate_curve(day,hours,rule,threshold,closed)
    result['as_of'] = source.get('as_of',now.isoformat())
    result['threshold_percent'] = str(threshold)
    return result
