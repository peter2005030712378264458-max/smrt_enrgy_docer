#!/bin/bash
# Smart Energy / local-develop. macOS: bash ./start-mac.command
# Requires Docker Desktop and access to the existing PostgreSQL server via SSH.
# Creates local configuration only when needed; does not enqueue manual refreshes
# or change the existing scheduler setting. No secrets are embedded here.
# Optional: SMART_ENERGY_SSH_TARGET=user@host, SMART_ENERGY_SSH_KEY=/path/to/key.
set -Eeuo pipefail
umask 077
export PATH="/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LAUNCHER_NAME="$(basename -- "${BASH_SOURCE[0]}")"
cd "$PROJECT_DIR"
STEP='Подготовка'

fail() { printf '\nОшибка: %s\n' "$*" >&2; exit 1; }
on_exit() {
    local code=$?
    if [ "$code" -ne 0 ]; then
        printf '\nНе удалось завершить этап: %s. Исправьте причину выше и запустите файл снова.\n' "$STEP" >&2
    fi
    if [ -t 0 ]; then
        printf '\nНажмите Enter, чтобы закрыть это окно… '
        read -r _ || true
    fi
}
trap on_exit EXIT

[ "$(uname -s)" = Darwin ] || fail 'Этот файл предназначен для macOS.'
chmod u+x "$PROJECT_DIR/$LAUNCHER_NAME"
for executable in docker ssh openssl curl nc open; do
    command -v "$executable" >/dev/null 2>&1 || fail "Не найдена команда $executable. Установите Docker Desktop; остальные команды входят в macOS."
done

STEP='Запуск Docker Desktop'
if ! docker info >/dev/null 2>&1; then
    printf 'Запускаю Docker Desktop…\n'
    open -a Docker
    ready=0
    for ((attempt=0; attempt<120; attempt++)); do
        if docker info >/dev/null 2>&1; then ready=1; break; fi
        sleep 2
    done
    [ "$ready" -eq 1 ] || fail 'Docker Desktop не запустился за 4 минуты. Завершите его первоначальную настройку и повторите запуск.'
fi
docker compose version >/dev/null

# Build the small adapter image first: its Python and psycopg prepare configuration
# without requiring Python/Homebrew on the Mac or evaluating .env as shell code.
STEP='Подготовка образа настройки'
printf '\nПодготавливаю окружение настройки (первый запуск может занять несколько минут)…\n'
docker build -t smart-energy-local-adapter -f adapter/Dockerfile .

STEP='Настройка локальных файлов'
runtime="$(docker run --rm -i --user "$(id -u):$(id -g)" \
    --mount "type=bind,source=$PROJECT_DIR,target=/workspace" \
    --entrypoint python smart-energy-local-adapter - <<'PY_CONFIG'
import os
from pathlib import Path
import re
import secrets
import shutil

from psycopg.conninfo import make_conninfo, conninfo_to_dict

root = Path('/workspace')


def local_path(value):
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise SystemExit('Пути конфигурации должны находиться внутри папки проекта.')
    return path


def read_env(path):
    result = {}
    if path.exists():
        for raw in path.read_text(encoding='utf-8-sig').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].lstrip()
            if '=' not in line:
                raise SystemExit(f'Некорректная строка в {path.relative_to(root)}')
            key, value = line.split('=', 1)
            value = value.strip()
            if value.startswith(("'", '"')):
                quote = value[0]
                match = re.fullmatch(r'([\s\S]*)' + re.escape(quote) + r'\s*(?:#.*)?', value[1:])
                if not match:
                    raise SystemExit(f'Проверьте кавычки {key.strip()} в {path.relative_to(root)}')
                value = match.group(1)
                if quote == "'":
                    value = value.replace("\\'", "'")
                else:
                    value = value.replace('\\"', '"').replace('\\\\', '\\')
            else:
                value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
                value = value.replace('$$', '$')
            if '${' in value:
                raise SystemExit(f'Для автоматической настройки задайте {key.strip()} в {path.relative_to(root)} без ссылок на другие переменные.')
            result[key.strip()] = value
    return result


def placeholder(value):
    return not value or any(word in value for word in ('replace-me', 'replace-with-', 'ВАШ_'))


def put(path, key, value):
    # Single-quoted Compose values keep passwords and dollar signs literal.
    if any(c in value for c in '\r\n\x00'):
        raise SystemExit(f'Недопустимый перевод строки в {key}')
    text = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    line = key + "='" + value.replace("'", "\\'") + "'"
    pattern = r'(?m)^\s*(?:export\s+)?' + re.escape(key) + r'\s*=.*$'
    if re.search(pattern, text):
        text = re.sub(pattern, lambda _: line, text)
    else:
        text = text.rstrip('\r\n') + ('\n' if text else '') + line + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    path.chmod(0o600)


