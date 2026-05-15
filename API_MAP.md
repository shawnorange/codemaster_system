# API 与路由清单

生成日期：2026-05-11  
路由来源：`codemaster_system/urls.py`、`entry/urls.py`、`entry/views.py`

## 1. 总体说明

项目不是 DRF 项目，没有 `serializers.py`。JSON 响应主要由 `views.py`、`student_learning_api.py`、`portal_context.py` 手写 dict 后通过 `JsonResponse` 返回。绝大多数路由是 Django 模板页。

认证：

- HTML 页面：`codemaster_auth` cookie。
- JSON API：支持 cookie，也支持 `Authorization: Codemaster <token>`。
- 小程序登录接口是 `csrf_exempt`，其余 POST 页面通常需要 CSRF。

## 2. 登录与小程序 JSON API

| Method | Path | Name | View | 权限 | 功能 |
|---|---|---|---|---|---|
| GET/POST | `/` | `login` | `login_page` | anonymous | 统一登录页，成功后按角色跳转 |
| GET/POST | `/logout` | `logout` | `logout_view` | anonymous | 清除 auth cookie |
| POST | `/api/miniapp/login` | `api-miniapp-login` | `api_miniapp_login` | anonymous | 小程序账号密码登录，仅允许 parent/principal |
| POST | `/api/miniapp/login_by_phone` | `api-miniapp-login-by-phone` | `api_miniapp_login_by_phone` | anonymous | 微信手机号 code 登录，仅允许已绑定手机号的 parent |
| GET | `/api/parent/get_my_child` | `api-parent-get-my-child` | `api_parent_get_my_child` | parent | 获取当前家长绑定孩子及学习统计 |
| GET | `/api/principal/get_students_info` | `api-principal-get-students-info` | `api_principal_get_students_info` | principal | 获取全部学生学习统计 |

### `/api/miniapp/login`

请求 JSON：

```json
{
  "username": "parent001",
  "password": "password"
}
```

成功响应：

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

错误：400 JSON 格式错误或缺字段；401 账号密码错误；403 非 parent/principal。

### `/api/miniapp/login_by_phone`

请求 JSON：

```json
{
  "code": "wx-phone-code"
}
```

流程：

1. 后端用 `WECHAT_MINIAPP_APPID` / `WECHAT_MINIAPP_SECRET` 获取微信 `access_token`。
2. 调用 `wxa/business/getuserphonenumber` 获取手机号。
3. 标准化手机号，只查找 `PortalUser.role=parent` 且 `is_active=True` 的账号。
4. 手机号唯一匹配时返回同 `/api/miniapp/login` 的 token。

错误码：

- `missing_code`
- `wechat_config_missing`
- `wechat_api_error`
- `invalid_phone_code`
- `phone_not_bound`
- `phone_conflict`

### `/api/parent/get_my_child`

Query：

- `anchor_date=YYYY-MM-DD`，可选，默认当天。

响应结构：

```json
{
  "anchor_date": "2026-05-04",
  "children": [
    {
      "student_id": 1,
      "display_name": "学生A",
      "primary_level_name": "GESP4",
      "oj_week": {
        "week_start": "2026-05-04",
        "week_end": "2026-05-10",
        "submission_count": 12,
        "accepted_count": 8
      },
      "oj_month": {
        "month_start": "2026-05-01",
        "month_end": "2026-05-31",
        "submission_count": 48,
        "accepted_count": 32
      },
      "week": {
        "assignment_count": 1,
        "completed_count": 1,
        "on_time_completed_count": 1,
        "delayed_completed_count": 0,
        "incomplete_count": 0,
        "excluded_undated_count": 0,
        "lesson_feedbacks": [],
        "highlights": [],
        "areas_for_growth": []
      },
      "month": {},
      "quarter": {},
      "knowledge_points_by_period": {
        "week": [],
        "month": [],
        "quarter": []
      }
    }
  ]
}
```

家长响应不包含 `teacher_id` / `teacher_name`。
`oj_month` 基于 `StudentOjWeeklyStat` 聚合，统计 `week_start` 落在 `anchor_date` 所在自然月内的周统计。

### `/api/principal/get_students_info`

