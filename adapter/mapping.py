from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from zoneinfo import ZoneInfo

import psycopg
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

NUMERIC_TYPES = {'int2', 'int4', 'int8', 'numeric', 'float4', 'float8'}
TIME_TYPES = {'timestamp', 'timestamptz'}
METRIC_INPUTS = {
    'active_power_w_avg': (('pt',), ('p1', 'p2', 'p3')),
    'phase1_power_w_avg': (('p1',),), 'phase2_power_w_avg': (('p2',),),
    'phase3_power_w_avg': (('p3',),),
    'reactive_power_var_avg': (('qt',), ('q1', 'q2', 'q3')),
    'apparent_power_va_avg': (('st',), ('s1', 's2', 's3')),
    'current_avg_a': (('i1',), ('i2',), ('i3',)),
    'voltage_avg_v': (('u1',), ('u2',), ('u3',)),
    'frequency_hz_avg': (('frequency',),), 'meter_temperature_avg': (('t',),),
}

# валидация при сохранении и запуске задания по профилю  
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

# вычленение колонок из таблицы с показаниями 
def _source_columns(connection, table_name):
    schema, name = table_parts(table_name)
    columns = connection.execute('''
        SELECT a.attname AS name, COALESCE(base.typname,t.typname) AS type,
               format_type(a.atttypid,a.atttypmod) AS declared_type,
               col_description(c.oid,a.attnum) AS comment,
               NOT a.attnotnull AS nullable,
               (has_schema_privilege(n.oid,'USAGE') AND has_column_privilege(c.oid,a.attnum,'SELECT')) AS readable
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid JOIN pg_type t ON t.oid=a.atttypid
        LEFT JOIN pg_type base ON t.typtype='d' AND base.oid=t.typbasetype
        WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped
          AND c.relkind IN ('r','p','v','m','f')
        ORDER BY a.attnum
    ''', (schema, name)).fetchall()
    if not columns:
        raise ValueError('Таблица или представление не найдены либо не содержат столбцов')
    return columns

# возвращаем колонки таблицы + предложения по заполнению 
def inspect_source(connection, table_name):
    schema, name = table_parts(table_name)
    # вычленяем колонки 
    columns = _source_columns(connection, table_name)
    suggestions = {}
    for field in FIELDS:
        matches = [c['name'] for c in columns if c['name'].casefold() in ALIASES[field]
                   and c['readable'] and (field == 'sensor_name' or c['type'] in
                                         (TIME_TYPES if field == 'ts' else NUMERIC_TYPES))]
        suggestions[field] = {'column': matches[0] if len(matches) == 1 else None,
                              'candidates': matches, 'needs_confirmation': True}
    order = sql.SQL('')
    timestamp = suggestions['ts']['column']
    if any(c['name'] == timestamp and c['type'] in ('timestamp', 'timestamptz') for c in columns):
        indexed = connection.execute('''
            SELECT EXISTS (
                SELECT 1 FROM pg_index i
                JOIN pg_class c ON c.oid=i.indrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_class idx ON idx.oid=i.indexrelid
                JOIN pg_am am ON am.oid=idx.relam
                JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=i.indkey[0]
                WHERE n.nspname=%s AND c.relname=%s AND a.attname=%s
                  AND am.amname='btree' AND i.indisvalid AND i.indisready
                  AND i.indpred IS NULL
            ) AS available
        ''', (schema, name, timestamp)).fetchone()['available']
        if indexed:
            # An unordered scan can spend the entire timeout on empty heap pages.
            # Only order by time when a leading B-tree key can avoid a full sort.
            order = sql.SQL(' ORDER BY {} DESC').format(sql.Identifier(timestamp))
    readable = [sql.Identifier(c['name']) for c in columns if c['readable']]
    sample, warnings = [], []
    if readable:
        try:
            # A savepoint keeps metadata usable after a sample timeout, including
            # when the caller already has an open read-only transaction.
            with connection.transaction():
                sample = connection.execute(sql.SQL('SELECT {} FROM {}{} LIMIT 5').format(
                    sql.SQL(', ').join(readable), sql.Identifier(schema, name), order)).fetchall()
        except (psycopg.errors.QueryCanceled, psycopg.errors.InsufficientPrivilege):
            warnings.append('Столбцы получены, но пример строк недоступен: превышено время чтения или недостаточно прав. Соответствия можно настроить по структуре.')
    else:
        warnings.append('Нет прав SELECT на столбцы источника. Структура показана, но расчёт требует доступа к времени, счётчику и выбранным измерениям.')
    
    return {
        'columns': columns, 
        'suggestions': suggestions, 
        'sample': sample,
        'warnings': warnings,
        'notice': 'Единицы и семантика не определяются надёжно по названию. Подтвердите профиль.'
    }

