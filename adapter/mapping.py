"""Explicit, versionable source contracts. Suggestions never activate a profile."""
from copy import deepcopy
from decimal import Decimal
from zoneinfo import ZoneInfo

from psycopg import sql

from .model import table_parts

FIELDS = ('ts', 'sensor_name', 'pt', 'p1', 'p2', 'p3', 'qt', 'q1', 'q2', 'q3',
          'st', 's1', 's2', 's3', 'i1', 'i2', 'i3', 'u1', 'u2', 'u3', 'frequency', 't')
ALIASES = {
    'ts': ('ts', 'timestamp', 'measured_at', 'datetime', 'measurement_time', 'время', 'дата'),
    'sensor_name': ('sensor_name', 'meter_id', 'device_id', 'meter_name', 'счетчик', 'счётчик'),
    'pt': ('pt', 'active_power', 'power_total', 'total_active_power', 'активная_мощность'),
    'qt': ('qt', 'reactive_power', 'total_reactive_power'),
    'st': ('st', 'apparent_power', 'total_apparent_power'),
    'frequency': ('frequency', 'frequency_hz', 'частота'),
    't': ('t', 'temperature', 'meter_temperature', 'температура'),
}
for prefix, names in (('p', ('power', 'active_power')), ('q', ('reactive_power',)),
                      ('s', ('apparent_power',)), ('i', ('current',)), ('u', ('voltage',))):
    for phase in range(1, 4):
        ALIASES[f'{prefix}{phase}'] = (f'{prefix}{phase}', *(f'{name}_l{phase}' for name in names),
                                     *(f'{name}_phase{phase}' for name in names))


def validate_profile(value):
    if not isinstance(value, dict):
        raise ValueError('Профиль должен быть JSON-объектом')
    value = deepcopy(value)
    allowed = {'name', 'connection', 'source_table', 'columns', 'scales', 'timezone', 'source_timezone',
               'sample_seconds', 'sensor_map', 'directory_connection', 'directory_table', 'directory_column',
               'confirmed', 'quality'}
    if set(value) - allowed:
        raise ValueError('Неизвестные настройки профиля')
    if value.get('confirmed') is not True:
        raise ValueError('Подтвердите соответствия, единицы и часовой пояс')
    table_parts(value['source_table'])
    table_parts(value.get('directory_table', 'public.sensor_directory'))
    ZoneInfo(value.get('timezone', 'Europe/Moscow'))
    # Existing daily dashboard semantics use Moscow calendar dates.
    if value.get('timezone', 'Europe/Moscow') != 'Europe/Moscow':
        raise ValueError('Выходные агрегаты приложения используют Europe/Moscow')
    ZoneInfo(value.get('source_timezone', 'Europe/Moscow'))
    columns = value.get('columns', {})
    if not isinstance(columns, dict) or set(columns) - set(FIELDS):
        raise ValueError('Неизвестное назначение столбца')
    for field, column in columns.items():
        if column is not None and (not isinstance(column, str) or not column or '\x00' in column):
            raise ValueError(f'Некорректный столбец {field}')
    if not columns.get('ts') or not columns.get('sensor_name'):
        raise ValueError('Нужны время и идентификатор счётчика')
    if not columns.get('pt') and not all(columns.get(f'p{i}') for i in (1, 2, 3)):
        raise ValueError('Нужна общая активная мощность или все три фазы; накопленная энергия пока не поддерживается')
    scales = value.get('scales', {})
    if not isinstance(scales, dict) or set(scales) - (set(FIELDS) - {'ts', 'sensor_name'}):
        raise ValueError('Некорректные единицы измерения')
    for factor in scales.values():
        number = Decimal(str(factor))
        if not number.is_finite() or number <= 0 or number > Decimal('1000000'):
            raise ValueError('Множитель единиц должен быть положительным конечным числом')
    sample = Decimal(str(value.get('sample_seconds', 0)))
    if not sample.is_finite() or sample <= 0 or sample > 86400:
        raise ValueError('Укажите подтверждённый интервал измерений в секундах')
    mapping = value.get('sensor_map', {})
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in mapping.items()):
        raise ValueError('sensor_map должен связывать исходные ID с именами справочника')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('Несколько исходных ID нельзя объединять в один счётчик')
    quality = value.setdefault('quality', {})
    defaults = {'min_coverage': 0.9, 'max_duplicate_fraction': 0.001, 'interval_tolerance': 0.2,
                'min_interval_match': 0.9, 'min_existing_points_ratio': 0.8}
    if not isinstance(quality, dict) or set(quality) - set(defaults):
        raise ValueError('Неизвестные пороги качества')
    for key, default in defaults.items():
        quality.setdefault(key, default)
        if not isinstance(quality[key], (int, float)) or not 0 <= quality[key] <= 1:
            raise ValueError(f'Порог {key} должен быть от 0 до 1')
    value.setdefault('name', value['source_table'])
    for key in ('name', 'connection', 'directory_column'):
        if key in value and (not isinstance(value[key], str) or not value[key] or '\x00' in value[key]):
            raise ValueError(f'Некорректная настройка {key}')
    return value


