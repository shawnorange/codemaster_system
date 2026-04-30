# CodeMaster System 项目总览

最后整理时间：2026-04-30  
整理依据：当前仓库 `/Users/apple/coding/python/codemaster_system` 的真实代码、模型、URL、配置与模板结构。

## 1. 项目是什么

`codemaster_system` 是一个基于 Django 的教学管理系统，当前更接近“单校区、本地优先”的教学执行平台，而不是通用型 SaaS 教务系统。

当前主线能力集中在：

- 多角色门户：学生、家长、教师、校长
- 教师工作台
- 课程结构与知识点管理
- 学生内容开放权限
- 作业布置、导入、提交、判分、统计
- GESP / C++ 内容页与题库承接

当前主要业务 App 只有一个：`entry`

## 2. 当前技术框架

### 2.1 后端框架

- Python 3
- Django `>=5.2,<5.3`

项目入口：

- `manage.py`
- Django settings：`codemaster_system/settings.py`
- 根路由：`codemaster_system/urls.py`
- 业务路由：`entry/urls.py`

### 2.2 前端形态

不是前后端分离项目，当前是：

- Django 模板渲染
- 本地静态资源
- 少量原生 JS
- Tabulator 作为教师端 datagrid
- ECharts 作为图表

Tabulator 本地资源：

- `entry/static/vendor/tabulator/6.3.1/tabulator.min.js`
- `entry/static/vendor/tabulator/6.3.1/tabulator.min.css`

共享 datagrid 入口：

- `entry/templates/entry/includes/teacher_tabulator_head.html`
- `entry/templates/entry/includes/teacher_tabulator_scripts.html`
- `entry/static/entry/js/teacher_tabulator.js`

### 2.3 当前依赖

来自 `requirements.txt`：

- Django
- psycopg2-binary
- pypdf
- pypdfium2
- Pillow
- requests
- certifi

这说明项目当前还包含：

- PDF 解析 / OCR 前处理
- 作业导入
- 图片处理
- 外部模型 / OCR API 调用

## 3. 当前运行方式

### 3.1 默认数据库

默认 settings 使用 PostgreSQL：

- 引擎：`django.db.backends.postgresql`
- 配置来源：`.env`

