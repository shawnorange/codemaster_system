#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_URL="${APP_URL:-http://127.0.0.1/}"
WITH_LIVEKIT=0
NO_BUILD=0

usage() {
  cat <<'EOF'
Usage:
  scripts/start_local.sh [--no-build] [--with-livekit]

Starts the local Docker stack, applies Django migrations, and checks http://127.0.0.1/.

Options:
  --no-build      Start existing images without rebuilding.
  --with-livekit  Also start LiveKit and LiveKit egress profile services.
  -h, --help      Show this help.

Environment:
  APP_URL         URL to check after startup. Default: http://127.0.0.1/
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build)
      NO_BUILD=1
      shift
      ;;
    --with-livekit)
      WITH_LIVEKIT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not in PATH." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop, then run this script again." >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo ".env is missing. Create it from .env.example before starting services." >&2
  exit 1
fi

compose=(docker compose)
services=(db redis web homework-import-worker realtime nginx)

if [[ "$WITH_LIVEKIT" -eq 1 ]]; then
  compose+=(--profile livekit)
  services+=(livekit livekit-egress)
fi

echo "Checking docker-compose config..."
"${compose[@]}" config -q

up_args=(up -d)
if [[ "$NO_BUILD" -eq 0 ]]; then
  up_args+=(--build)
fi
up_args+=("${services[@]}")

echo "Starting local services..."
"${compose[@]}" "${up_args[@]}"

echo "Waiting for web container to accept management commands..."
for attempt in {1..30}; do
  if "${compose[@]}" exec -T web python manage.py check >/tmp/codemaster_start_check.log 2>&1; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    cat /tmp/codemaster_start_check.log >&2 || true
    echo "Web container did not become ready in time." >&2
    exit 1
  fi
  sleep 2
done

echo "Applying database migrations..."
"${compose[@]}" exec -T web python manage.py migrate

echo "Checking ${APP_URL}..."
for attempt in {1..20}; do
  status_code="$(curl -sS -o /tmp/codemaster_start_home.html -w "%{http_code}" --max-time 5 "$APP_URL" || true)"
  if [[ "$status_code" =~ ^(200|302)$ ]]; then
    echo "Local service is ready: ${APP_URL} (HTTP ${status_code})"
    "${compose[@]}" ps
    exit 0
  fi
  sleep 2
done

echo "Service started, but ${APP_URL} did not return 200/302." >&2
"${compose[@]}" ps >&2
exit 1
