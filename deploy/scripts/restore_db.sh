#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_FILE_ARGS="${COMPOSE_FILE_ARGS:-}"
DUMP_FILE="${1:-}"

compose() {
    docker compose ${COMPOSE_FILE_ARGS} "$@"
}

if [[ -z "$DUMP_FILE" ]]; then
    echo "Usage: $0 /path/to/codemaster_YYYYmmdd_HHMMSS.dump"
    exit 1
fi

if [[ ! -f "$DUMP_FILE" ]]; then
    echo "Dump file not found: $DUMP_FILE"
    exit 1
fi

if [[ ! -f ".env" ]]; then
    echo "Missing .env. Cannot determine database settings."
    exit 1
fi

cat <<EOF
This will restore a PostgreSQL dump into the configured database using:
  pg_restore --clean --if-exists --no-owner --no-acl

The restore can overwrite existing production data. Make a fresh backup first.
Type RESTORE to continue.
EOF

read -r confirmation
if [[ "$confirmation" != "RESTORE" ]]; then
    echo "Restore cancelled."
    exit 1
fi

cat "$DUMP_FILE" | compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-acl'

cat <<'EOF'
Restore finished. Run the production check script next:
  ./deploy/scripts/check_prod.sh

For direct table-count verification, run:
  docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select '\''entry_portaluser'\'' as table_name, count(*) from entry_portaluser union all select '\''entry_student'\'', count(*) from entry_student union all select '\''entry_teacherstudentassignment'\'', count(*) from entry_teacherstudentassignment union all select '\''entry_course'\'', count(*) from entry_course union all select '\''entry_coursecategory'\'', count(*) from entry_coursecategory union all select '\''entry_courselevel'\'', count(*) from entry_courselevel union all select '\''entry_coursecontent'\'', count(*) from entry_coursecontent union all select '\''questions'\'', count(*) from questions union all select '\''entry_homeworkassignment'\'', count(*) from entry_homeworkassignment union all select '\''entry_homeworkimportjob'\'', count(*) from entry_homeworkimportjob union all select '\''entry_homeworkquestion'\'', count(*) from entry_homeworkquestion union all select '\''entry_homeworksubmission'\'', count(*) from entry_homeworksubmission union all select '\''entry_homeworksubmissionanswer'\'', count(*) from entry_homeworksubmissionanswer;"'
EOF