关键环境变量：

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`

### 3.2 SQLite 备用配置

项目还保留了一个 SQLite settings：

- `codemaster_system/settings_sqlite.py`

主要用途：

- 本地轻量测试
- 使用 `test.sqlite3`
- 密码哈希切换为 `MD5PasswordHasher`

### 3.3 时区与本地化

- `LANGUAGE_CODE = "zh-hans"`
- `TIME_ZONE = "Asia/Shanghai"`
- `USE_TZ = True`

### 3.4 静态与媒体文件

- `STATIC_URL = "/static/"`
- `STATIC_ROOT = BASE_DIR / "staticfiles"` 或环境变量指定
- `MEDIA_URL = "/media/"`
- `MEDIA_ROOT = BASE_DIR / "media"`

`DEBUG=True` 时，根 URL 会直接托管 `/media/`。

## 4. 当前 Django 架构特点

这个项目不是典型的“Django 全家桶”。

### 4.1 已启用的 app 很少

`INSTALLED_APPS` 当前只有：

- `django.contrib.staticfiles`
- `entry.apps.EntryConfig`

没有启用：

- `django.contrib.admin`
- `django.contrib.auth`
- `django.contrib.sessions`
- `django.contrib.messages`

### 4.2 中间件也很轻

当前只启用了：

- `SecurityMiddleware`
- `CommonMiddleware`
- `CsrfViewMiddleware`
- `XFrameOptionsMiddleware`

没有启用 Django 原生 session / auth middleware。

### 4.3 认证是自定义实现

当前登录体系不走 Django `User` / Session。

而是：

- 账号表：`PortalUser`
- 登录后写签名 Cookie：`codemaster_auth`
- 通过 `entry/auth.py` 中的 `role_required(...)` 做角色校验

这意味着：

- 角色体系完全自定义
- 页面的身份切换是 portal 风格
- 不是 admin 后台风格

## 5. 当前目录结构

### 5.1 核心目录

- `codemaster_system/`
  Django 项目配置
- `entry/`
  当前唯一核心业务应用
- `entry/templates/entry/`
  所有页面模板
- `entry/static/entry/`
  样式、脚本、图片
- `entry/topic_content/`
  具体专题内容与站点数据
- `entry/migrations/`
  数据库迁移
- `entry/tests/`
  自动化测试
- `entry/management/commands/`
  管理命令

### 5.2 重要模块

- `entry/models.py`
  核心数据模型
- `entry/views.py`
  视图入口
- `entry/portal_context.py`
  页面上下文构建中心，当前是非常关键的业务拼装层
- `entry/auth.py`
  登录、Cookie、角色权限
- `entry/homework_online.py`
  作业导入、判题、外部模型交互
- `entry/homework_batch.py`
  批量作业相关逻辑
- `entry/content_visibility.py`
  知识点开放权限逻辑

## 6. 当前路由框架

根路由：

- `codemaster_system/urls.py` 只做一件事：`include("entry.urls")`

业务路由分四大角色：

- 学生：`/student/...`
- 家长：`/parent/...`
- 教师：`/teacher/...`
- 校长：`/principal/...`

教师端当前主要区域：

- `/teacher/students`
- `/teacher/homework-stats`
- `/teacher/homework/batch-create`
- `/teacher/students/<student_id>`
- `/teacher/courses/...`

当前 Homework Stats 已经演进出三级查看链路：

1. `Teacher Homework Stats`
   `/teacher/homework-stats`
2. `Homework Submission Detail`
   `/teacher/homework-stats/submissions?student_id=...&period=week`
3. `Homework Submission Answer Detail`
   `/teacher/homework-stats/submission-answer-detail?submission_id=...`

## 7. 当前数据库与核心实体

下面以“模型 -> 表 -> 用途”的方式整理。

说明：

- 除 `Question` 以外，大多数模型使用 Django 默认表名
- 默认表名规则通常是：`entry_<modelname>`

### 7.1 账号与角色

#### `PortalUser`

- 表名：`entry_portaluser`
- 用途：系统统一账号表
- 关键字段：
  - `username`
  - `password`
  - `role`
  - `full_name`
  - `phone`
  - `is_active`

角色值：

- `student`
- `parent`
- `teacher`
- `principal`

这是整个门户系统的身份根表。

### 7.2 学生主档

#### `Student`

- 表名：`entry_student`
- 用途：学生业务主档
- 关键字段：
  - `user` -> `PortalUser`
  - `parent_user` -> `PortalUser`
  - `teacher_user` -> `PortalUser`
  - `display_name`
  - `grade`
  - `campus`
  - `primary_course_name`
  - `primary_track_name`
  - `primary_level_name`

说明：

- `Student.teacher_user` 当前仍然大量用于“当前教师名下学生”的直接筛选
- 但更细粒度的“教师-学生-课程-级别”关系，已经落到了 `TeacherStudentAssignment`

### 7.3 课程结构

#### `Course`

- 表名：`entry_course`
- 用途：课程方向，如 `cpp`

关键字段：

- `slug`
- `title`
- `summary`

#### `CourseCategory`

- 表名：`entry_coursecategory`
- 用途：课程下一级分类，如 GESP / CSP / 机器人编程

关键字段：

- `course`
- `slug`
- `title`
- `summary`
- `sort_order`
- `is_active`

#### `CourseLevel`

- 表名：`entry_courselevel`
- 用途：分类下的级别，如 GESP1 ~ GESP8

关键字段：

- `category`
- `code`
- `title`
- `summary`
- `sort_order`
- `is_active`

#### `CourseContent`

- 表名：`entry_coursecontent`
- 用途：具体知识点 / 专题 / 教学页入口

关键字段：

- `course`
- `level`
- `content_type`
- `slug`
- `title`
- `phase`
- `permission_code`
- `route_path`
- `summary`
- `has_real_content`
- `is_active`

这张表是当前内容系统和教学页接入的核心承接表。

### 7.4 题库

#### `Question`

- 表名：`questions`
- 用途：独立题库表，不走默认表名

关键字段：

- `code`
- `content_slug`
- `level_code`
- `question_type`
- `source_year`
- `source_month`
- `source_question_no`
- `title`
- `payload`
- `sort_order`
- `is_active`
- `is_demo`

说明：

- 这是通用题库，不等于作业提交时的在线题目表
- 作业系统内部的题目是 `HomeworkQuestion`

### 7.5 学生内容开放记录

#### `StudentContentAccess`

- 表名：`entry_studentcontentaccess`
- 用途：控制某学生对某知识点 / 内容是否开放

关键字段：

- `student`
- `content`
- `is_open`
- `granted_by`
- `granted_at`
- `updated_at`

### 7.6 教师-学生负责关系

#### `TeacherStudentAssignment`

- 表名：`entry_teacherstudentassignment`
- 用途：教师负责哪些学生、哪个课程、哪个级别

关键字段：

- `teacher`
- `student`
- `course`
- `level_code`
- `is_active`
- `assigned_at`
- `updated_at`

这是当前教师工作台、学生池、分组、级别筛选的重要关系表。

### 7.7 作业主链路

#### `HomeworkAssignment`

- 表名：`entry_homeworkassignment`
- 用途：一次布置给某学生的作业

关键字段：

- `teacher`
- `student`
- `content`
- `title`
- `description`
- `due_date`
- `status`
- `teacher_comment`
- `summary`
- `source_import_job`
- `assigned_at`
- `completed_at`
- `reviewed_at`
- `is_active`
- `created_at`
- `updated_at`

状态值：

- `assigned`
- `completed`
- `reviewed`
- `cancelled`

说明：

- 统计周期很多地方按 `created_at` 判断
- 教师端 Homework Stats 当前明确依赖 `created_at`

#### `HomeworkSummary`

- 表名：`entry_homeworksummary`
- 用途：作业周总结 / 总结 HTML

关键字段：

- `title`
- `summary_html`
- `created_by`
- `created_at`
- `updated_at`

#### `HomeworkImportJob`

- 表名：`entry_homeworkimportjob`
- 用途：作业导入任务

关键字段：

- `teacher`
- `assignment`
- `source_file`
- `source_filename`
- `source_sha256`
- `source_type`
- `parse_status`
- `candidates_json`
- `parse_notes`
- `confirmed_at`
- `is_active`
- `created_at`
- `updated_at`

重要说明：

- 当前很多“知识点名称”都是从这里来：
  `HomeworkAssignment.source_import_job -> HomeworkImportJob.source_filename`
- 页面展示时通常会去掉 `.txt` / `.xlsx`

#### `HomeworkQuestion`

- 表名：`entry_homeworkquestion`
- 用途：某份作业里的在线题目

关键字段：

- `assignment`
- `import_job`
- `question_no`
- `question_type`
- `stem`
- `options_json`
- `correct_answer`
- `analysis`
- `source_snapshot_json`
- `is_active`

说明：

- 一份作业可能有 direct questions
- 如果 direct questions 为空，也可能回退到 `source_import_job.questions`

#### `HomeworkSubmission`

- 表名：`entry_homeworksubmission`
- 用途：学生对某份作业的一次提交记录

关键字段：

- `assignment`
- `student`
- `status`
- `total_count`
- `correct_count`
- `wrong_count`
- `score`
- `started_at`
- `submitted_at`
- `checked_at`
- `is_active`
- `created_at`
- `updated_at`

状态值：

- `in_progress`
- `submitted`
- `auto_checked`
- `reviewed`

说明：

- 当前已经支持同一作业多次提交
- Homework Stats 的“本周提交 X 条”现在统计的是这张表的记录数

#### `HomeworkSubmissionAnswer`

- 表名：`entry_homeworksubmissionanswer`
- 用途：某一条 `HomeworkSubmission` 的逐题作答明细

关键字段：

- `submission`
- `homework_question`
- `selected_answer`
- `is_correct`
- `correct_answer_snapshot`
- `analysis_snapshot`
- `created_at`
- `updated_at`

说明：

- 这是三级详情页 `Homework Submission Answer Detail` 的核心数据来源

### 7.8 教学记录类数据

#### `TeacherEvaluation`

- 表名：`entry_teacherevaluation`
- 用途：教师评价记录

关键字段：

- `student`
- `teacher`
- `evaluation_text`
- `created_at`

#### `RewardRecord`

- 表名：`entry_rewardrecord`
- 用途：奖励记录

关键字段：

- `student`
- `teacher`
- `reward_text`
- `created_at`

#### `LessonHourLedger`

- 表名：`entry_lessonhourledger`
- 用途：课时变动流水

关键字段：

- `student`
- `teacher`
- `delta_hours`
- `note`
- `created_at`

## 8. 当前最重要的实体关系

### 8.1 登录与角色

`PortalUser`

- 1:1 `Student.user`
- 1:n `Student.parent_user`
- 1:n `Student.teacher_user`

### 8.2 教师负责关系

`PortalUser(teacher)` -> `TeacherStudentAssignment` -> `Student`

并附带：

- `course`
- `level_code`

### 8.3 课程结构

`Course`
-> `CourseCategory`
-> `CourseLevel`
-> `CourseContent`

### 8.4 作业主链路

`PortalUser(teacher)`
-> `HomeworkAssignment`
-> `HomeworkSubmission`
-> `HomeworkSubmissionAnswer`

同时：

`HomeworkAssignment`
-> `HomeworkImportJob`
-> `HomeworkQuestion`

### 8.5 当前知识点名称链路

教师端统计和详情页中，知识点通常不从 `HomeworkSubmissionAnswer` 反推，而是直接来自：

`HomeworkSubmission.assignment`
-> `HomeworkAssignment.source_import_job`
-> `HomeworkImportJob.source_filename`

这是当前 Homework Stats 页面最重要的知识点来源链路。

## 9. 当前页面和数据的关键实现方式

### 9.1 页面上下文集中在 `portal_context.py`

当前不是“每个 view 自己直接拼全部数据”。

而是：

- `views.py` 负责接请求、做权限校验
- `portal_context.py` 负责构建页面上下文

这意味着：

- 页面逻辑大多集中在 `portal_context.py`
- 查统计、查关系、拼表格数据，基本都在这里做

### 9.2 教师端 datagrid

当前教师端表格几乎统一走 Tabulator。

重要 JS：

- `entry/static/entry/js/teacher_tabulator.js`

当前已经覆盖：

- Teacher Workbench 学生表
- 课程表
- Homework Stats Student Detail
- Homework Submission Detail
- Homework Submission Answer Detail
- 学生关系管理
- 课程学生列表
- 学生池
- Knowledge Point / Level Grid

### 9.3 Homework Stats 当前结构

当前 `Teacher Homework Stats` 页主要包含：

- summary 总览卡片
- Done Top 10
- Missing Top 10
- Student Detail

week 下 Student Detail 还会带：

- `知识点`
- `Homework Submission Detail`

并进入两级下钻：

1. HomeworkSubmission 列表
2. HomeworkSubmissionAnswer 题目详情

## 10. 当前认证与安全边界

### 10.1 登录态

当前登录依赖：

- 自定义签名 Cookie
- `PortalUser`
- `role_required`

### 10.2 页面权限方式

教师端通常通过两层限制：

1. `request.codemaster_user` 的 role 必须是 `teacher`
2. 查询数据时继续限制为“当前教师自己的学生 / 作业 / 提交”

例如 Homework Stats 相关页会限制：

- `Student.teacher_user = 当前教师`
- `assignment.teacher = 当前教师`
- `submission.student_id = assignment.student_id`
- `submission_id` 必须属于当前教师学生

## 11. 当前管理命令

`entry/management/commands/` 下当前有：

- `import_cpp_students.py`
- `import_questions.py`
- `normalize_student_usernames.py`
- `repair_enumeration_pdf_import.py`

说明当前项目已经把：

- 学生导入
- 题库导入
- 数据修复

都纳入了 Django management command 体系。

## 12. 当前数据库迁移状态

当前迁移已经至少演进到：

- `0023_homeworkassignment_source_import_job.py`

说明项目已经经历过多轮结构演化，尤其是：

- 教师学生负责关系
- 作业系统
- 在线题目
- 多次提交
- 作业总结
- 导入链路

都不是初始版本。

## 13. 当前项目边界与真实定位

按当前代码看，这个项目的定位更准确地说是：

- 一个以教师工作流为中心的教学执行系统
- 一个以课程结构 + 学生权限 + 作业链路为核心的数据驱动平台
- 一个以 Django 模板为主、局部 JS 强化交互的业务系统

它当前不是：

- 通用 CMS
- SaaS 多租户平台
- Django Admin 型后台
- 纯前后端分离平台

## 14. 如果要继续接手开发，优先看哪些文件

建议顺序：

1. `README.md`
2. `codemaster_system/settings.py`
3. `entry/models.py`
4. `entry/auth.py`
5. `entry/urls.py`
6. `entry/views.py`
7. `entry/portal_context.py`
8. `entry/static/entry/js/teacher_tabulator.js`
9. `entry/templates/entry/teacher_homework_stats.html`
10. `entry/tests/test_teacher_homework_stats.py`

## 15. 当前一句话总结

当前 `codemaster_system` 是一个基于 Django 5.2 + PostgreSQL 的教学管理系统，采用自定义 PortalUser + 签名 Cookie 认证，围绕教师工作台、课程结构、学生权限和作业系统组织数据；核心业务关系集中在 `Student`、`TeacherStudentAssignment`、`Course*`、`HomeworkAssignment`、`HomeworkSubmission`、`HomeworkSubmissionAnswer` 这几条主链上。
