# 小程序接口对接说明

生成日期：2026-05-11  
相关文件：`entry/views.py`、`entry/auth.py`、`entry/student_learning_api.py`、`codemaster_system/settings.py`

## 1. 当前小程序能力边界

已实现：

- 账号密码登录：家长、校长。
- 微信手机号登录：家长。
- token 自动登录：`Authorization: Codemaster <token>`。
- 家长获取自己孩子数据。
- 校长获取全部学生数据。
- week/month/quarter 作业统计。
- week 课堂反馈、亮点、待提升点。

未实现：

- 微信 `openid` / `unionid` 存储。
- 家长主动绑定孩子接口。
- 家长手机号首次绑定流程。
- 学生小程序登录。
- 教师小程序登录。
- 独立周报/月报资源接口。
- 小程序端提交作业或查看作业详情的 JSON API。

## 2. 环境变量

`.env` 需要配置：

```env
WECHAT_MINIAPP_APPID=
WECHAT_MINIAPP_SECRET=
CODEMASTER_AUTH_COOKIE_SECURE=True
```

如果要使用微信手机号登录，服务器域名也需要在微信小程序后台配置合法 request 域名。

## 3. 登录接口

### 3.1 账号密码登录

`POST /api/miniapp/login`

请求：

```http
Content-Type: application/json
```

```json
{
  "username": "parent001",
  "password": "password"
}
```

成功：

```json
{
  "token": "signed-token",
  "user": {
    "id": "1",
    "username": "parent001",
    "full_name": "家长",
    "role": "parent",
    "role_label": "家长",
    "landing_url": "/parent/student-profile"
  }
}
```

限制：

- 只允许 `parent`、`principal`。
- `teacher` 和 `student` 即使密码正确也返回 403。

### 3.2 微信手机号登录

`POST /api/miniapp/login_by_phone`

请求：

```json
{
  "code": "微信 getPhoneNumber 返回的 code"
}
```

后端流程：

1. 调用 `https://api.weixin.qq.com/cgi-bin/token`。
2. 调用 `https://api.weixin.qq.com/wxa/business/getuserphonenumber`。
3. 从 `purePhoneNumber` 或 `phoneNumber` 取手机号。
4. 用 `normalize_phone()` 去掉非数字字符。
5. 在 `PortalUser` 里查找 `role=parent`、`is_active=True`、`phone` 匹配的账号。
6. 唯一匹配后签发 token。

错误响应：

| HTTP | error_code | 含义 |
|---|---|---|
| 400 | `missing_code` | 未提交 code |
| 400 | `invalid_phone_code` | 微信 code 无效或未返回手机号 |
| 403 | `phone_not_bound` | 手机号未绑定任何 active parent |
| 409 | `phone_conflict` | 多个 active parent 使用同一手机号 |
| 500 | `wechat_config_missing` | appid/secret 未配置 |
| 502 | `wechat_api_error` | 微信接口调用失败 |

## 4. 自动登录

小程序端保存登录返回的 `token`，后续请求带：

```http
Authorization: Codemaster <token>
```

后端会用 `entry.auth._resolve_authenticated_user_from_signed_payload()` 校验签名、过期时间和当前账号状态。token 有效期是 `AUTH_COOKIE_MAX_AGE = 8 小时`。

如果同时带 cookie，后端优先读取 Authorization header。

## 5. 家长绑定逻辑

当前没有“绑定接口”。所谓绑定来自数据库已有关系：

- `PortalUser.phone`：家长手机号。
- `Student.parent_user`：学生绑定家长账号。

手机号登录只解决“通过手机号找到家长账号”。孩子列表只通过：

```python
Student.objects.filter(parent_user=current_parent)
```

因此小程序继续开发时，需要补齐：

- `openid` / `unionid` 字段或独立 `MiniappIdentity` 表。
- 手机号首次绑定 parent 账号流程。
- 通过学生码/邀请码/老师确认绑定孩子的接口。
- 解绑、换绑、冲突处理和审计记录。

## 6. 家长获取孩子数据

`GET /api/parent/get_my_child`

鉴权：`parent`

Query：

```text
anchor_date=YYYY-MM-DD
```

不传时使用服务器当前日期。日期非法返回：

```json
{"error": "anchor_date 参数无效，应为 YYYY-MM-DD。"}
```

响应：

