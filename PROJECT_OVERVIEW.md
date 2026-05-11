# CodeMaster 项目总览

生成日期：2026-05-11  
分析范围：`/Users/apple/coding/python/codemaster_system`

说明：IDE 当前打开的 `python/hermes` 是 TypeScript/Node 项目，不包含 Django/PostgreSQL；本批文档基于当前工作区中实际匹配“Docker + PostgreSQL + Django”的 `python/codemaster_system`。

## 1. 项目定位

CodeMaster 是一个 Django 5.2 + PostgreSQL 的单体教学系统。当前主要服务 C++ / GESP 教学闭环，使用 Django 模板页承载教师端、学生端、家长端和校长端页面，同时提供少量 JSON API 给小程序或前端异步交互使用。

当前系统不是 DRF 项目，也没有拆成前后端分离架构；`views.py` 直接处理 HTML 页面、JSON 接口、文件上传和表单提交。

## 2. 技术栈

- 后端：Python 3.12、Django 5.2
- 数据库：PostgreSQL 15，测试/本地备用配置可用 SQLite
- Web 服务：Gunicorn + Nginx
- 前端：Django Templates、项目本地 static 资源、Tabulator
- 文件处理：PDF、图片、HTML、TXT、DOCX、XLSX 作业题源导入
- 外部服务：DashScope/Qwen、Volcengine Ark Vision、微信小程序手机号能力

## 3. 目录结构

```text
codemaster_system/
  manage.py
  requirements.txt
  Dockerfile
  docker-compose.yml
  docker-compose.prod.yml
  .env.example
  codemaster_system/
    settings.py
    settings_sqlite.py
    urls.py
    wsgi.py
    asgi.py
  entry/
    models.py
    urls.py
    views.py
    auth.py
    portal_context.py
    student_learning_api.py
    homework_completion_stats.py
    homework_online.py
    homework_batch.py
    student_import.py
    question_queries.py
    templates/entry/
    static/entry/
    migrations/
    management/commands/
    tests/
  deploy/
    nginx/codemaster.conf
    scripts/deploy.sh
    scripts/backup_db.sh
    scripts/restore_db.sh
    scripts/check_prod.sh
    docs/
  docs/generated/current_system_docs/
  media/
  staticfiles/
  project_inputs/
```

核心 Django app 只有 `entry`。`settings.py` 的 `INSTALLED_APPS` 仅启用了 `django.contrib.staticfiles` 和 `entry.apps.EntryConfig`，没有启用 `django.contrib.auth`、`django.contrib.admin`、`sessions` 或 DRF。

## 4. Docker 启动方式

开发/验证 compose：`docker-compose.yml`

- `db`：`postgres:15`，读取 `.env`，数据卷 `postgres_data:/var/lib/postgresql/data`，带 `pg_isready` healthcheck。
- `web`：当前目录构建镜像，读取 `.env`，等待 `db` healthy，暴露容器内 `8000`，挂载 `staticfiles` 和 `media` 卷，运行 Gunicorn。
- `nginx`：`nginx:1.27-alpine`，监听宿主机 `80`，代理到 `web:8000`，直接服务 `/static/` 与 `/media/`。

生产 compose：`docker-compose.prod.yml`

- PostgreSQL 数据挂载到 `/opt/codemaster/postgres`
- 静态文件挂载到 `/opt/codemaster/staticfiles`
- 用户上传挂载到 `/opt/codemaster/media`
- Nginx 监听 `80` 和 `443`，额外挂载 `/etc/letsencrypt`

部署脚本 `deploy/scripts/deploy.sh` 会按顺序执行：构建镜像、启动数据库、等待健康检查、`manage.py check`、`migrate`、`collectstatic`、启动全量服务。

## 5. PostgreSQL 配置、数据卷和迁移