def inspect_source(connection, table_name):
    schema, name = table_parts(table_name)
    columns = connection.execute('''
        SELECT a.attname AS name, t.typname AS type, col_description(c.oid,a.attnum) AS comment
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid JOIN pg_type t ON t.oid=a.atttypid
        WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY a.attnum
    ''', (schema, name)).fetchall()
    if not columns:
        raise ValueError('Таблица не найдена или не содержит столбцов')
    suggestions = {}
    for field in FIELDS:
        matches = [c['name'] for c in columns if c['name'].casefold() in ALIASES[field]]
        suggestions[field] = {'column': matches[0] if len(matches) == 1 else None,
                              'candidates': matches, 'needs_confirmation': True}
    sample = connection.execute(sql.SQL('SELECT * FROM {} LIMIT 5').format(sql.Identifier(schema, name))).fetchall()
    return {'columns': columns, 'suggestions': suggestions, 'sample': sample,
            'notice': 'Единицы и семантика не определяются надёжно по названию. Подтвердите профиль.'}


def source_cte(profile, column_types):
    columns = profile['columns']
    if any(c and c not in column_types for c in columns.values()):
        raise ValueError('Структура источника изменилась: столбец из профиля отсутствует')
    timestamp = sql.Identifier(columns['ts'])
    timestamp_type = column_types[columns['ts']]
    if timestamp_type == 'timestamp':
        timestamp = sql.SQL('({} AT TIME ZONE {})').format(timestamp, sql.Literal(profile.get('source_timezone', 'Europe/Moscow')))
    elif timestamp_type != 'timestamptz':
        raise ValueError('Столбец времени должен иметь тип timestamp или timestamptz')
    expressions = []
    numeric_types = {'int2', 'int4', 'int8', 'numeric', 'float4', 'float8'}
    for field in FIELDS:
        column = columns.get(field)
        if field == 'ts':
            expression = timestamp
        elif field == 'sensor_name':
            expression = sql.SQL('{}::text').format(sql.Identifier(column))
        elif column:
            if column_types[column] not in numeric_types:
                raise ValueError(f'{field}: ожидается числовой тип, получен {column_types[column]}')
            expression = sql.SQL('({}::numeric * {})').format(sql.Identifier(column), sql.Literal(Decimal(str(profile.get('scales', {}).get(field, 1)))))
        else:
            expression = sql.SQL('NULL::numeric')
        expressions.append(sql.SQL('{} AS {}').format(expression, sql.Identifier(field)))
    return sql.SQL('SELECT {} FROM {} WHERE {} >= %(start)s AND {} < %(end)s').format(
        sql.SQL(', ').join(expressions), sql.Identifier(*table_parts(profile['source_table'])), timestamp, timestamp)


def column_types(connection, profile):
    return {c['name']: c['type'] for c in inspect_source(connection, profile['source_table'])['columns']}
