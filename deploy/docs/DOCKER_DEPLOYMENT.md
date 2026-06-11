# CodeMaster Docker Deployment

This project is a Django 5.2 + PostgreSQL monolith. It serves Django templates, local static assets, and user-uploaded media. Production deployment should keep the image immutable and store PostgreSQL data, `staticfiles`, and `media` outside the container.

Current migration target: the Tencent Cloud source database is PostgreSQL 15.16, so the Docker database image is pinned to `postgres:15` during the server migration. Do not combine server migration, Dockerization, and a PostgreSQL major-version upgrade in the same cutover window.

## 1. Local Docker Verification

The Dockerfile pins the base image to `python:3.12-slim-bookworm` instead of the rolling `python:3.12-slim` tag, reducing surprises from Debian major-version changes. It also rewrites Debian apt sources to the Alibaba Cloud Debian mirror and configures pip to use the Alibaba Cloud PyPI mirror so image builds are more stable on mainland China ECS hosts. If pip downloads still time out, retry the build first; if the mirror is unstable, temporarily switch to the Tsinghua PyPI mirror.

1. Copy `.env.example` to `.env` and fill local test values. Do not use production secrets locally.
2. Build the web image:
   ```bash
   docker compose build web
   ```
3. Start PostgreSQL:
   ```bash
   docker compose up -d db
   ```
4. Run read-only checks:
   ```bash
   docker compose run --rm web python manage.py check
   docker compose run --rm web python manage.py makemigrations --check --dry-run
   ```
5. For a disposable local database only, run migrations and collect static explicitly:
   ```bash
   docker compose run --rm web python manage.py migrate
   docker compose run --rm web python manage.py collectstatic --noinput
   docker compose up -d
   ```
6. Open `http://127.0.0.1/`.

Do not run `docker compose down -v` unless you intentionally want to delete local Docker volumes.

## 2. Tencent Cloud Codex Verification

Use Tencent Cloud as a staging and remote-debug host before touching Alibaba Cloud production:

1. Pull the target branch.
2. Create a staging `.env` with non-production secrets and a staging database.
3. Run:
   ```bash
   COMPOSE_FILE_ARGS="-f docker-compose.yml" ./deploy/scripts/deploy.sh
   ```
4. Run:
   ```bash
   COMPOSE_FILE_ARGS="-f docker-compose.yml" BASE_URL=http://127.0.0.1 ./deploy/scripts/check_prod.sh
   ```

Tencent Cloud can also SSH into Alibaba Cloud for log inspection, but it should not directly edit production data.

## 3. Alibaba Cloud ECS Directory Layout

Recommended single-host layout:

```text
/opt/codemaster_system
/opt/codemaster/media
/opt/codemaster/staticfiles
/opt/codemaster/postgres
/opt/codemaster/backups
```

Place the Git checkout in `/opt/codemaster_system`. Keep `.env` in that directory, outside Git. Store user uploads in `/opt/codemaster/media`; do not bake them into the image.

PostgreSQL 15 data is mounted at `/var/lib/postgresql/data` inside the container. Do not switch this mount to a different PostgreSQL major-version layout during the migration.

## 4. .env Configuration

Start from `.env.example`, then replace all placeholder values.

Production minimum:

- `DJANGO_DEBUG=False`
- `DJANGO_SECRET_KEY=<long random value>`
- `DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com,your-server-ip`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com,https://www.your-domain.com`
- `DJANGO_SECURE_SSL_REDIRECT=True` after HTTPS is ready, or keep HTTPS redirects in Nginx
- `DJANGO_SECURE_HSTS_SECONDS=31536000` only after HTTPS has been stable
- `POSTGRES_HOST=db`
- `DJANGO_STATIC_ROOT=/app/staticfiles`
- `DJANGO_MEDIA_ROOT=/app/media`
- `CODEMASTER_AUTH_COOKIE_SECURE=True`
- `GUNICORN_TIMEOUT=120`

