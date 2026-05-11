# 数据库结构总结

生成日期：2026-05-11  
模型来源：`entry/models.py`

## 1. 总体关系图

```text
PortalUser(role=student/parent/teacher/principal)
  1:1 Student.user
  1:N Student.parent_user
  1:N Student.teacher_user
  1:N TeacherStudentAssignment.teacher
  1:N HomeworkAssignment.teacher

Course
  1:N CourseCategory
  1:N CourseContent
CourseCategory
  1:N CourseLevel
CourseLevel
  1:N CourseContent

Student
  1:N StudentContentAccess
  1:N TeacherStudentAssignment
  1:N HomeworkAssignment
  1:N HomeworkSubmission
  1:N TeacherEvaluation / RewardRecord / LessonHourLedger

HomeworkAssignment
  1:N HomeworkImportJob
  1:N HomeworkQuestion
  1:N HomeworkSubmission
  N:1 HomeworkSummary
  N:1 HomeworkImportJob(source_import_job)

HomeworkSubmission
  1:N HomeworkSubmissionAnswer
HomeworkCompletionStat
  1:N HomeworkCompletionStatMissingAssignment
```

## 2. 账号与学生

### `entry_portaluser` / `PortalUser`

用途：系统自定义账号表，替代 Django auth user。

核心字段：

- `username`：唯一账号
- `password`：Django hash
- `role`：`student`、`parent`、`teacher`、`principal`
- `full_name`
- `phone`：小程序手机号登录和家长绑定依据
- `is_active`

注意：没有 `admin` 角色，没有 `is_staff` / `is_superuser`。

### `entry_student` / `Student`

用途：学生档案。

核心字段：

- `user`：一对一到学生 PortalUser
- `parent_user`：外键到家长 PortalUser，可为空
- `teacher_user`：外键到主负责教师 PortalUser，可为空
- `display_name`、`grade`、`campus`
- `primary_course_name`、`primary_track_name`、`primary_level_name`

关系说明：

- 家长小程序接口通过 `Student.parent_user=current_parent` 查孩子。
- 教师端很多统计仍使用 `Student.teacher_user=current_teacher` 过滤。
- 更细的多教师关系使用 `TeacherStudentAssignment`。

## 3. 课程结构

### `entry_course` / `Course`

用途：课程方向，例如 C++、Scratch、PBL、无人机。

核心字段：`slug` 唯一、`title`、`summary`。

### `entry_coursecategory` / `CourseCategory`

用途：课程下的分类，例如 C++ 下的 GESP、CSP、机器人编程。

核心字段：

- `course`
- `slug`
- `title`
- `sort_order`
- `is_active`

约束：`course + slug` 唯一。

### `entry_courselevel` / `CourseLevel`

用途：分类下的 Level，例如 GESP1-GESP8。

核心字段：

- `category`
- `code`
- `title`
- `sort_order`
- `is_active`

约束：`category + code` 唯一。

### `entry_coursecontent` / `CourseContent`

用途：知识点/专题内容。

核心字段：

- `course`
- `level`
- `content_type`
- `slug` 唯一
- `title`
- `phase`
- `permission_code`：C1-C4 内容等级
- `route_path` 唯一
- `has_real_content`
- `is_active`

索引：

- `level + is_active + sort_order`
- `permission_code + is_active + sort_order`

## 4. 题库与内容开放

### `questions` / `Question`

用途：通用题库单表。

核心字段：

- `code`：唯一题目标识
- `content_slug`
- `level_code`
- `question_type`：`single_choice`、`judgement`、`programming`
- `source_year`、`source_month`、`source_question_no`
- `title`
- `payload`：题目主体 JSON
- `sort_order`
- `is_active`
- `is_demo`

约束：`source_month` 为空或在 1-12。

说明：`Question` 没有外键到 `CourseContent`，而是通过 `content_slug` 软关联。

### `entry_studentcontentaccess` / `StudentContentAccess`

用途：学生-知识点开放记录。

核心字段：

- `student`
- `content`
- `is_open`
- `granted_by`
- `granted_at`

约束：`student + content` 唯一。

说明：学生能否进入内容由 `StudentContentAccess`、课程权限等级和静态 fallback 共同决定。

## 5. 作业主链路

### `entry_homeworkassignment` / `HomeworkAssignment`

用途：老师布置给学生的一条作业。

核心字段：

- `teacher`
- `student`
- `content`
- `title`
- `description`
- `due_date`：`DateTimeField`
- `status`：`assigned`、`completed`、`reviewed`、`cancelled`
- `teacher_comment`
- `highlights`
- `areas_for_growth`
- `summary`
- `source_import_job`
- `assigned_at`、`completed_at`、`reviewed_at`
- `is_active`

业务规则：

- `status` 是业务状态；`is_active` 只做停用/软删除。
- 在线题作业是否完成看是否有完成态提交。
- 要求型作业是否完成看 assignment 状态和 `completed_at`。
- 周/月/季度归属统一按 `due_date` 计算。

