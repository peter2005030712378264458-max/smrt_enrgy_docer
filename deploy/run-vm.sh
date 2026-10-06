#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

if docker compose version >/dev/null 2>&1; then
    compose_mode=plugin
elif command -v docker-compose >/dev/null 2>&1; then
    compose_version=$(docker-compose version --short 2>/dev/null || true)
    case "$compose_version" in
        1.*|v1.*|'')
            printf '%s\n' 'Docker Compose 2 or newer is required. Install the Compose plugin; see deploy/VM_DEPLOYMENT.md.' >&2
            exit 1
            ;;
    esac
    compose_mode=standalone
else
    printf '%s\n' 'Docker Compose is unavailable. Install the Compose plugin; see deploy/VM_DEPLOYMENT.md.' >&2
    exit 1
fi

run_compose() {
    if [ "$compose_mode" = plugin ]; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

write_existing_env_files() {
    for path in \
        './Аналитический сервис/Analytical_service/.env' \
        './Аналитический сервис/Analytical_service/.env.local' \
        './Бэк/project_backend/.env' \
        './Бэк/project_backend/.env.local' \
        './.env' \
        './.env.vm'; do
        if [ -f "$path" ]; then
            printf '      - "%s"\n' "$path"
        fi
    done
}

existing_env_files=$(write_existing_env_files)

if [ -n "$existing_env_files" ]; then
    config_override=$(mktemp)
    trap 'rm -f "$config_override"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' HUP TERM
    for service in backend analytical; do
        if [ "$service" = backend ]; then
            printf 'services:\n' > "$config_override"
        fi
        printf '  %s:\n    env_file:\n%s\n' "$service" "$existing_env_files" >> "$config_override"
    done
    set -- -f "$config_override" "$@"
fi

if [ -f './.env.vm' ]; then
    set -- --env-file .env.vm "$@"
fi

# Keep the repository file first so relative paths and project naming stay stable.
run_compose --project-directory "$PWD" -f docker-compose.vm.yml "$@"