main_path = root / '.env'
main = read_env(main_path)
defaults = {
    'BACKEND_ENV_FILE': './Бэк/project_backend/.env',
    'ANALYTICS_ENV_FILE': './Аналитический сервис/Analytical_service/.env',
    'ADAPTER_ENV_FILE': './adapter/.env',
    'ENERGY_USE_STORED_ENERGY': '0',
}
for key, value in defaults.items():
    if key not in main:
        put(main_path, key, value)
        main[key] = value

backend_path = local_path(main['BACKEND_ENV_FILE'])
analytics_path = local_path(main['ANALYTICS_ENV_FILE'])
adapter_path = local_path(main['ADAPTER_ENV_FILE'])
for dest, template in (
    (backend_path, root / 'Бэк/project_backend/.env.example'),
    (analytics_path, root / 'Аналитический сервис/Analytical_service/.env.example'),
):
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, dest)
        dest.chmod(0o600)

backend, analytics, adapter = map(read_env, (backend_path, analytics_path, adapter_path))
tokens = [v for v in (main.get('ENERGY_ADAPTER_TOKEN'), adapter.get('ENERGY_ADAPTER_TOKEN')) if not placeholder(v)]
if len(set(tokens)) > 1:
    raise SystemExit('ENERGY_ADAPTER_TOKEN в .env и файле адаптера различается. Существующие токены не заменены.')
token = tokens[0] if tokens else secrets.token_hex(32)
if len(token) < 24:
    raise SystemExit('Существующий ENERGY_ADAPTER_TOKEN короче 24 символов.')
for path, values in ((main_path, main), (adapter_path, adapter)):
    if placeholder(values.get('ENERGY_ADAPTER_TOKEN')):
        put(path, 'ENERGY_ADAPTER_TOKEN', token)

# Compose loads analytics first, then backend, for both application services.
app_env = {**analytics, **backend}
analytics_token = app_env.get('ANALYTICS_INTERNAL_TOKEN')
gateway_token = app_env.get('ANALYTICS_SERVICE_TOKEN')
if gateway_token and analytics_token and gateway_token != analytics_token:
    raise SystemExit('ANALYTICS_SERVICE_TOKEN и ANALYTICS_INTERNAL_TOKEN различаются. Исправьте существующие настройки.')
if placeholder(analytics_token):
    put(backend_path, 'ANALYTICS_INTERNAL_TOKEN', gateway_token or secrets.token_hex(32))

host = main.get('POSTGRES_HOST', 'host.docker.internal')
port = main.get('POSTGRES_PORT', '15432')
base_user = backend.get('POSTGRES_USER')
base_password = backend.get('POSTGRES_PASSWORD')


def connection(prefix, db):
    selected_host = backend.get(prefix + '_HOST', host)
    if selected_host in ('127.0.0.1', 'localhost'):
        selected_host = 'host.docker.internal'
    values = dict(host=selected_host, port=backend.get(prefix + '_PORT', port),
                  dbname=backend.get(prefix + '_DB', db),
                  user=backend.get(prefix + '_USER', base_user),
                  password=backend.get(prefix + '_PASSWORD', base_password), connect_timeout=8)
    if placeholder(values['user']) or placeholder(values['password']):
        raise SystemExit(f'Заполните учётные данные {prefix} в {backend_path.relative_to(root)}; доступ к БД нельзя создать автоматически.')
    return make_conninfo(**values)


source_db = backend.get('SOURCE_POSTGRES_DB', backend.get('POSTGRES_DB', 'paradigm_db'))
generated = {
    'ENERGY_AGG_SOURCE_DSN': lambda: connection('SOURCE_POSTGRES', source_db),
    'ENERGY_AGG_TARGET_DSN': lambda: connection('ENERGY_AGG_POSTGRES', 'student'),
    'ENERGY_AGG_DIRECTORY_DSN': lambda: connection('SOURCE_POSTGRES', source_db),
    'ENERGY_ADAPTER_STATEMENT_TIMEOUT': lambda: '120',
}
for key, factory in generated.items():
    if placeholder(adapter.get(key)):
        put(adapter_path, key, factory())

# Existing explicit DSNs remain untouched, including their selected database/role.
for key, value in read_env(adapter_path).items():
    if key.endswith('_DSN'):
        dsn = conninfo_to_dict(value)
        if dsn.get('host') in ('127.0.0.1', 'localhost'):
            raise SystemExit(f'{key}: внутри Docker используйте host.docker.internal вместо localhost. Существующий DSN не изменён.')

runtime = {
    'DB_HOST': host,
    'DB_PORT': port,
    'HTTPS_PORT': main.get('HTTPS_PORT', '443'),
    'CERT_PATH': str(local_path(main.get('TLS_CERTIFICATE_PATH', './ssl/nginx.crt'))).replace('/workspace/', '', 1),
    'KEY_PATH': str(local_path(main.get('TLS_KEY_PATH', './ssl/nginx.key'))).replace('/workspace/', '', 1),
}
for key, value in runtime.items():
    if any(c in value for c in '\r\n\x00'):
        raise SystemExit(f'Недопустимое значение {key}')
    if key.endswith('PORT') and (not value.isdigit() or not 1 <= int(value) <= 65535):
        raise SystemExit(f'Некорректный порт {key}')
    print(key + '=' + value)
