"""Pure, deterministic hourly reference curves. No SQL, clocks or mail here."""
from datetime import date, timedelta
from decimal import Decimal, localcontext, DecimalException
from statistics import median
import hashlib
import json

DEFAULT_RULE = {'method': 'mean', 'window_mode': 'last_n', 'history_days': 7,
    'date_from': None, 'date_to': None, 'day_group': 'day_type', 'minimum_days': 1,
    'mode': 'daily', 'absolute_threshold': '0'}


def normalize_rule(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_RULE):
        raise ValueError('Неизвестные настройки кривой')
    rule = {**DEFAULT_RULE, **value}
    for key, choices in {'method': ('mean','median','linear'), 'window_mode': ('last_n','range'),
                         'day_group': ('day_type','weekday','all'), 'mode': ('daily','live')}.items():
        if rule[key] not in choices:
            raise ValueError('Некорректная настройка: ' + key)
    for key, maximum in (('history_days',60),('minimum_days',60)):
        if type(rule[key]) is not int or not 1 <= rule[key] <= maximum:
            raise ValueError('N и минимум дней должны быть целыми числами от 1 до 60')
    if rule['minimum_days'] > rule['history_days'] and rule['window_mode'] == 'last_n':
        raise ValueError('Минимум дней не может превышать N')
    try:
        absolute = Decimal(str(rule['absolute_threshold']))
        if not absolute.is_finite() or not 0 <= absolute <= Decimal('1000000000'):
            raise ValueError()
    except (DecimalException, TypeError, ValueError):
        raise ValueError('Абсолютный порог должен быть конечным числом от 0 до 1000000000 кВт·ч') from None
    rule['absolute_threshold'] = str(absolute)
    if rule['window_mode'] == 'range':
        try:
            begin, end = date.fromisoformat(rule['date_from']), date.fromisoformat(rule['date_to'])
        except (ValueError, TypeError):
            raise ValueError('Укажите начало и конец диапазона') from None
        if begin > end or (end-begin).days >= 366:
            raise ValueError('Диапазон должен содержать от 1 до 366 дней')
        if rule['minimum_days'] > (end-begin).days+1:
            raise ValueError('В диапазоне меньше дней, чем требуется для расчёта')
    else:
        rule['date_from'] = rule['date_to'] = None
    return rule


def rule_key(rule, threshold):
    return hashlib.sha256(json.dumps({'rule':normalize_rule(rule),'threshold':str(Decimal(str(threshold)).normalize())},
        sort_keys=True).encode()).hexdigest()


def history_bounds(day, rule):
    rule = normalize_rule(rule)
    if rule['window_mode'] == 'range':
        return date.fromisoformat(rule['date_from']), min(day, date.fromisoformat(rule['date_to'])+timedelta(days=1))
    return day-timedelta(days=rule['history_days']*7+7), day


def same_group(left, right, group):
    if group == 'all':
        return True
    if group == 'weekday':
        return left.weekday() == right.weekday()
    return (left.weekday() >= 5) == (right.weekday() >= 5)


def complete_total(values, scope):
    if not scope or set(values) != scope or any(v is None or not v.is_finite() for v in values.values()):
        return None
    return sum(values.values(), Decimal(0))


def fitted_value(samples, day, method):
    values = [value for _, value in samples]
    if method == 'median':
        return Decimal(median(values)), None
    if method == 'mean':
        return sum(values)/len(values), None
    # Centre on the target date: intercept is f(target), slope is kWh/day.
    xs = [Decimal((sample_day-day).days) for sample_day,_ in samples]
    xm, ym = sum(xs)/len(xs), sum(values)/len(values)
    denominator = sum((x-xm)**2 for x in xs)
    if not denominator:
        return None, None
    slope = sum((x-xm)*(y-ym) for x,y in zip(xs, values))/denominator
    return ym-slope*xm, slope


