#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
if docker compose version >/dev/null 2>&1; then
  compose() { docker compose -p smart-energy-vm42 --env-file deploy/vm42/.env -f docker-compose.vm42.yml "$@"; }
else
  compose() { docker-compose -p smart-energy-vm42 --env-file deploy/vm42/.env -f docker-compose.vm42.yml "$@"; }
fi
test -s deploy/vm42/app.env
if [ "${VM42_BUILD:-0}" = 1 ]; then compose build; fi
compose up -d --no-build db source-db
attempt=0
until compose exec -T db pg_isready -U postgres -p 25433 -d student >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then echo 'Application PostgreSQL is not ready' >&2; exit 1; fi
  sleep 2
done
if [ ! -f deploy/vm42/backups/restore-complete ]; then
  compose run --rm migrate
fi
# Stop the previous writer before the explicit, additive aggregate upgrade.
# The database volume and existing readings remain in place.
compose stop adapter
compose run --rm --no-deps adapter upgrade-aggregates
compose up -d --no-build
compose exec -T adapter python -m adapter init-control
compose ps