数据库连接在 `codemaster_system/settings.py`：

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": POSTGRES_DB,
        "USER": POSTGRES_USER,
        "PASSWORD": POSTGRES_PASSWORD,
        "HOST": POSTGRES_HOST,
        "PORT": POSTGRES_PORT,
        "CONN_MAX_AGE": POSTGRES_CONN_MAX_AGE,
    }
}
```

环境变量来自 `.env`，模板见 `.env.example`。默认容器内 `POSTGRES_HOST=db`，端口 `5432`。

迁移方式：

- 本地：`.venv/bin/python manage.py migrate`
- Docker：`docker compose run --rm web python manage.py migrate`
- 生产：`docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate`

备份/恢复：

- 备份脚本使用 `pg_dump -Fc --no-owner --no-acl`
- 恢复脚本使用 `pg_restore --clean --if-exists --no-owner --no-acl`
- 不要执行 `docker compose down -v`，它会删除本地 compose volume。

## 6. Django Settings 配置

重要配置点：

- `load_env_file(BASE_DIR / ".env")`：启动时手动加载 `.env`
- `DEBUG`：`DJANGO_DEBUG`
- `SECRET_KEY`：生产必须配置 `DJANGO_SECRET_KEY`
- `ALLOWED_HOSTS`：`DJANGO_ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`：`DJANGO_CSRF_TRUSTED_ORIGINS`
- HTTPS 安全：`SECURE_SSL_REDIRECT`、HSTS、`CSRF_COOKIE_SECURE`
- Cookie 安全：`CODEMASTER_AUTH_COOKIE_SECURE`
- 静态和媒体：`DJANGO_STATIC_ROOT`、`DJANGO_MEDIA_ROOT`
- 小程序：`WECHAT_MINIAPP_APPID`、`WECHAT_MINIAPP_SECRET`
- 作业导入 AI/OCR：DashScope 和 Volcengine Ark 相关环境变量

备用 `codemaster_system/settings_sqlite.py` 继承主 settings，但把数据库改成 `test.sqlite3`，并使用 MD5 password hasher，适合本地测试。

## 7. App 与模块用途

`entry` 是唯一业务 app，内部按模块拆分：

- `models.py`：账号、学生、课程、题库、作业、作业提交、统计、教师记录等表。
- `auth.py`：自定义 PortalUser 登录、签名 token、cookie、角色装饰器。
- `urls.py`：全部业务路由。
- `views.py`：登录、小程序 API、学生/家长/教师/校长页面、作业和题源上传。
- `portal_context.py`：模板上下文、页面组装、序列化、权限过滤、作业展示逻辑。
- `student_learning_api.py`：小程序/校长端/家长端使用的学生学习统计 JSON。
- `homework_completion_stats.py`：按 week/month/quarter 统计作业完成情况。
- `homework_online.py`：题源文件识别、OCR/LLM 解析、候选题确认、在线作答判分。
- `homework_batch.py`：公共题源/导入任务预览、批量作业复制。
- `student_import.py`：学生、家长账号和教师 assignment 导入。
- `question_queries.py`：题库 `questions` 表查询和序列化。
- `content_visibility.py`：C1-C4 内容权限等级、GESP/CSP 可见性规则。
- `course_identity.py`：课程 slug 和等级标准化。
- `gesp2_catalog.py` / `gesp4_catalog.py`：静态专题定义和 fallback。

## 8. 用户体系与权限体系

系统不使用 Django 内置 `User`/`Group`/`Permission`。核心账号是 `PortalUser`，角色包括：

- `student`：学生
- `parent`：家长
- `teacher`：教师
- `principal`：校长

权限控制主要靠两个装饰器：

- `role_required(role)`：HTML 页面，未登录跳登录页，角色不匹配跳回自身 landing。
- `api_role_required(role)`：JSON API，未登录返回 401，角色不匹配返回 403。

认证方式：

- HTML 登录成功后写入 `codemaster_auth` HttpOnly cookie。
- 小程序登录返回 `token`，也会同时写 cookie。
- JSON API 可用 `Authorization: Codemaster <token>` 自动登录。

`admin` 不是当前代码里的正式角色。项目没有启用 Django admin，也没有 `ROLE_ADMIN`。测试里出现“管理员老师”是一个 teacher 账号，不是独立 admin 权限模型。

## 9. 端侧功能

学生端：

- 课程入口：C++、GESP2、GESP4 等目录。
- 内容访问：按 `StudentContentAccess` 和 C1-C4 权限决定可见。
- 作业列表、作业详情、知识点任务标记完成。
- 在线单选题作答、自动判分、历史提交查看。
- 作业打印、错题打印、空白卷打印。
- 学生账号设置。

家长端：

- 学生档案入口。
- 查看绑定孩子的作业列表、作业详情、提交详情。
- 查看周总结/课后总结。
- 打印作业、错题、空白卷。
- 小程序 API 获取孩子学习数据和 week/month/quarter 作业统计。

教师端：

- 教师工作台：学生、课程、作业统计 tabs。
- 管理名下学生和教师-学生 assignment。
- 学生详情：内容权限、教师评价、奖励、课时、作业布置、作业评语、周总结。
- 课程结构：课程、分类、Level、知识点增删改查、导出。
- 知识点权限：给学生开关内容访问。
- 作业：单个布置、批量布置、题源上传、候选题确认、在线题生成。
- 作业统计：周/月/季度完成情况、正确率、课堂反馈编辑。

校长端：

- `/principal/dashboard` 提供校区概览壳。
- 小程序/JSON API `/api/principal/get_students_info` 获取全部学生学习统计，包含教师字段。

## 10. 核心文件

继续开发必须优先理解：

- `codemaster_system/settings.py`
- `docker-compose.yml`
- `docker-compose.prod.yml`
- `deploy/nginx/codemaster.conf`
- `entry/models.py`
- `entry/urls.py`
- `entry/views.py`
- `entry/auth.py`
- `entry/student_learning_api.py`
- `entry/homework_completion_stats.py`
- `entry/portal_context.py`
- `entry/homework_online.py`
- `entry/homework_batch.py`
- `entry/student_import.py`
- `entry/templates/entry/`
- `entry/static/entry/`
- `entry/tests/`

## 11. 历史遗留或可疑文件

- `db.sqlite3`、`test.sqlite3`：本地 SQLite 文件，不是主数据库；已在 `.gitignore` 中。
- `project_inputs/`：题源、PDF、导入样例等本地输入；已在 `.gitignore` 中。
- `.tmp_quicklook/`：macOS 预览临时目录；已忽略。
- `media/`：用户上传文件，不能提交到 Git，部署时必须跟数据库一起迁移。
- `staticfiles/`：`collectstatic` 产物，不能当源文件维护。
- `docs/generated/current_system_docs/`：已有自动生成文档包，生成时间是 2026-05-08，可参考但不是源码。
- `CODEMASTER_SYSTEM_OVERVIEW.md`：旧总览文档，内容可能落后于当前小程序和作业统计接口。
- `entry/shell_content.py` 与 `entry/student_portal_content.py` 中仍有较多占位文案和预留课程入口。
- `entry/views.py` 已超过 3600 行，承担过多职责，是后续重构重点。
- 作业导入解析目前在 HTTP 请求内同步执行，慢外部 API 会占用 Gunicorn worker。

## 12. 小程序继续开发优先文件

优先改这些文件：

1. `entry/views.py`：新增小程序 API、登录、绑定、路由入口。
2. `entry/urls.py`：注册小程序路由。
3. `entry/auth.py`：token/cookie/角色校验逻辑。
4. `entry/student_learning_api.py`：学生数据、周/月/季度统计 JSON。
5. `entry/models.py`：如需 openid、绑定关系、通知订阅，需要加字段/表。
6. `entry/tests/test_miniapp_auth_api.py`、`entry/tests/test_student_learning_api.py`：补小程序接口测试。
7. `codemaster_system/settings.py`、`.env.example`：新增微信配置或接口开关。

当前最缺的是“家长主动绑定孩子/手机号/openid”的模型和接口。现在只有“手机号匹配已存在家长账号”的登录能力，没有正式绑定流程。