Query 同上。响应结构是：

```json
{
  "anchor_date": "2026-05-04",
  "periods": {
    "week": {"start": "2026-05-04", "end": "2026-05-10"},
    "month": {"start": "2026-05-01", "end": "2026-05-31"},
    "quarter": {"start": "2026-04-01", "end": "2026-06-30"}
  },
  "students": []
}
```

校长响应包含 `teacher_id` / `teacher_name`。

## 3. 学生端路由

| Method | Path | Name | View | 功能 |
|---|---|---|---|---|
| GET | `/student/courses` | `student-courses` | `student_courses` | 学生课程选择页 |
| GET | `/student/practice` | `student-practice` | `student_practice` | 练习入口 |
| GET | `/student/practice/homework` | `student-homework-list` | `student_homework_list` | 我的作业列表 |
| GET/POST | `/student/practice/homework/<assignment_id>` | `student-homework-detail` | `student_homework_detail` | 作业详情；POST 可标记要求型作业完成 |
| GET/POST | `/student/practice/homework/<assignment_id>/practice` | `student-homework-practice` | `student_homework_practice` | 在线选择题作答并自动判分 |
| GET | `/student/practice/homework/<assignment_id>/summary` | `student-homework-summary` | `student_homework_summary_detail` | 作业总结详情 |
| GET | `/student/practice/homework/<assignment_id>/submissions/<submission_id>` | `student-homework-submission-detail` | `student_homework_submission_detail` | 提交详情 |
| GET | `/student/practice/homework/<assignment_id>/submissions/<submission_id>/print` | `student-homework-print` | `student_homework_print` | 打印提交卷 |
| GET | `/student/practice/homework/<assignment_id>/submissions/<submission_id>/print/wrong` | `student-homework-print-wrong` | `student_homework_print_wrong` | 打印错题 |
| GET | `/student/practice/homework/<assignment_id>/print/blank` | `student-homework-print-blank` | `student_homework_print_blank` | 打印空白卷 |
| GET/POST | `/student/account-settings` | `student-account-settings` | `student_account_settings` | 学生账号设置 |
| GET | `/student/cpp` | `student-cpp` | `student_cpp` | C++ 方向 |
| GET | `/student/cpp/gesp` | `student-cpp-gesp` | `student_cpp_gesp` | GESP 层级 |
| GET | `/student/cpp/gesp/gesp2` | `student-cpp-gesp2` | `student_cpp_gesp2` | GESP2 知识点目录 |
| GET | `/student/cpp/gesp/gesp2/<topic_slug>` | `student-cpp-gesp2-topic` | `student_cpp_gesp2_topic` | GESP2 知识点页 |
| GET | `/student/cpp/gesp/gesp4` | `student-cpp-gesp4` | `student_cpp_gesp4` | GESP4 专题目录 |
| GET | `/student/cpp/gesp/gesp4/array-2d` | `student-cpp-gesp4-array-2d` | `student_cpp_gesp4_array_2d` | 二维数组专题兼容入口 |
| GET | `/student/cpp/gesp/gesp4/<topic_slug>` | `student-cpp-gesp4-topic` | `student_cpp_gesp4_topic` | GESP4 专题页 |

## 4. 家长端路由

| Method | Path | Name | View | 功能 |
|---|---|---|---|---|
| GET | `/parent/student-profile` | `parent-student-profile` | `parent_student_profile` | 家长学生档案 |
| GET | `/parent/student-profile/homework` | `parent-homework-list` | `parent_homework_list` | 孩子作业列表 |
| GET | `/parent/student-profile/homework/<assignment_id>` | `parent-homework-detail` | `parent_homework_detail` | 作业详情 |
| GET | `/parent/student-profile/homework/<assignment_id>/summary` | `parent-homework-summary` | `parent_homework_summary_detail` | 作业总结 |
| GET | `/parent/student-profile/homework/<assignment_id>/submissions/<submission_id>` | `parent-homework-submission-detail` | `parent_homework_submission_detail` | 提交详情 |
| GET | `/parent/student-profile/homework/<assignment_id>/submissions/<submission_id>/print` | `parent-homework-print` | `parent_homework_print` | 打印提交卷 |
| GET | `/parent/student-profile/homework/<assignment_id>/submissions/<submission_id>/print/wrong` | `parent-homework-print-wrong` | `parent_homework_print_wrong` | 打印错题 |
| GET | `/parent/student-profile/homework/<assignment_id>/print/blank` | `parent-homework-print-blank` | `parent_homework_print_blank` | 打印空白卷 |