def evaluate_curve(day, hours, rule, threshold, closed_hours=24):
    rule = normalize_rule(rule)
    try:
        threshold = Decimal(str(threshold))
    except (DecimalException, TypeError, ValueError):
        raise ValueError('Некорректный процентный порог') from None
    if not threshold.is_finite() or not Decimal('0.01') <= threshold <= 10000:
        raise ValueError('Процентный порог должен быть от 0.01 до 10000')
    if type(closed_hours) is not int or not 0 <= closed_hours <= 24:
        raise ValueError('Некорректное число завершённых часов')
    begin, finish = history_bounds(day, rule)
    candidates = sorted((d for d in hours if begin <= d < finish and same_group(d,day,rule['day_group'])), reverse=True)
    if rule['window_mode'] == 'last_n':
        candidates = candidates[:rule['history_days']]
    actual_hours = hours.get(day,{})
    scope = {meter for values in actual_hours.values() for meter in values}
    if not scope and candidates:
        scope = {meter for values in hours[candidates[0]].values() for meter in values}
    points, training = [], {}
    with localcontext() as context:
        context.prec = 50
        for hour in range(24):
            samples = []
            for previous in candidates:
                value = complete_total(hours[previous].get(hour,{}), scope)
                training.setdefault(previous.isoformat(),[]).append(value)
                if value is not None:
                    samples.append((previous,value))
            expected, slope = (None,None)
            minimum = max(rule['minimum_days'], 2 if rule['method']=='linear' else 1)
            if len(samples) >= minimum:
                expected, slope = fitted_value(samples,day,rule['method'])
            actual = complete_total(actual_hours.get(hour,{}),scope) if hour < closed_hours else None
            deviation, anomaly, tolerance = None, False, None
            if expected is not None:
                tolerance = max(abs(expected)*threshold/100, Decimal(rule['absolute_threshold']))
                if actual is not None:
                    difference = actual-expected
                    deviation = difference/abs(expected)*100 if expected else (Decimal(0) if actual == 0 else None)
                    anomaly = abs(difference) > 0 and abs(difference) >= tolerance
            reason = ('Час ещё не завершён' if hour >= closed_hours else
                      'Нет полных показаний тех же счётчиков' if actual is None else
                      'Недостаточно сопоставимых дней для функции' if expected is None else '')
            points.append({'hour':hour,'time':f'{hour:02}:00','actual_kwh':actual,
                'expected_kwh':expected,'lower':expected-tolerance if expected is not None else None,
                'upper':expected+tolerance if expected is not None else None,
                'sample_count':len(samples),'deviation_percent':deviation,'anomaly':anomaly,
                'reason':reason,'slope_kwh_per_day':slope})
        checked = [p for p in points[:closed_hours] if p['actual_kwh'] is not None and p['expected_kwh'] is not None]
        alerts = [p for p in checked if p['anomaly']]
        status = 'critical' if alerts else 'normal' if closed_hours and len(checked)==closed_hours else 'insufficient'
        actual_total = sum(p['actual_kwh'] for p in points[:closed_hours]) if closed_hours and all(p['actual_kwh'] is not None for p in points[:closed_hours]) else None
        expected_total = sum(p['expected_kwh'] for p in points[:closed_hours]) if closed_hours and all(p['expected_kwh'] is not None for p in points[:closed_hours]) else None
        peak = max(checked,key=lambda p:abs(p['deviation_percent']) if p['deviation_percent'] is not None else Decimal('Infinity')) if checked else None
        return {'status':status,'energy_kwh':actual_total,'average_kwh':expected_total,
            'deviation_percent':peak['deviation_percent'] if peak else None,
            'baseline_days':min((p['sample_count'] for p in points[:closed_hours]),default=0),
            'baseline_dates':[d.isoformat() for d in sorted(candidates)],'meter_count':len(scope),
            'explanation':f'Проверено часов: {len(checked)} из {closed_hours}. Превышений: {len(alerts)}.',
            'curve':points,'reference_days':[{'day':d,'hours':values} for d,values in sorted(training.items())],
            'closed_hours':closed_hours,'checked_hours':len(checked),'anomaly_hours':[p['hour'] for p in alerts],
            'rule':rule,'day':day.isoformat()}