```json
{
  "anchor_date": "2026-05-04",
  "children": [
    {
      "student_id": 1,
      "display_name": "张三",
      "primary_level_name": "GESP4",
      "week": {
        "assignment_count": 3,
        "completed_count": 2,
        "on_time_completed_count": 1,
        "delayed_completed_count": 1,
        "incomplete_count": 1,
        "excluded_undated_count": 0,
        "lesson_feedbacks": [
          {
            "assignment_id": 10,
            "title": "课堂作业",
            "due_date": "2026-05-06T23:59:59+08:00",
            "source_import_job_id": 5,
            "highlights": "回答问题积极。",
            "areas_for_growth": "审题速度还可以更快。"
          }
        ],
        "highlights": ["回答问题积极。"],
        "areas_for_growth": ["审题速度还可以更快。"]
      },
      "month": {
        "assignment_count": 10,
        "completed_count": 8,
        "on_time_completed_count": 6,
        "delayed_completed_count": 2,
        "incomplete_count": 2,
        "excluded_undated_count": 0
      },
      "quarter": {},
      "knowledge_points_by_period": {
        "week": [
          {
            "name": "二维数组",
            "source": "题源文件名",
            "mastery_status": "基本掌握",
            "correct_rate": 0.8,
            "correct_rate_text": "80%",
            "assignment_id": 10
          }
        ],
        "month": [],
        "quarter": []
      }
    }
  ]
}
```

注意：家长接口不会返回 `teacher_id`、`teacher_name`。

## 7. 校长获取学生数据

`GET /api/principal/get_students_info`

鉴权：`principal`

响应：

```json
{
  "anchor_date": "2026-05-04",
  "periods": {
    "week": {"start": "2026-05-04", "end": "2026-05-10"},
    "month": {"start": "2026-05-01", "end": "2026-05-31"},
    "quarter": {"start": "2026-04-01", "end": "2026-06-30"}
  },
  "students": [
    {
      "student_id": 1,
      "display_name": "张三",
      "primary_level_name": "GESP4",
      "teacher_id": 2,
      "teacher_name": "王老师",
      "week": {},
      "month": {},
      "quarter": {},
      "knowledge_points_by_period": {}
    }
  ]
}
```

## 8. 作业统计口径

统计代码在 `entry/student_learning_api.py` 和 `entry/homework_completion_stats.py`。

周期：

- `week`：anchor_date 所在周，周一到周日。
- `month`：anchor_date 所在月。
- `quarter`：anchor_date 所在季度。

归属：

- 作业按 `HomeworkAssignment.due_date` 判断属于哪个周期。
- `assigned_at` 只用于排序和展示。
- `due_date` 为空的作业会累计到 `excluded_undated_count`。

完成：

- 在线题：存在 `submitted` / `auto_checked` / `reviewed` 状态提交即完成。
- 要求型：`HomeworkAssignment.status` 为 `completed` 或 `reviewed`，且有 `completed_at`。
- 小程序学生学习概览默认也可用 assignment status 判断完成。

掌握度：

- 无提交：`未作答`
- 错误率 0：`已掌握`
- 错误率 <= 30%：`基本掌握`
- 其他：`未掌握`

## 9. 周报、月报、课后总结

当前有三类相关能力：

1. 小程序 JSON 统计中的 `week` / `month` / `quarter`。
2. `week.lesson_feedbacks`、`week.highlights`、`week.areas_for_growth`。
3. HTML 页面中的 `HomeworkSummary.summary_html`。

现状：

- 小程序没有独立 `/weekly_report` 或 `/monthly_report` 接口。
- 月报只有统计指标，没有类似 `lesson_feedbacks` 的月度文本聚合。
- HTML 周总结可由学生/家长页面查看，但不是专门的小程序 JSON。

建议下一步新增：

- `GET /api/parent/children/<student_id>/reports/week`
- `GET /api/parent/children/<student_id>/reports/month`
- `GET /api/parent/children/<student_id>/homework`
- `GET /api/parent/children/<student_id>/homework/<assignment_id>`

## 10. 小程序端建议调用流程

1. 首次登录：
   - 优先使用微信手机号登录 `/api/miniapp/login_by_phone`
   - 如果失败且已有账号密码，可使用 `/api/miniapp/login`
2. 保存 token 到小程序本地 storage。
3. 启动时带 token 请求 `/api/parent/get_my_child` 或 `/api/principal/get_students_info`。
4. 如果返回 401，清理 token 并回到登录。
5. 如果返回 403，提示当前账号角色无权访问当前页面。

## 11. 后端继续开发优先点

- 增加小程序身份模型：`openid`、`unionid`、`session_key`、绑定时间、最后登录时间。
- 增加绑定/解绑接口，不再只依赖 `PortalUser.phone`。
- 把 `student_learning_api.py` 的输出 schema 固化成测试和文档。
- 为小程序新增作业详情、提交详情、总结详情 JSON。
- 统一 `Student.teacher_user` 与 `TeacherStudentAssignment` 的教师范围口径。
