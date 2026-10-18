"""Private control API; a durable queue is consumed by one elected worker."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
import math
import os
import signal
import threading
from urllib.parse import urlparse, parse_qs
import uuid
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .mapping import inspect_source, validate_profile
from .model import Settings, table_parts
from .runner import refresh, RefreshError, RefreshInterrupted, fingerprint
from .store import Store

LOG = logging.getLogger('adapter')


def serial(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else str(value)
    return str(value)


def clean_json(value):
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return json.loads(json.dumps(value, default=serial, allow_nan=False))


def connections():
    # Only server-side aliases, never accept connection strings over HTTP.
    registry = json.loads(os.environ.get('ENERGY_ADAPTER_CONNECTIONS', '{}'))
    if os.environ.get('ENERGY_AGG_SOURCE_DSN'):
        registry.setdefault('paradigm', os.environ['ENERGY_AGG_SOURCE_DSN'])
    directory_dsn = os.environ.get('ENERGY_AGG_DIRECTORY_DSN')
    if directory_dsn:
        if 'directory' in registry and registry['directory'] != directory_dsn:
            raise ValueError('Псевдоним directory уже задан с другим подключением')
        registry['directory'] = directory_dsn
    if not registry or any(not isinstance(v, str) for v in registry.values()):
        raise ValueError('Настройте серверные подключения ENERGY_ADAPTER_CONNECTIONS')
    return registry


def directory_alias(document, registry):
    """Explicit profile choice wins; otherwise use the dedicated metadata reader."""
    alias = document.get('directory_connection') or (
        'directory' if 'directory' in registry else document['connection'])
    if alias not in registry:
        raise ValueError('Неизвестное подключение справочника')
    return alias


def build_settings(document, registry):
    profile = validate_profile(document)
    if profile.get('connection') not in registry or profile.get('connection') == 'directory':
        raise ValueError('Неизвестное серверное подключение')
    directory_alias(profile, registry)
    return Settings(registry[profile['connection']], os.environ['ENERGY_AGG_TARGET_DSN'],
                    source_table=profile['source_table'], profile=profile,
                    sample_seconds=Decimal(str(profile['sample_seconds'])), timezone=profile.get('timezone', 'Europe/Moscow'),
                    lookback_days=0, statement_timeout_seconds=int(os.environ.get('ENERGY_ADAPTER_STATEMENT_TIMEOUT', '120')))


def directory_names(document, registry):
    dsn = registry[directory_alias(document, registry)]
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=8) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='15s'")
        return {str(row['name']) for row in conn.execute(sql.SQL('SELECT {}::text AS name FROM {}').format(
            sql.Identifier(document.get('directory_column', 'sensor_name')),
            sql.Identifier(*table_parts(document.get('directory_table', 'public.sensor_directory'))))).fetchall()}


def schedule_ranges(profile, now, repeat_minutes):
    today = now.astimezone(ZoneInfo(profile['document'].get('timezone', 'Europe/Moscow'))).date()
    begin = profile['processed_until'] or today-timedelta(days=1)
    if begin < today:
        yield begin, today, f"{profile['id']}:{today}:daily"
    local = now.astimezone(ZoneInfo('Europe/Moscow'))
    if repeat_minutes and local.hour*60+local.minute >= repeat_minutes:
        yield today-timedelta(days=1), today, f"{profile['id']}:{today}:repeat"


class Controller:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry

    def dispatch(self, method, path, body):
        parts = urlparse(path)
        route = parts.path.strip('/').split('/')
        if method == 'GET' and route == ['health']:
            self.store.query('SELECT id FROM adapter_control.settings WHERE id=1')
            return {'status': 'ok'}
        if method == 'GET' and route == ['connections']:
            return sorted(alias for alias in self.registry if alias != 'directory')
        if method == 'GET' and route == ['tables']:
            alias = parse_qs(parts.query).get('connection', [''])[0]
            if alias not in self.registry or alias == 'directory':
                raise ValueError('Неизвестное подключение')
            with psycopg.connect(self.registry[alias], autocommit=True, row_factory=dict_row, connect_timeout=8) as conn:
                conn.execute("SET default_transaction_read_only=on")
                conn.execute("SET statement_timeout='10s'")
                return conn.execute('''SELECT table_schema || '.' || table_name AS name
                    FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')
                    AND table_type IN ('BASE TABLE','VIEW','FOREIGN') ORDER BY 1 LIMIT 500''').fetchall()
        if method == 'GET' and route == ['profiles']:
            return self.store.query("SELECT * FROM adapter_control.profiles WHERE document ? 'columns' ORDER BY created_at DESC LIMIT 100")
        if method == 'POST' and route == ['profiles']:
            document = validate_profile(body)
            signature = fingerprint(build_settings(document, self.registry))
            return self.store.query('INSERT INTO adapter_control.profiles(id,document,signature) VALUES(%s,%s,%s) RETURNING *',
                                    (str(uuid.uuid4()), Jsonb(document), signature), one=True)
        if method == 'POST' and len(route) == 3 and route[0] == 'profiles' and route[2] == 'activate':
            profile = self.store.profile(route[1])
            if not profile['validated']:
                raise ValueError('Сначала выполните успешный предварительный расчёт этого профиля')
            with self.store.connection() as conn:
                conn.execute('SELECT * FROM adapter_control.settings WHERE id=1 FOR UPDATE')
                conn.execute('UPDATE adapter_control.profiles SET active=false WHERE active')
                conn.execute('UPDATE adapter_control.profiles SET active=true WHERE id=%s', (route[1],))
            return self.store.profile(route[1])
        if route == ['schedule']:
            if method == 'GET':
                state = self.store.query('SELECT * FROM adapter_control.settings WHERE id=1', one=True)
                profile = self.store.query('SELECT * FROM adapter_control.profiles WHERE active', one=True)
                state['active_profile'] = profile
                state['next_run'] = (datetime.combine(datetime.now(ZoneInfo('Europe/Moscow')).date()+timedelta(days=1),
                    datetime.min.time(), ZoneInfo('Europe/Moscow')).isoformat()) if state['enabled'] else None
                return state
            if method == 'POST':
                if set(body) - {'enabled', 'repeat_minutes'}:
                    raise ValueError('Неизвестные настройки расписания')
                for key in ('enabled',):
                    if key in body and not isinstance(body[key], bool):
                        raise ValueError('Ожидается логическое значение')
                if 'repeat_minutes' in body and (type(body['repeat_minutes']) is not int or not 0 <= body['repeat_minutes'] <= 1439):
                    raise ValueError('Повтор: от 0 до 1439 минут после полуночи')
                if body.get('enabled') and not self.store.query('SELECT id FROM adapter_control.profiles WHERE active AND validated'):
                    raise ValueError('Нет проверенного активного профиля')
                for key, value in body.items():
                    self.store.query(sql.SQL('UPDATE adapter_control.settings SET {}=%s WHERE id=1').format(sql.Identifier(key)), (value,))
                return self.dispatch('GET', '/schedule', {})
        if method == 'GET' and route == ['jobs']:
            return self.store.query('SELECT * FROM adapter_control.jobs ORDER BY created_at DESC LIMIT 50')
        if method == 'GET' and len(route) == 2 and route[0] == 'jobs':
            return self.store.job(route[1])
        if method == 'POST' and route == ['inspect']:
            # Inspection needs only table/alias; not an approved measurement mapping.
            alias, table = body['connection'], body['source_table']
            if alias not in self.registry or alias == 'directory':
                raise ValueError('Неизвестное подключение')
            table_parts(table)
            # Store an immutable inspection document, not an activatable profile.
            profile = self.store.query('INSERT INTO adapter_control.profiles(id,document) VALUES(%s,%s) RETURNING *',
                (str(uuid.uuid4()), Jsonb({'connection': alias, 'source_table': table, 'name': 'Обследование'})), one=True)
            return self.store.enqueue(profile['id'], 'inspect')
        if method == 'POST' and route == ['jobs']:
            profile = self.store.profile(body['profile_id'])
            build_settings(profile['document'], self.registry)
            kind = body.get('kind', 'preview')
            if kind not in ('preview', 'apply'):
                raise ValueError('Разрешены preview и apply')
            begin, end = date.fromisoformat(body['from']), date.fromisoformat(body['to'])
            if begin >= end or (end-begin).days > 366:
                raise ValueError('Выберите диапазон от 1 до 366 дней; конечная дата не включается')
            preview_id = body.get('preview_id')
            if kind == 'apply':
                preview = self.store.job(preview_id or '')
                if preview['kind'] != 'preview' or preview['status'] != 'succeeded' or preview['profile_id'] != profile['id'] or preview['date_from'] != begin or preview['date_to'] != end:
                    raise ValueError('Нужен успешный предварительный расчёт того же профиля и диапазона')
                plan = next((event for event in preview['events'] if event['event'] == 'plan'), None)
                effective_end = min(end, datetime.now(ZoneInfo('Europe/Moscow')).date())
                if not plan or date.fromisoformat(plan['to_exclusive'][:10]) != effective_end:
                    raise ValueError('После предварительного расчёта изменилась граница завершённых суток. Повторите проверку.')
            return self.store.enqueue(profile['id'], kind, begin, end, preview_id)
        raise ValueError('Неизвестная операция')


def process_job(controller, job, stop_requested=None):
    store = controller.store
    profile_record = store.profile(job['profile_id'])
    document = profile_record['document']
    if job['kind'] == 'inspect':
        with psycopg.connect(controller.registry[document['connection']], autocommit=True, row_factory=dict_row, connect_timeout=8) as conn:
            conn.execute('SET default_transaction_read_only=on')
            conn.execute("SET statement_timeout='15s'")
            store.event(job['id'], clean_json({'event': 'inspection', **inspect_source(conn, document['source_table'])}))
        return
    settings = build_settings(document, controller.registry)
    if profile_record['signature'] != fingerprint(settings):
        raise ValueError('Подключения или параметры назначения изменились. Создайте и проверьте новую версию профиля.')
    allowed = directory_names(document, controller.registry)
    errors = []
    days = []
    def emit(event):
        errors.extend(event.get('errors', []))
        if event['event'] == 'day':
            days.append(event)
            if job['kind'] == 'scheduled':
                # Advance only contiguous, successfully committed automatic days.
                next_day = date.fromisoformat(event['date'])+timedelta(days=1)
                store.query('UPDATE adapter_control.profiles SET processed_until=%s WHERE id=%s AND (processed_until IS NULL OR processed_until=%s)',
                            (next_day, job['profile_id'], date.fromisoformat(event['date'])))
        store.event(job['id'], clean_json(event))
    refresh(settings, start=job['date_from'], end=job['date_to'], apply=job['kind'] in ('apply', 'scheduled'),
            emit=emit, allowed_sensors=allowed, stop_requested=stop_requested)
    if errors:
        raise RefreshError('; '.join(dict.fromkeys(errors)))
    if not days:
        raise RefreshError('В диапазоне нет завершённых суток; запись не выполнялась')
    if job['kind'] == 'preview':
        store.query('UPDATE adapter_control.profiles SET validated=true WHERE id=%s', (job['profile_id'],))


def worker(controller, stop):
    """One leader across replicas; PostgreSQL releases leadership on disconnect."""
    while not stop.is_set():
        try:
            with psycopg.connect(controller.store.dsn, autocommit=True, row_factory=dict_row, connect_timeout=8) as leader:
                if not leader.execute('SELECT pg_try_advisory_lock(482019771) AS acquired').fetchone()['acquired']:
                    stop.wait(5)
                    continue
                controller.store.query("UPDATE adapter_control.jobs SET status='queued', error='Возобновлено после перезапуска' WHERE status='running'")
                while not stop.is_set():
                    leader.execute('SELECT 1')
                    state = controller.store.query('SELECT * FROM adapter_control.settings WHERE id=1', one=True)
                    if state['enabled']:
                        profile = controller.store.query('SELECT * FROM adapter_control.profiles WHERE active AND validated', one=True)
                        if profile:
                            for begin, end, slot in schedule_ranges(profile, datetime.now(timezone.utc), state['repeat_minutes']):
                                controller.store.enqueue(profile['id'], 'scheduled', begin, end, slot=slot)
                    job = controller.store.claim()
                    if not job:
                        stop.wait(2)
                        continue
                    try:
                        process_job(controller, job, stop.is_set)
                        controller.store.query("UPDATE adapter_control.jobs SET status='succeeded',finished_at=now() WHERE id=%s", (job['id'],))
                    except Exception as exc:
                        if isinstance(exc, RefreshInterrupted):
                            controller.store.query("UPDATE adapter_control.jobs SET status='queued',attempts=attempts-1,error=%s WHERE id=%s", (str(exc), job['id']))
                            continue
                        retry = job['kind'] == 'scheduled' and job['attempts'] < 5 and isinstance(exc, (psycopg.Error, RefreshError))
                        message = ('Ошибка PostgreSQL; проверьте доступность, права и структуру' if isinstance(exc, psycopg.Error)
                                   else str(exc) if isinstance(exc, (ValueError, RefreshError)) else 'Внутренняя ошибка задания')
                        controller.store.query('''UPDATE adapter_control.jobs SET status=%s,error=%s,finished_at=now(),
                            available_at=now()+interval '5 minutes' WHERE id=%s''', ('queued' if retry else 'failed', message, job['id']))
                        LOG.error('job=%s status=%s error=%s', job['id'], 'retry' if retry else 'failed', message)
        except Exception:
            LOG.error('Worker unavailable; retrying control database connection')
            stop.wait(5)


def serve():
    token = os.environ.get('ENERGY_ADAPTER_TOKEN', '')
    if len(token) < 24:
        raise ValueError('ENERGY_ADAPTER_TOKEN: задайте внутренний токен длиной не менее 24 символов')
    store = Store(os.environ.get('ENERGY_ADAPTER_CONTROL_DSN') or os.environ['ENERGY_AGG_TARGET_DSN'])
    controller = Controller(store, connections())
    # Installation is intentionally explicit, not performed on startup.
    version = store.query('SELECT schema_version FROM adapter_control.settings WHERE id=1', one=True)
    if not version or version['schema_version'] != 1:
        raise ValueError('Неподдерживаемая версия служебной схемы')
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log URLs, request bodies or connection credentials.

        def handle_request(self):
            if not hmac.compare_digest(self.headers.get('X-Internal-Token', ''), token):
                self.send_response(403)
                self.end_headers()
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 131072:
                    raise ValueError('Слишком большой запрос')
                body = json.loads(self.rfile.read(size)) if size else {}
                if not isinstance(body, dict):
                    raise ValueError('Ожидается JSON-объект')
                result = controller.dispatch(self.command, self.path, body)
                payload, status = clean_json(result), 200
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                payload, status = {'detail': str(exc) if isinstance(exc, ValueError) else 'Некорректные параметры запроса'}, 400
            except psycopg.Error:
                payload, status = {'detail': 'База недоступна или служебная схема не установлена'}, 503
            except Exception:
                payload, status = {'detail': 'Внутренняя ошибка адаптера'}, 500
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('ENERGY_ADAPTER_PORT', '8002'))), Handler)
    server.timeout = 1
    server.daemon_threads = True
    thread = threading.Thread(target=worker, args=(controller, stop), daemon=True)
    thread.start()
    def shutdown(*_args):
        stop.set()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()
        thread.join()  # Let the current day commit or roll back before exit.
