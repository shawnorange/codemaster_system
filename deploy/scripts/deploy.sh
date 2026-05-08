#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_FILE_ARGS="${COMPOSE_FILE_ARGS:-}"

compose() {
    docker compose ${COMPOSE_FILE_ARGS} "$@"
}

wait_for_db() {
    local container_id=""
    local health=""

    container_id="$(compose ps -q db)"
    if [[ -z "$container_id" ]]; then
        echo "db container was not created."
        exit 1
    fi

    echo "Waiting for PostgreSQL healthcheck..."
    for _ in $(seq 1 60); do
        health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' "$container_id" 2>/dev/null || true)"
        if [[ "$health" == "healthy" ]]; then
            echo "PostgreSQL is healthy."
            return 0
        fi
        sleep 2
    done

    echo "PostgreSQL did not become healthy in time."
    compose logs --tail=100 db
    exit 1
}

if [[ ! -f ".env" ]]; then
    echo "Missing .env. Create it from .env.example and fill production values first."
    exit 1
fi

if [[ -f "deploy/scripts/backup_db.sh" ]]; then
    if compose exec -T db true >/dev/null 2>&1; then
        echo "Running database backup before deployment..."
        bash deploy/scripts/backup_db.sh
    else
        echo "db service is not running yet; skipping pre-deploy backup for this run."
    fi
fi

compose build web
compose up -d db
wait_for_db

compose run --rm web python manage.py check
compose run --rm web python manage.py migrate
compose run --rm web python manage.py collectstatic --noinput
compose up -d
compose ps

echo "Recent web logs:"
compose logs --tail=100 web

echo "Recent nginx logs:"
compose logs --tail=100 nginx
