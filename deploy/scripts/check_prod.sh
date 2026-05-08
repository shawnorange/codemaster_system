#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_FILE_ARGS="${COMPOSE_FILE_ARGS:-}"
BASE_URL="${BASE_URL:-http://127.0.0.1}"

compose() {
    docker compose ${COMPOSE_FILE_ARGS} "$@"
}

echo "== docker compose ps =="
compose ps

echo "== web logs =="
compose logs --tail=100 web

echo "== nginx logs =="
compose logs --tail=100 nginx

echo "== Django deploy check =="
compose run --rm -T web python manage.py check --deploy

echo "== Migration drift check =="
compose run --rm -T web python manage.py makemigrations --check --dry-run

echo "== Database connection and key table counts =="
compose run --rm -T web python manage.py shell -c '
from django.db import connection

tables = [
    "entry_portaluser",
    "entry_student",
    "entry_teacherstudentassignment",
    "entry_course",
    "entry_coursecategory",
    "entry_courselevel",
    "entry_coursecontent",
    "questions",
    "entry_homeworkassignment",
    "entry_homeworkimportjob",
    "entry_homeworkquestion",
    "entry_homeworksubmission",
    "entry_homeworksubmissionanswer",
]

connection.ensure_connection()
print(f"database_vendor={connection.vendor}")
with connection.cursor() as cursor:
    for table in tables:
        cursor.execute(f"select count(*) from {table}")
        count = cursor.fetchone()[0]
        print(f"{table}: {count}")
'

echo "== HTTP smoke checks =="
for path in "/" "/teacher/students" "/teacher/homework-stats"; do
    status_code="$(curl -sS -o /dev/null -w "%{http_code}" "${BASE_URL}${path}")"
    echo "${path} -> ${status_code}"
done
