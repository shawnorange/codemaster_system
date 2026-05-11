# 下一步开发建议

生成日期：2026-05-11

## 1. 最高优先级：补齐小程序身份与绑定

当前小程序登录只靠 `PortalUser.phone` 找家长账号，没有 openid/unionid，也没有正式绑定流程。建议新增：

- `MiniappIdentity` 或在 `PortalUser` 增加小程序身份字段。
- 字段建议：`portal_user`、`openid`、`unionid`、`phone`、`bound_at`、`last_login_at`、`is_active`。
- 家长绑定孩子接口：通过学生码、邀请码或老师生成的绑定码完成。
- 解绑/换绑接口和审计记录。

优先文件：

- `entry/models.py`
- `entry/views.py`
- `entry/urls.py`
- `entry/auth.py`
- `entry/tests/test_miniapp_auth_api.py`
- `.env.example`

## 2. 把小程序 API 从大 views 中拆出来

`entry/views.py` 已经超过 3600 行，继续加接口会很快失控。建议新增：

```text
entry/miniapp_api.py
entry/miniapp_auth.py
entry/miniapp_serializers.py
```

然后 `entry/urls.py` 只导入这些模块的 view。这样小程序接口、HTML 页面和教师端上传逻辑可以分离演进。

## 3. 固化小程序响应 schema

现在没有 DRF serializer，响应 dict 分散在 `student_learning_api.py` 和 `portal_context.py`。建议先不引入 DRF，先做轻量 schema 函数：

- `serialize_miniapp_user()`
- `serialize_student_learning_row()`
- `serialize_homework_summary()`
- `serialize_homework_assignment_for_miniapp()`
- `serialize_submission_for_miniapp()`

优先文件：

- `entry/student_learning_api.py`
- 新增 `entry/miniapp_serializers.py`
- `entry/tests/test_student_learning_api.py`

## 4. 新增小程序作业与报告接口

当前小程序只能拿聚合统计，不能拿作业详情。建议新增：

- `GET /api/parent/children/<student_id>/homework`
- `GET /api/parent/children/<student_id>/homework/<assignment_id>`
- `GET /api/parent/children/<student_id>/homework/<assignment_id>/submissions`
- `GET /api/parent/children/<student_id>/reports/week`
- `GET /api/parent/children/<student_id>/reports/month`

这些接口应复用现有模型和统计逻辑，不直接复用 HTML context。

## 5. 统一教师范围口径

系统同时存在：

- `Student.teacher_user`
- `TeacherStudentAssignment`

部分统计和权限用 `teacher_user`，课程/多等级关系用 `TeacherStudentAssignment`。后续会出现“一个学生多个老师”时统计漏数或越权风险。建议：

1. 明确 `Student.teacher_user` 是否只是主班主任/兼容字段。
2. 教师端列表、作业统计、权限管理统一改用 active `TeacherStudentAssignment`。
3. 小程序校长数据可以保留主教师字段，但最好同时输出负责关系列表。

## 6. 作业导入改为后台任务

当前 `parse_homework_import_job()` 在 HTTP 请求中同步执行。风险：

- 外部 OCR/LLM 慢时占用 Gunicorn worker。
- 超时后导入任务可能停留在 `parsing`。
- 用户上传体验不可控。

建议引入后台任务：

- 简单阶段：Django management command + cron/队列表轮询。
- 正式阶段：Celery/RQ + Redis。
- 页面上传后立即返回任务 ID，由前端轮询状态。

优先文件：

- `entry/homework_online.py`
- `entry/views.py`
- `entry/homework_batch.py`
- `deploy/`

## 7. 增加真正的管理后台或管理角色

用户提到 `admin`，但当前系统没有 admin 角色，也没有 Django admin。可选路线：

- 轻量路线：新增 `principal` 的管理页面，不启用 Django admin。
- 标准路线：启用 `django.contrib.admin`、`auth`、`sessions`，但需要迁移认证体系。
- 折中路线：保留 `PortalUser`，新增 `role=admin` 和内部管理页面。

不建议直接打开 Django admin 管理当前 `PortalUser.password`，除非先补好 ModelAdmin、密码设置表单和权限边界。

## 8. 清理历史与产物目录

建议保持这些文件不进入版本库：

- `db.sqlite3`
- `test.sqlite3`
- `media/`
- `staticfiles/`
- `project_inputs/`
- `.tmp_quicklook/`
- `.env`

已有 `.gitignore` 覆盖这些路径。继续开发时不要把本地数据、上传文件、PDF 题源或密钥混入提交。

## 9. 文档与测试优先级

下一轮建议补：

- 小程序 API contract tests。
- 手机号冲突、解绑、换绑测试。
- 多教师负责关系下的数据隔离测试。
- 作业统计 week/month/quarter 边界测试。
- Docker 启动 smoke test。

当前已经有：

- `entry/tests/test_miniapp_auth_api.py`
- `entry/tests/test_student_learning_api.py`
- `entry/tests/test_teacher_homework_stats.py`
- `entry/tests/test_homework_online_choice.py`

继续开发小程序前，应先扩展前两组测试。
