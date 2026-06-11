#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_CHECK=1
REMOTE="${REMOTE:-origin}"
MESSAGE=""

usage() {
  cat <<'EOF'
Usage:
  scripts/commit_and_push.sh "commit message" [--no-check]

Stages current changes, excludes local secret env files, commits, and pushes the
current branch to GitHub.

Options:
  --no-check   Skip Django system check before committing.
  -h, --help   Show this help.

Environment:
  REMOTE       Git remote to push. Default: origin
  PYTHON_BIN   Python executable for manage.py check. Default: .venv/bin/python, then python3
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-check)
      RUN_CHECK=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$MESSAGE" ]]; then
        MESSAGE="$1"
      else
        MESSAGE="${MESSAGE} $1"
      fi
      shift
      ;;
  esac
done

cd "$ROOT_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not inside a git repository." >&2
  exit 1
fi

branch="$(git branch --show-current)"
if [[ -z "$branch" ]]; then
  echo "Cannot push from a detached HEAD. Check out a branch first." >&2
  exit 1
fi

if [[ -z "$MESSAGE" ]]; then
  read -r -p "Commit message: " MESSAGE
fi

if [[ -z "${MESSAGE// }" ]]; then
  echo "Commit message cannot be empty." >&2
  exit 1
fi

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "Git remote '${REMOTE}' does not exist." >&2
  exit 1
fi

if git diff --quiet && git diff --cached --quiet && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
  echo "No changes to commit."
  exit 0
fi

echo "Staging changes..."
git add -A

while IFS= read -r path; do
  case "$path" in
    .env|.env.*)
      if [[ "$path" != ".env.example" ]]; then
        git restore --staged -- "$path"
        echo "Skipped local env file: $path"
      fi
      ;;
  esac
done < <(git diff --cached --name-only)

if git diff --cached --quiet; then
  echo "No staged changes after filtering local env files."
  exit 0
fi

if [[ "$RUN_CHECK" -eq 1 ]]; then
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    python_bin="$PYTHON_BIN"
  elif [[ -x ".venv/bin/python" ]]; then
    python_bin=".venv/bin/python"
  else
    python_bin="python3"
  fi

  echo "Running Django system check..."
  DJANGO_SETTINGS_MODULE=codemaster_system.settings_sqlite "$python_bin" manage.py check
fi

echo "Files staged for commit:"
git diff --cached --name-status

git commit -m "$MESSAGE"

if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  echo "Pushing ${branch}..."
  git push
else
  echo "Pushing ${branch} and setting upstream..."
  git push -u "$REMOTE" "$branch"
fi

echo "Done."