External services:

- `DASHSCOPE_API_KEY`
- `ARK_API_KEY`
- `WECHAT_MINIAPP_APPID`
- `WECHAT_MINIAPP_SECRET`

Never commit `.env`, database dumps, private keys, or media files.

## 5. Compose Startup

Production should use the production file:

```bash
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" ./deploy/scripts/deploy.sh
```

Manual equivalent:

```bash
docker compose -f docker-compose.prod.yml build web
docker compose -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.prod.yml run --rm web python manage.py check
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate
docker compose -f docker-compose.prod.yml run --rm web python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml up -d
```

Migrations and `collectstatic` are explicit deployment steps. They are intentionally not run in the Dockerfile or the default container command.

## 6. Nginx and HTTPS

`deploy/nginx/codemaster.conf` provides an HTTP server block:

- Proxies app traffic to `web:8000`
- Serves `/static/` from `/var/www/codemaster/staticfiles/`
- Serves `/media/` from `/var/www/codemaster/media/`
- Sets `Host`, `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto`
- Allows uploads up to `50M`

For HTTPS, obtain certificates on the host, mount `/etc/letsencrypt:/etc/letsencrypt:ro`, and add a 443 server block using the real certificate path. Keep `SECURE_PROXY_SSL_HEADER` enabled in Django.

## 7. Media Migration

`media/` contains uploaded homework sources and manual question assets, including PDF, PNG, DOCX, XLSX, and TXT files. It must be migrated with the database.

Recommended flow:

```bash
rsync -a --info=progress2 old-server:/path/to/codemaster_system/media/ /opt/codemaster/media/
```

After restore, verify that `entry_homeworkimportjob.source_file` paths exist under `/opt/codemaster/media`.

## 8. PostgreSQL Backup and Restore

The migration baseline is Tencent Cloud PostgreSQL 15.16 restored into Docker `postgres:15`. Upgrade to a newer PostgreSQL major version only after the Docker migration is stable and separately backed up.

Backup:

```bash
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" BACKUP_DIR=/opt/codemaster/backups ./deploy/scripts/backup_db.sh
```

Restore:

```bash
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" ./deploy/scripts/restore_db.sh /opt/codemaster/backups/codemaster_YYYYmmdd_HHMMSS.dump
```

The restore script requires typing `RESTORE` before it runs `pg_restore --clean --if-exists`.

## 9. Service-Specific Production Checks

Before switching DNS, test from Alibaba Cloud ECS:

- DashScope Qwen API
- Volcengine Ark Vision API
- WeChat miniapp access token and phone-number APIs
- WeChat miniapp server domain whitelist
- ECharts CDN currently referenced from `teacher_homework_stats.html`
- External OJ link `http://oi.dashima.com:88`

The ECharts CDN should be vendored locally before relying on mainland production traffic.

## 10. Homework Import Worker

Homework uploads return after creating a `HomeworkImportJob`. OCR and Qwen/DashScope parsing run in the separate `homework-import-worker` service:

```bash
python manage.py process_homework_import_jobs --poll-interval 2
```

This keeps slow external model calls out of the HTTP request path, so nginx/Gunicorn timeouts do not freeze the upload page. A queued job moves from `uploaded` to `parsing`, then to `parsed` or `failed`.

Operational checks:

- Ensure `homework-import-worker` is running after deploy.
- Ensure the worker shares the same `media` volume as `web`, because source files are uploaded by `web` and parsed by the worker.
- If a process dies while a job is `parsing`, stale jobs are automatically marked `failed` after `HOMEWORK_IMPORT_STALE_MINUTES`.

## 11. Do Not Delete Volumes

Do not run:

```bash
docker compose down -v
```

That command deletes Compose volumes and can remove PostgreSQL, static, or media data in local/test setups. On production, keep PostgreSQL and media on host bind mounts and back them up before every deployment.
