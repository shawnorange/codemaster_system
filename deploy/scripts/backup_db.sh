#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_FILE_ARGS="${COMPOSE_FILE_ARGS:-}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_DIR="${BACKUP_DIR:-/opt/codemaster/backups}"

compose() {
    docker compose ${COMPOSE_FILE_ARGS} "$@"
}

if [[ ! -f ".env" ]]; then
    echo "Missing .env. Cannot determine database settings."
    exit 1
fi

if ! mkdir -p "$BACKUP_DIR" 2>/dev/null; then
    BACKUP_DIR="$PROJECT_ROOT/backups"
    mkdir -p "$BACKUP_DIR"
fi

timestamp="$(date +%Y%m%d_%H%M%S)"
backup_file="$BACKUP_DIR/codemaster_${timestamp}.dump"

echo "Writing PostgreSQL backup to $backup_file"
compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl' > "$backup_file"

echo "Removing backups older than ${BACKUP_RETENTION_DAYS} days from $BACKUP_DIR"
find "$BACKUP_DIR" -type f -name "codemaster_*.dump" -mtime +"$BACKUP_RETENTION_DAYS" -print -delete

echo "Backup complete: $backup_file"