PY_CONFIG
)"
while IFS='=' read -r key value; do
    case "$key" in
        DB_HOST) DB_HOST="$value" ;;
        DB_PORT) DB_PORT="$value" ;;
        HTTPS_PORT) HTTPS_PORT="$value" ;;
        CERT_PATH) CERT_PATH="$PROJECT_DIR/$value" ;;
        KEY_PATH) KEY_PATH="$PROJECT_DIR/$value" ;;
        *) fail 'Не удалось прочитать результат настройки.' ;;
    esac
done <<< "$runtime"

STEP='Создание HTTPS-сертификата'
if [ ! -e "$CERT_PATH" ] && [ ! -e "$KEY_PATH" ]; then
    mkdir -p "$(dirname "$CERT_PATH")" "$(dirname "$KEY_PATH")"
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$KEY_PATH" -out "$CERT_PATH" -extensions local_https \
        -config <(printf '%s\n' '[req]' 'distinguished_name=dn' '[dn]' '[local_https]' \
            'subjectAltName=DNS:localhost,IP:127.0.0.1' 'basicConstraints=CA:FALSE' \
            'keyUsage=digitalSignature,keyEncipherment' 'extendedKeyUsage=serverAuth') \
        -subj '/CN=localhost'
elif [ ! -f "$CERT_PATH" ] || [ ! -f "$KEY_PATH" ]; then
    fail 'Есть только один файл сертификата/ключа либо путь указывает на папку. Восстановите пару; существующие файлы не перезаписаны.'
fi

STEP='Подключение к PostgreSQL'
if [ "$DB_HOST" = host.docker.internal ]; then
    if nc -z -w 2 127.0.0.1 "$DB_PORT" >/dev/null 2>&1; then
        printf '\nПорт %s уже открыт; использую существующее подключение.\n' "$DB_PORT"
    else
        printf '\nОткрываю SSH-туннель. SSH может запросить пароль или подтверждение ключа сервера.\n'
        ssh_options=(-fN -o ExitOnForwardFailure=yes -o ConnectTimeout=15 \
            -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
            -L "127.0.0.1:$DB_PORT:localhost:5432")
        if [ -n "${SMART_ENERGY_SSH_KEY:-}" ]; then
            ssh_options+=(-i "$SMART_ENERGY_SSH_KEY")
        fi
        ssh "${ssh_options[@]}" "${SMART_ENERGY_SSH_TARGET:-restricted_user@193.232.208.50}"
    fi
fi

compose() { docker compose -f docker-compose.yml -f docker-compose.adapter.yml "$@"; }
STEP='Проверка конфигурации и сборка приложения'
compose config --quiet
compose build
STEP='Установка служебных таблиц адаптера'
compose run --rm --no-deps adapter init-control
STEP='Запуск приложения и миграций Django'
compose up -d

# Wait for migrations and HTTP readiness rather than assuming up -d is ready.
url="https://localhost:$HTTPS_PORT"
ready=0
for ((attempt=0; attempt<120; attempt++)); do
    status="$(curl --noproxy '*' -k -s -o /dev/null -w '%{http_code}' \
        --connect-timeout 2 --max-time 4 "$url/api/auth/me/" || true)"
    if [ "$status" = 401 ] || [ "$status" = 200 ]; then ready=1; break; fi
    sleep 2
done
if [ "$ready" -ne 1 ]; then
    compose ps
    fail 'Приложение ещё не готово. Проверьте docker compose -f docker-compose.yml -f docker-compose.adapter.yml logs --tail=100.'
fi

STEP='Проверка доступа администратора'
staff="$(compose exec -T backend python manage.py shell -c \
    'from django.contrib.auth import get_user_model; print("STAFF_READY" if get_user_model().objects.filter(is_staff=True,is_active=True).exists() else "STAFF_MISSING")')"
if [[ "$staff" == *STAFF_MISSING* ]]; then
    printf '\nДля управления адаптером нужен администратор. Создайте его сейчас:\n'
    compose exec backend python manage.py createsuperuser
elif [[ "$staff" != *STAFF_READY* ]]; then
    fail 'Не удалось проверить наличие администратора.'
fi

STEP='Проверка адаптера'
compose exec -T backend python manage.py shell -c \
    'from django.conf import settings; import urllib.request; r=urllib.request.Request(settings.ENERGY_ADAPTER_URL.rstrip("/")+"/health",headers={"X-Internal-Token":settings.ENERGY_ADAPTER_TOKEN}); urllib.request.urlopen(r,timeout=10).close()'

printf '\nПриложение запущено: %s\nАдаптер: %s/aggregation\n' "$url" "$url"
printf 'Браузер может показать предупреждение о локальном самоподписанном сертификате.\n'
printf 'Контейнеры и SSH-туннель продолжат работать после закрытия этого окна.\n'
printf 'Остановка: docker compose -f docker-compose.yml -f docker-compose.adapter.yml down\n'
open "$url"
