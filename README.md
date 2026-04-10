# CodeMaster 教学系统 MVP

单校区、本地优先开发的教学系统 MVP。当前项目围绕学生、教师、家长、校长四类角色打通最小闭环，优先承接 C++ / GESP 内容体系，以及教师日常教学执行工作流。

## 当前定位

- 单校区、本地优先开发，不追求一开始就做成多校区 SaaS。
- 当前先围绕真实教学执行链路收口：登录、角色分流、教师工作台、课程结构、学生权限、教学页接入。
- 主线课程是 C++ / GESP。
- 当前数据库以 PostgreSQL 为准，教师端和课程结构都按数据库驱动，静态内容只做兜底。

## 当前已落地的能力

### 统一登录与角色分流

- 已有统一登录页，按账号角色进入学生 / 家长 / 教师 / 校长页面。
- 当前角色主入口：
  - 学生：课程入口与内容页
  - 家长：学生档案、评价、奖励、课时
  - 教师：Teacher Workbench
  - 校长：最小概览页

### 教师工作台

- Teacher Workbench 已按两条工作流组织：
  - 学生工作流
  - 课程工作流
- 教师端已支持：
  - 查看自己名下学生
  - 查看自己负责课程方向
  - 学生关系管理
  - 课程分类、Level、Knowledge Point 浏览
  - 知识点权限管理
  - Teaching Pages 接入
  - 当前课程结构导出

### 教师-学生关系

- 已使用 `TeacherStudentAssignment` 作为教师与学生的主关系来源。
- 支持：
  - 一个老师对应多个学生
  - 一个学生对应多个老师
  - 同一学生按不同课程 / 级别建立多条 assignment
- 教师端当前只允许维护“自己的 assignment”，不改学生主档，不做跨老师全局分配。

### 课程结构

- 已落地课程结构主链路：
  - `Course`
  - `CourseCategory`
  - `CourseLevel`
  - `CourseContent`
- 当前教师课程工作流已经支持三级结构：
  - Course Categories
  - Level data-grid
  - Knowledge Point data-grid
- 当前已在 `cpp` 课程下落地三大分类：
  - GESP
  - CSP
  - 机器人编程
- 当前已落地 `GESP1 ~ GESP8` 的 Level 主数据。

### questions 题库

- 已新增 `questions` 单表题库模型。
- 支持通过 `import_questions` 管理命令从 JSON 导入题目。
- 当前题目查询模式是：
  - 数据库优先
  - 静态 fallback 兜底

### 教师端 datagrid

- 教师端的 CRUD / 列表型页面已统一优先使用 Tabulator。
- 当前已覆盖的主要页面包括：
  - 教师工作台学生列表
  - 教师工作台课程列表
  - 学生关系管理列表
  - 课程学生列表
  - 学生池
  - Level Grid
  - Knowledge Point Grid
  - 知识点学生权限管理页
- Tabulator 的 CSS / JS 均走项目本地 static 文件，不依赖远程 CDN。

### GESP2 / GESP4 内容接入

- GESP2 当前已接入：
  - 枚举法
  - ASCII 编码
- GESP4 当前已接入真实教师教学页或内容页的专题包括：
  - 二维数组专题
  - 二分查找专题
  - 排序专题
  - 字符串专题
- 其它未完全接入的知识点 / 专题仍可通过数据库主数据与占位页继续承接。

### 数据库优先、静态兜底

- 当前内容页和题目页优先读取 PostgreSQL 中的真实结构与题目。
- 当数据库数据不足时，使用静态 JSON / 站点数据做 fallback。
- 这套模式已用于：
  - GESP2 枚举法
  - GESP2 ASCII 编码
  - GESP4 专题页

## 当前项目边界

当前项目还不是以下系统：

- 不是完整 CMS
- 不是完整作业系统
- 不是完整教务后台
- 不是复杂 RBAC 权限平台

当前明确未展开为完整子系统的方向包括：

- Scratch
- 无人机
- AI
- 3D 打印
- 更完整的教学运营后台

当前优先级仍然是：

- 内容型课程体系
- 教师工作流
- 学生 / 家长 / 教师 / 校长四类基础闭环

## 技术栈与运行方式

### 技术栈

- Python
- Django 5.2
- PostgreSQL
- Django 模板页
- Tabulator（本地静态资源）

依赖文件见：

- `requirements.txt`

### 数据库

当前默认数据库是 PostgreSQL，不再以 sqlite 作为主运行数据库。

配置来自 `.env`：

```env
POSTGRES_DB=codemaster_system
POSTGRES_USER=appuser
POSTGRES_PASSWORD=your_password
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

本地开发当前通常使用 Docker 中的 PostgreSQL：

- 容器名：`local-pg`
- 端口：`5432`

### 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 常用命令

应用 migration：

```bash
python manage.py migrate
```

启动开发服务：

```bash
python manage.py runserver
```

导入题库 JSON：

```bash
python manage.py import_questions path/to/questions.json
```

仅校验导入但不落库：

```bash
python manage.py import_questions path/to/questions.json --dry-run
```

导入学生数据：

```bash
python manage.py import_cpp_students path/to/student.xlsx
```

运行测试：

```bash
python manage.py test entry.tests
```

### Tabulator 本地静态资源

Tabulator 已 vendored 到项目本地：

- `entry/static/vendor/tabulator/6.3.1/tabulator.min.css`
- `entry/static/vendor/tabulator/6.3.1/tabulator.min.js`

模板统一通过本地 static 引用：

- `entry/templates/entry/includes/teacher_tabulator_head.html`
- `entry/templates/entry/includes/teacher_tabulator_scripts.html`

不依赖远程 CDN。

## 当前开发原则

- PostgreSQL 优先
- 本地先跑通
- 教师端先做教学执行层，不扩成全局管理后台
- 能用数据库表达的结构，优先数据库驱动
- 前端以 Django 模板页为主，必要时用成熟组件增强，不重构成前后端分离

## 当前仓库说明

- 代码目录：`entry/`
- 项目配置：`codemaster_system/`
- 当前有意忽略本地输入 / 临时目录：
  - `project_inputs/`
  - `.tmp_quicklook/`
  - `.DS_Store`

这些文件不属于正式版本内容，不应随推送进入仓库。
