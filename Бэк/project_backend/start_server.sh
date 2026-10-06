#!/bin/sh
set -eu

if [ "${DJANGO_USE_GUNICORN:-0}" = "1" ] && [ -n "${DJANGO_SECRET_KEY_FILE:-}" ]; then
    python -m config.runtime_secret
fi

python wait_for_db.py

if [ "${RUN_DJANGO_MIGRATIONS:-0}" = "1" ]; then
    python manage.py migrate --noinput
fi

if [ "${RUN_ENERGY_INDEXES:-0}" = "1" ]; then
    python manage.py ensure_energy_indexes
fi

if [ "${DJANGO_USE_GUNICORN:-0}" = "1" ]; then
    exec gunicorn config.wsgi:application \
        --bind "${GUNICORN_BIND:-0.0.0.0:5000}" \
        --workers "${GUNICORN_WORKERS:-2}" \
        --timeout "${GUNICORN_TIMEOUT:-60}" \
        --access-logfile - \
        --error-logfile -
fi

exec python manage.py runserver 0.0.0.0:5000