### `entry_homeworksummary` / `HomeworkSummary`

用途：课后/周总结 HTML。

核心字段：

- `title`
- `summary_html`
- `created_by`

说明：亮点和待提升点已经从 `HomeworkSummary` 移到 `HomeworkAssignment.highlights` / `areas_for_growth`。

### `entry_homeworkimportjob` / `HomeworkImportJob`

用途：上传题源后的解析任务。

核心字段：

- `teacher`
- `assignment`：绑定具体作业时使用
- `content`：公共题池导入时使用
- `source_file`
- `source_filename`
- `source_sha256`
- `source_type`：`pdf`、`image`、`html`、`text`、`docx`、`xlsx`
- `parse_status`：`uploaded`、`parsing`、`parsed`、`confirmed`、`failed`、`cancelled`
- `candidates_json`
- `parse_notes`
- `confirmed_at`
- `is_active`

说明：当前解析是同步请求内执行，慢外部 OCR/LLM 会阻塞 worker。

### `entry_homeworkquestion` / `HomeworkQuestion`

用途：正式作业在线题。

核心字段：

- `assignment`
- `import_job`
- `question_no`
- `question_type`：当前只有 `single_choice`
- `stem`
- `options_json`
- `correct_answer`
- `analysis`
- `source_snapshot_json`
- `is_active`

约束：同一 `assignment` 下 active `question_no` 唯一。

### `entry_homeworksubmission` / `HomeworkSubmission`

用途：学生一次作答/提交记录，支持多次提交。

核心字段：

- `assignment`
- `student`
- `status`：`in_progress`、`submitted`、`auto_checked`、`reviewed`
- `total_count`
- `correct_count`
- `wrong_count`
- `score`
- `started_at`
- `submitted_at`
- `checked_at`
- `is_active`

### `entry_homeworksubmissionanswer` / `HomeworkSubmissionAnswer`

用途：提交下的逐题答案。

核心字段：

- `submission`
- `homework_question`
- `selected_answer`
- `is_correct`
- `correct_answer_snapshot`
- `analysis_snapshot`

约束：`submission + homework_question` 唯一。

## 6. 作业统计

### `entry_homeworkcompletionstat` / `HomeworkCompletionStat`

用途：持久化作业完成统计。

核心字段：

- `teacher`
- `student`
- `period_type`：`week`、`month`、`quarter`
- `period_start`
- `period_end`
- `completed_count`
- `incomplete_count`
- `excluded_undated_count`
- `generated_at`

约束：`teacher + student + period_type + period_start + period_end` 唯一。

说明：当前教师统计和小程序统计主要实时计算；该表用于需要持久化统计时。

### `entry_homeworkcompletionstatmissingassignment` / `HomeworkCompletionStatMissingAssignment`

用途：统计中未完成作业明细。

核心字段：

- `stat`
- `assignment`
- `reason`：`online_missing`、`requirement_not_marked_completed`、`undated`

约束：`stat + assignment` 唯一。

## 7. 教师记录

### `entry_teacherevaluation` / `TeacherEvaluation`

用途：教师评价。

核心字段：`student`、`teacher`、`evaluation_text`、`created_at`。

### `entry_rewardrecord` / `RewardRecord`

用途：奖励记录。

核心字段：`student`、`teacher`、`reward_text`、`created_at`。

### `entry_lessonhourledger` / `LessonHourLedger`

用途：课时变动流水。

核心字段：`student`、`teacher`、`delta_hours`、`note`、`created_at`。

## 8. 教师-学生负责关系

### `entry_teacherstudentassignment` / `TeacherStudentAssignment`

用途：多教师、多课程、多等级的负责关系。

核心字段：

- `teacher`
- `student`
- `course`
- `level_code`
- `is_active`
- `assigned_at`

约束：`teacher + student + course + level_code` 唯一。

说明：这是教师课程/学生权限范围的重要来源；但部分旧逻辑仍依赖 `Student.teacher_user`，后续需要统一口径。

## 9. 迁移概况

当前迁移文件到 `0028_alter_homeworkassignment_due_date.py`。关键演进：

- `0001` 初始账号、学生、课程、内容、开放权限。
- `0004` 增加教师评价、奖励、课时。
- `0009` 增加 `questions` 题库表。
- `0013` 增加 `TeacherStudentAssignment`。
- `0015` 增加 `HomeworkAssignment`。
- `0016` 增加在线选择题相关提交表。
- `0021`-`0022` 增加 `HomeworkSummary` 及作业关联。
- `0023`-`0024` 增加作业导入和公共题源能力。
- `0025` 增加作业完成统计表。
- `0026`-`0027` 将亮点/待提升点调整到 `HomeworkAssignment`。
- `0028` 将 `HomeworkAssignment.due_date` 调整为 DateTime 口径。
