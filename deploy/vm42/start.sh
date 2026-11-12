#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
if docker compose version >/dev/null 2>&1; then
  legacy_compose=0
  compose() { docker compose -p smart-energy-vm42 --env-file deploy/vm42/.env -f docker-compose.vm42.yml "$@"; }
else
  legacy_compose=1
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
compose stop adapter notifications
compose run --rm --no-deps adapter upgrade-aggregates
compose run --rm --no-deps adapter upgrade-control
if [ "$legacy_compose" = 1 ]; then
  # Compose v1 cannot merge ContainerConfig from newer Docker images.
  # Recreate only application containers; keep named volumes and DB/tunnel.
  compose stop frontend backend analytical adapter notifications
  compose rm -f frontend backend analytical adapter notifications
fi
compose up -d --no-build db source-db backend analytical adapter frontend
compose up -d --no-build notifications
compose ps
