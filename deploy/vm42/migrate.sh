#!/bin/sh
set -eu
export PGCONNECT_TIMEOUT=10
export PGPASSWORD="$(cat /run/secrets/database_password)"
tables=$(psql -h 127.0.0.1 -p 25433 -U postgres -d student -At -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('student_schema','adapter_control') AND c.relkind IN ('r','p','v','m','f')")
if [ "$tables" != 0 ]; then
  echo 'Target already contains application tables. Refusing to overwrite them.' >&2
  exit 1
fi
if [ -e /backups/student.dump ]; then
  echo 'A previous dump exists. Keep it and use a fresh backup directory for a new migration.' >&2
  exit 1
fi
echo 'Reading a consistent snapshot from .50 (read-only)...'
PGPASSWORD="$(cat /run/secrets/source_app_password)" \
PGOPTIONS='-c default_transaction_read_only=on' \
pg_dump -h 127.0.0.1 -p 25432 -U "${SOURCE_APP_USER:-student}" -d student \
  --format=custom --schema=student_schema --schema=adapter_control \
  --exclude-table-data=student_schema.electricity_sensor_readings \
  --no-owner --no-privileges --lock-wait-timeout=10s -f /backups/student.dump
echo 'Restoring application tables and aggregates on .42...'
pg_restore -h 127.0.0.1 -p 25433 -U postgres -d student --exit-on-error --single-transaction \
  --no-owner --no-privileges /backups/student.dump
# Old profiles contain a source/target fingerprint. Keep their history, but require
# preview against the new connections before the operator enables scheduling.
psql -h 127.0.0.1 -p 25433 -U postgres -d student -v ON_ERROR_STOP=1 <<'SQL'
UPDATE adapter_control.settings SET enabled=false WHERE id=1;
UPDATE adapter_control.profiles SET active=false,validated=false;
SQL
touch /backups/restore-complete
echo 'Restore completed. Run sh deploy/vm42/start.sh.'