## 5. 教师端路由

| Method | Path | Name | View | 功能 |
|---|---|---|---|---|
| GET | `/teacher/students` | `teacher-students` | `teacher_students` | 教师工作台 |
| GET | `/teacher/homework-stats` | `teacher-homework-stats` | `teacher_homework_stats` | 作业统计页，支持 `period`、`anchor_date` |
| GET | `/teacher/homework-stats/lesson-feedback` | `teacher-homework-stats-lesson-feedback` | `teacher_homework_stats_lesson_feedback` | JSON 获取某学生本周课堂反馈 |
| POST | `/teacher/homework-stats/lesson-feedback/save` | `teacher-homework-stats-lesson-feedback-save` | `teacher_homework_stats_lesson_feedback_save` | JSON 保存本周课堂反馈 |
| GET | `/teacher/homework-stats/submissions` | `teacher-homework-stats-submissions` | `teacher_homework_submission_detail` | 学生提交汇总 |
| GET | `/teacher/homework-stats/student-period-assignments` | `teacher-homework-stats-student-period-assignments` | `teacher_homework_student_period_assignment_detail` | 学生周期作业明细 |
| GET | `/teacher/homework-stats/assignment-submissions` | `teacher-homework-stats-assignment-submissions` | `teacher_homework_assignment_submission_detail` | 单作业提交明细 |
| GET | `/teacher/homework-stats/submission-answer-detail` | `teacher-homework-stats-submission-answer-detail` | `teacher_homework_submission_answer_detail` | 单提交作答明细 |
| GET/POST | `/teacher/homework/batch-create` | `teacher-homework-batch-create` | `teacher_homework_batch_create` | 批量布置作业，可关联公共题源和课后总结 |
| POST | `/teacher/homework/question-source/create-content` | `teacher-question-source-create-content` | `teacher_question_source_create_content` | JSON 创建知识点选项 |
| GET/POST | `/teacher/homework/question-source/import` | `teacher-question-source-import` | `teacher_question_source_import` | 公共题源上传、候选题确认 |
| GET | `/teacher/homework/import-jobs/<import_job_id>/preview` | `teacher-homework-import-job-preview` | `teacher_homework_import_job_preview` | JSON 预览导入题源 |
| GET/POST | `/teacher/students/assignments/new` | `teacher-assignment-new` | `teacher_assignment_new` | 新增教师-学生负责关系 |
| GET | `/teacher/students/<student_id>/assignments` | `teacher-student-assignments` | `teacher_student_assignments` | 学生负责关系列表 |
| GET/POST | `/teacher/students/<student_id>/assignments/<assignment_id>/edit` | `teacher-student-assignment-edit` | `teacher_student_assignment_edit` | 编辑负责关系 |
| GET/POST | `/teacher/students/<student_id>/assignments/<assignment_id>/remove` | `teacher-student-assignment-remove` | `teacher_student_assignment_remove` | 软删除负责关系 |
| GET | `/teacher/students/<student_id>` | `teacher-student-detail` | `teacher_student_detail` | 学生详情、权限、评价、奖励、课时、作业 |
| GET/POST | `/teacher/students/<student_id>/homework/<assignment_id>` | `teacher-homework-builder` | `teacher_homework_builder` | 作业题源上传、候选题确认、在线题生成 |
| GET | `/teacher/courses/<course_slug>` | `teacher-course-detail` | `teacher_course_detail` | 课程详情 |
| GET | `/teacher/courses/<course_slug>/export` | `teacher-course-export` | `teacher_course_export` | 导出课程结构 JSON |
| GET | `/teacher/courses/<course_slug>/categories/<category_slug>` | `teacher-course-category-detail` | `teacher_course_category_detail` | 课程分类详情 |
| GET | `/teacher/courses/<course_slug>/categories/<category_slug>/export` | `teacher-course-category-export` | `teacher_course_category_export` | 导出分类结构 JSON |
| GET | `/teacher/courses/<course_slug>/categories/<category_slug>/levels/<level_code>` | `teacher-course-level-detail` | `teacher_course_level_detail` | Level 知识点 grid |
| GET | `/teacher/courses/<course_slug>/categories/<category_slug>/levels/<level_code>/export` | `teacher-course-level-export` | `teacher_course_level_export` | 导出 Level 结构 JSON |
| GET/POST | `/teacher/courses/<course_slug>/categories/<category_slug>/levels/<level_code>/knowledge-points/new` | `teacher-course-knowledge-point-new` | `teacher_course_knowledge_point_new` | 新增知识点 |
| GET/POST | `/teacher/courses/<course_slug>/categories/<category_slug>/levels/<level_code>/knowledge-points/<content_slug>/edit` | `teacher-course-knowledge-point-edit` | `teacher_course_knowledge_point_edit` | 编辑知识点 |
| GET/POST | `/teacher/courses/<course_slug>/categories/<category_slug>/levels/<level_code>/knowledge-points/<content_slug>/delete` | `teacher-course-knowledge-point-delete` | `teacher_course_knowledge_point_delete` | 停用知识点 |
| GET/POST | `/teacher/courses/<course_slug>/categories/<category_slug>/levels/<level_code>/knowledge-points/<content_slug>/permissions` | `teacher-course-knowledge-point-permissions` | `teacher_course_knowledge_point_permissions` | 学生知识点开放权限 |
| GET | `/teacher/courses/<course_slug>/categories/<category_slug>/levels/<level_code>/knowledge-points/<content_slug>/teaching-page` | `teacher-course-knowledge-point-teaching-page` | `teacher_course_knowledge_point_teaching_page` | 跳转真实教学页或占位页 |
| GET | `/teacher/courses/<course_slug>/students` | `teacher-course-students-detail` | `teacher_course_students_detail` | 课程学生列表，支持学生导入 |
| GET/POST | `/teacher/courses/<course_slug>/student-pool` | `teacher-course-student-pool` | `teacher_course_student_pool` | 学生池、批量/单个加入课程 |
| GET | `/teacher/courses/cpp/gesp2/ascii-char-encoding` | `teacher-cpp-gesp2-ascii-char-encoding` | `teacher_cpp_gesp2_ascii_char_encoding` | GESP2 ASCII 教学页 |
| GET | `/teacher/courses/cpp/gesp2/enumeration-method` | `teacher-cpp-gesp2-enumeration` | `teacher_cpp_gesp2_enumeration` | GESP2 枚举法教学页 |
| POST | `/teacher/questions/<question_id>/manual-override` | `teacher-question-manual-override` | `teacher_question_manual_override` | 题目人工修订/图片覆盖 |
| GET | `/teacher/courses/cpp/gesp4/array-2d` | `teacher-cpp-gesp4-array-2d` | `teacher_cpp_gesp4_array_2d` | GESP4 二维数组教学页 |
| GET | `/teacher/courses/cpp/gesp4/binary-search` | `teacher-cpp-gesp4-binary-search` | `teacher_cpp_gesp4_binary_search` | GESP4 二分查找页 |
| GET | `/teacher/courses/cpp/gesp4/sorting` | `teacher-cpp-gesp4-sorting` | `teacher_cpp_gesp4_sorting` | GESP4 排序页 |
| GET | `/teacher/courses/cpp/gesp4/strings` | `teacher-cpp-gesp4-strings` | `teacher_cpp_gesp4_strings` | GESP4 字符串页 |

## 6. 校长端路由

| Method | Path | Name | View | 功能 |
|---|---|---|---|---|
| GET | `/principal/dashboard` | `principal-dashboard` | `principal_dashboard` | 校长概览页 |

## 7. DEBUG 媒体路由

当 `settings.DEBUG=True` 时，项目根路由额外注册：

| Method | Path | View | 功能 |
|---|---|---|---|
| GET | `/media/<path>` | `debug_media_serve` | 开发环境由 Django 直接服务媒体文件 |
