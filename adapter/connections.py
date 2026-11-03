"""Encrypted, server-side PostgreSQL aliases; credentials never enter profiles."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from cryptography.fernet import Fernet, InvalidToken
import psycopg
from psycopg.conninfo import make_conninfo

_LOCK = threading.Lock()


def registry_path():
    value = os.environ.get('ENERGY_ADAPTER_CONNECTIONS_FILE')
    return Path(value) if value else None


def cipher():
    path = os.environ.get('ENERGY_ADAPTER_CONNECTION_KEY_FILE')
    if not path:
        raise ValueError('Добавление подключений не настроено: отсутствует отдельный ключ шифрования')
    try:
        return Fernet(Path(path).read_bytes().strip())
    except (OSError, ValueError):
        raise ValueError('Не удалось загрузить ключ реестра подключений') from None


def saved_connections():
    path = registry_path()
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(cipher().decrypt(path.read_bytes()))
        if not isinstance(value, dict) or any(not isinstance(v, str) for v in value.values()):
            raise ValueError()
        return value
    except (OSError, ValueError, InvalidToken):
        raise ValueError('Не удалось расшифровать серверный реестр подключений') from None


def connection_document(body):
    allowed = {'alias', 'host', 'port', 'database', 'user', 'password', 'sslmode'}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ValueError('Передайте отдельные параметры подключения PostgreSQL')
    alias = body.get('alias', '')
    if not isinstance(alias, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,47}', alias) or alias in ('directory', 'paradigm'):
        raise ValueError('Имя: 2–48 латинских символов, цифр, _ или -; paradigm и directory зарезервированы')
    host = body.get('host', '')
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', host):
        raise ValueError('Укажите имя сервера или IP без URL и пути')
    port = body.get('port', 5432)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Порт должен быть числом от 1 до 65535')
    for field in ('database', 'user', 'password'):
        value = body.get(field)
        if not isinstance(value, str) or not value or '\x00' in value or len(value) > 1024:
            raise ValueError('Укажите базу данных, пользователя и пароль')
    sslmode = body.get('sslmode', 'prefer')
    if sslmode not in ('disable', 'prefer', 'require', 'verify-ca', 'verify-full'):
        raise ValueError('Недопустимый режим SSL')
    return alias, make_conninfo(host=host, port=port, dbname=body['database'], user=body['user'],
        password=body['password'], sslmode=sslmode, connect_timeout=5,
        options='-c default_transaction_read_only=on -c statement_timeout=5000')


def check_connection(body):
    alias, dsn = connection_document(body)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute('SELECT 1').fetchone()
            if conn.execute('SHOW transaction_read_only').fetchone()[0] != 'on':
                raise ValueError('Подключение должно работать в режиме чтения')
    except psycopg.Error:
        raise ValueError('PostgreSQL недоступен. Проверьте адрес, порт, доступ из Docker, учётные данные и SSL') from None
    return alias, dsn


def add_connection(body, existing):
    path = registry_path()
    if path is None:
        raise ValueError('Добавление подключений выключено на сервере')
    encryption = cipher()
    alias, dsn = check_connection(body)
    with _LOCK:
        saved = saved_connections()
        if alias in existing or alias in saved:
            raise ValueError('Это имя подключения уже используется')
        path.parent.mkdir(parents=True, exist_ok=True)
        saved[alias] = dsn
        fd, temporary = tempfile.mkstemp(prefix='.connections-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encryption.encrypt(json.dumps(saved).encode()))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {'alias': alias, 'status': 'saved'}