#сверяем назначения профиля с реальными доступными столбцами и их типами, определяя, какие показатели можно рассчитать
def source_capabilities(profile, types):
    columns = profile['columns']
    for field, label in (('ts', 'время'), ('sensor_name', 'идентификатор счётчика')):
        if not columns.get(field) or columns[field] not in types:
            raise ValueError(f'Обязательное поле «{label}»: столбец отсутствует или недоступен. Расчёт остановлен; прежние агрегаты сохранены.')
    if types[columns['ts']] not in TIME_TYPES:
        raise ValueError('Столбец времени должен иметь тип timestamp или timestamptz. Дата не подставляется автоматически.')
    for field, column in columns.items():
        if field not in ('ts', 'sensor_name') and column in types and types[column] not in NUMERIC_TYPES:
            raise ValueError(f'{field} ({column}): ожидается числовой тип, получен {types[column]}')
    fields = [field for field in FIELDS if columns.get(field) in types]
    metrics = [metric for metric, alternatives in METRIC_INPUTS.items()
               if any(all(field in fields for field in group) for group in alternatives)]
    missing = [{'field': field, 'column': column} for field, column in columns.items()
               if column and column not in types]
    warnings = []
    if missing:
        warnings.append('Назначенные столбцы отсутствуют или недоступны: '
                        + ', '.join(f"{item['field']} → {item['column']}" for item in missing)
                        + '. Их значения сохраняются как NULL; остальные показатели рассчитываются.')
    if 'active_power_w_avg' not in metrics:
        warnings.append('Общая активная мощность и энергия недоступны: нужны pt или все три фазы p1, p2, p3. Доступные измерения и число показаний будут сохранены, энергия останется NULL.')
    signature = hashlib.sha256(json.dumps(
        {field: [columns.get(field), types.get(columns.get(field))] for field in FIELDS},
        sort_keys=True).encode()).hexdigest()
    return {'signature': signature, 'available_fields': fields, 'missing_columns': missing,
            'available_metrics': metrics,
            'unavailable_metrics': [metric for metric in METRIC_INPUTS if metric not in metrics],
            'warnings': warnings}


def source_cte(profile, column_types):
    source_capabilities(profile, column_types)
    columns = profile['columns']
    timestamp = sql.Identifier(columns['ts'])
    start = sql.SQL('%(start)s')
    end = sql.SQL('%(end)s')
    timestamp_type = column_types[columns['ts']]
    if timestamp_type == 'timestamp':
        source_zone = sql.Literal(profile.get('source_timezone', 'Europe/Moscow'))
        timestamp = sql.SQL('({} AT TIME ZONE {})').format(timestamp, source_zone)
        start = sql.SQL('(%(start)s::timestamptz AT TIME ZONE {})').format(source_zone)
        end = sql.SQL('(%(end)s::timestamptz AT TIME ZONE {})').format(source_zone)
    elif timestamp_type != 'timestamptz':
        raise ValueError('Столбец времени должен иметь тип timestamp или timestamptz')
    expressions = []
    for field in FIELDS:
        column = columns.get(field)
        if field == 'ts':
            expression = timestamp
        elif field == 'sensor_name':
            expression = sql.SQL('{}::text').format(sql.Identifier(column))
        elif column and column in column_types:
            identifier = sql.Identifier(column)
            factor = sql.Literal(Decimal(str(profile.get('scales', {}).get(field, 1))))
            expression = sql.SQL('{}::numeric * {}').format(identifier, factor)
            # PostgreSQL 13 cannot cast float Infinity to numeric. Reject it
            # before the cast, rather than letting one bad measurement abort
            # the other available metrics. Numeric/integer inputs are cleaned
            # once downstream, avoiding duplicate casts across every metric.
            if column_types[column] in ('float4', 'float8'):
                expression = sql.SQL("CASE WHEN {}::text IN ('NaN','Infinity','-Infinity') THEN NULL ELSE {} END").format(
                    identifier, expression)
        else:
            expression = sql.SQL('NULL::numeric')
        expressions.append(sql.SQL('{} AS {}').format(expression, sql.Identifier(field)))
    return sql.SQL('SELECT {} FROM {} WHERE {} >= {} AND {} < {}').format(
        sql.SQL(', ').join(expressions), sql.Identifier(*table_parts(profile['source_table'])),
        sql.Identifier(columns['ts']), start, sql.Identifier(columns['ts']), end)

# получаем актуальные типы читаемых столбцов
def column_types(connection, profile):
    return {c['name']: c['type'] for c in _source_columns(connection, profile['source_table']) if c['readable']}
