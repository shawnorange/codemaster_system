# 本地与 Docker 常用命令

生成日期：2026-05-11

## 1. 本地环境

```bash
cd /Users/apple/coding/python/codemaster_system

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

当前机器上 `python` 命令不可用，使用：

```bash
.venv/bin/python
```

## 2. 环境变量

```bash
cp .env.example .env
```

本地 PostgreSQL 连接关键项：

```env
POSTGRES_DB=codemaster
POSTGRES_USER=codemaster_user
POSTGRES_PASSWORD=change-me
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_CONN_MAX_AGE=60
```

Docker 内 web 服务连接 db 时通常使用：

```env
POSTGRES_HOST=db
```

## 3. 本地 Django 命令

检查：

```bash
.venv/bin/python manage.py check
```

迁移：

```bash
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py migrate
```

启动开发服务：

```bash
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

收集静态文件：

```bash
.venv/bin/python manage.py collectstatic --noinput
```

测试：

```bash
.venv/bin/python manage.py test entry.tests
```

使用 SQLite 测试 settings：

```bash
DJANGO_SETTINGS_MODULE=codemaster_system.settings_sqlite .venv/bin/python manage.py check
DJANGO_SETTINGS_MODULE=codemaster_system.settings_sqlite .venv/bin/python manage.py test entry.tests
```

## 4. 题库与学生导入

导入题库 JSON：

```bash
.venv/bin/python manage.py import_questions path/to/questions.json
```

只校验不落库：

```bash
.venv/bin/python manage.py import_questions path/to/questions.json --dry-run
```

导入 C++ 学生 Excel：

```bash
.venv/bin/python manage.py import_cpp_students path/to/students.xlsx
```

指定老师与默认密码：

```bash
.venv/bin/python manage.py import_cpp_students path/to/students.xlsx \
  --teacher-username teacher001 \
  --default-password 'change-me'
```

修复学生用户名：

```bash
.venv/bin/python manage.py normalize_student_usernames --dry-run
.venv/bin/python manage.py normalize_student_usernames
```

## 5. Docker 本地启动

构建 web 镜像：

```bash
docker compose build web
```

启动 PostgreSQL：

```bash
docker compose up -d db
```

执行检查：

```bash
docker compose run --rm web python manage.py check
docker compose run --rm web python manage.py makemigrations --check --dry-run
```

迁移和静态文件：

```bash
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py collectstatic --noinput
```

启动全量服务：

```bash
docker compose up -d
```

访问：

```text
http://127.0.0.1/
```

查看日志：

```bash
docker compose logs -f web
docker compose logs -f nginx
docker compose logs -f db
```

进入 Django shell：

```bash
docker compose run --rm web python manage.py shell
```

进入 PostgreSQL：

```bash
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

停止服务但保留 volume：

```bash
docker compose down
```

不要执行：

```bash
docker compose down -v
```

## 6. 生产 Docker 启动

推荐：

```bash
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" ./deploy/scripts/deploy.sh
```

手动等价命令：

```bash
docker compose -f docker-compose.prod.yml build web
docker compose -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.prod.yml run --rm web python manage.py check
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate
docker compose -f docker-compose.prod.yml run --rm web python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml up -d
```

生产检查：

```bash
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" BASE_URL=http://127.0.0.1 ./deploy/scripts/check_prod.sh
```

## 7. 数据库备份与恢复

备份：

```bash
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" \
BACKUP_DIR=/opt/codemaster/backups \
./deploy/scripts/backup_db.sh
```

恢复：

```bash
COMPOSE_FILE_ARGS="-f docker-compose.prod.yml" \
./deploy/scripts/restore_db.sh /opt/codemaster/backups/codemaster_YYYYmmdd_HHMMSS.dump
```

恢复脚本会要求输入：

```text
RESTORE
```

## 8. 当前校验结果

本次文档生成前已执行：

```bash
DJANGO_SETTINGS_MODULE=codemaster_system.settings_sqlite .venv/bin/python manage.py check
DJANGO_SETTINGS_MODULE=codemaster_system.settings_sqlite .venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=codemaster_system.settings_sqlite .venv/bin/python manage.py test entry.tests.test_miniapp_auth_api entry.tests.test_student_learning_api
```

结果：

- `check`：通过
- `makemigrations --check --dry-run`：No changes detected
- 小程序登录与学生统计相关测试：27 个测试通过
