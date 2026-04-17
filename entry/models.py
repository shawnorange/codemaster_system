from __future__ import annotations

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class PortalUser(models.Model):
    ROLE_STUDENT = "student"
    ROLE_PARENT = "parent"
    ROLE_TEACHER = "teacher"
    ROLE_PRINCIPAL = "principal"

    ROLE_CHOICES = [
        (ROLE_STUDENT, "学生"),
        (ROLE_PARENT, "家长"),
        (ROLE_TEACHER, "教师"),
        (ROLE_PRINCIPAL, "校长"),
    ]

    username = models.CharField("账号", max_length=64, unique=True)
    password = models.CharField("密码哈希", max_length=128)
    role = models.CharField("角色", max_length=20, choices=ROLE_CHOICES)
    full_name = models.CharField("姓名", max_length=64)
    phone = models.CharField("手机号", max_length=32, blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "账号"
        verbose_name_plural = "账号"

    def __str__(self) -> str:
        return f"{self.full_name}({self.username})"

    def set_password(self, raw_password: str) -> None:
        self.password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password(raw_password, self.password)


class Student(models.Model):
    user = models.OneToOneField(PortalUser, on_delete=models.CASCADE, related_name="student_profile")
    parent_user = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )
    teacher_user = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_students",
    )
    display_name = models.CharField("学生姓名", max_length=64)
    grade = models.CharField("年级", max_length=32)
    campus = models.CharField("校区", max_length=64, blank=True)
    primary_course_name = models.CharField("主课程方向", max_length=64, blank=True)
    primary_track_name = models.CharField("主学习体系", max_length=64, blank=True)
    primary_level_name = models.CharField("主当前级别", max_length=64, blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "学生"
        verbose_name_plural = "学生"

    def __str__(self) -> str:
        return self.display_name


class Course(models.Model):
    slug = models.SlugField("课程标识", unique=True)
    title = models.CharField("课程名称", max_length=64)
    summary = models.TextField("课程说明", blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "课程方向"
        verbose_name_plural = "课程方向"

    def __str__(self) -> str:
        return self.title


class CourseCategory(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="categories")
    slug = models.SlugField("分类标识", max_length=64)
    title = models.CharField("分类名称", max_length=64)
    summary = models.TextField("分类说明", blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_active = models.BooleanField("是否启用", default=True)

    class Meta:
        ordering = ["course_id", "sort_order", "id"]
        verbose_name = "课程分类"
        verbose_name_plural = "课程分类"
        constraints = [
            models.UniqueConstraint(fields=["course", "slug"], name="course_category_slug_unique"),
        ]
        indexes = [
            models.Index(fields=["course", "is_active", "sort_order"], name="course_cat_active_sort_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.course.title} / {self.title}"


class CourseLevel(models.Model):
    category = models.ForeignKey(CourseCategory, on_delete=models.CASCADE, related_name="levels")
    code = models.CharField("级别编码", max_length=32)
    title = models.CharField("级别名称", max_length=64)
    summary = models.TextField("级别说明", blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_active = models.BooleanField("是否启用", default=True)

    class Meta:
        ordering = ["category_id", "sort_order", "id"]
        verbose_name = "课程级别"
        verbose_name_plural = "课程级别"
        constraints = [
            models.UniqueConstraint(fields=["category", "code"], name="course_level_code_unique"),
        ]
        indexes = [
            models.Index(fields=["category", "is_active", "sort_order"], name="course_lvl_active_sort_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.category.title} / {self.title}"


class CourseContent(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="contents")
    level = models.ForeignKey(
        CourseLevel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contents",
    )
    content_type = models.CharField("课程类别", max_length=64, blank=True)
    slug = models.SlugField("内容标识", unique=True)
    title = models.CharField("内容名称", max_length=128)
    phase = models.CharField("所属阶段", max_length=64, blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    route_path = models.CharField("访问路由", max_length=255, unique=True)
    summary = models.TextField("内容说明", blank=True)
    has_real_content = models.BooleanField("是否已有真实内容", default=False)
    is_active = models.BooleanField("是否启用", default=True)

    class Meta:
        ordering = ["course_id", "phase", "sort_order", "id"]
        verbose_name = "课程内容"
        verbose_name_plural = "课程内容"
        indexes = [
            models.Index(fields=["level", "is_active", "sort_order"], name="content_level_active_sort_idx"),
        ]

    def __str__(self) -> str:
        return self.title


class Question(models.Model):
    code = models.CharField("题目标识", max_length=128, unique=True)
    content_slug = models.CharField("知识点/专题标识", max_length=64)
    level_code = models.CharField("级别", max_length=16)
    question_type = models.CharField("题型", max_length=32)
    source_year = models.PositiveSmallIntegerField("来源年份", null=True, blank=True)
    source_month = models.PositiveSmallIntegerField("来源月份", null=True, blank=True)
    source_question_no = models.PositiveSmallIntegerField("来源题号", null=True, blank=True)
    title = models.CharField("题目标题", max_length=255)
    payload = models.JSONField("题目内容", default=dict, blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_active = models.BooleanField("是否启用", default=True)
    is_demo = models.BooleanField("是否适合作教学 Demo", default=False)

    class Meta:
        db_table = "questions"
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(source_month__isnull=True) | models.Q(source_month__gte=1, source_month__lte=12),
                name="questions_source_month_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["content_slug", "is_active", "sort_order", "id"],
                name="q_content_active_sort_idx",
            ),
            models.Index(
                fields=["level_code", "is_active", "sort_order", "id"],
                name="q_level_active_sort_idx",
            ),
            models.Index(
                fields=["question_type", "is_active", "sort_order", "id"],
                name="q_type_active_sort_idx",
            ),
            models.Index(
                fields=["source_year", "source_month", "is_active", "sort_order", "id"],
                name="q_source_active_sort_idx",
            ),
            models.Index(
                fields=["content_slug", "is_demo", "is_active", "sort_order", "id"],
                name="q_content_demo_sort_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class StudentContentAccess(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="content_accesses")
    content = models.ForeignKey(CourseContent, on_delete=models.CASCADE, related_name="student_accesses")
    is_open = models.BooleanField("是否开放", default=False)
    granted_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_content_accesses",
    )
    granted_at = models.DateTimeField("开放时间", null=True, blank=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["id"]
        unique_together = [("student", "content")]
        verbose_name = "学生内容开放记录"
        verbose_name_plural = "学生内容开放记录"

    def __str__(self) -> str:
        state = "开放" if self.is_open else "未开放"
        return f"{self.student.display_name} - {self.content.title} ({state})"

    def set_open_state(self, *, is_open: bool, granted_by: PortalUser | None) -> None:
        self.is_open = is_open
        self.granted_by = granted_by
        self.granted_at = timezone.now() if is_open else None


class HomeworkAssignment(models.Model):
    """`status` 驱动作业业务状态，`is_active` 仅保留为软删除/停用位。"""

    STATUS_ASSIGNED = "assigned"
    STATUS_COMPLETED = "completed"
    STATUS_REVIEWED = "reviewed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_ASSIGNED, "待完成"),
        (STATUS_COMPLETED, "已完成"),
        (STATUS_REVIEWED, "已评阅"),
        (STATUS_CANCELLED, "已取消"),
    ]

    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.CASCADE,
        related_name="homework_assignments",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="homework_assignments",
    )
    content = models.ForeignKey(
        CourseContent,
        on_delete=models.CASCADE,
        related_name="homework_assignments",
    )
    title = models.CharField("作业标题", max_length=255)
    description = models.TextField("作业说明", blank=True)
    due_date = models.DateField("截止日期")
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_ASSIGNED)
    teacher_comment = models.TextField("教师评语", blank=True)
    assigned_at = models.DateTimeField("布置时间", default=timezone.now)
    completed_at = models.DateTimeField("完成时间", null=True, blank=True)
    reviewed_at = models.DateTimeField("评阅时间", null=True, blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-due_date", "-assigned_at", "-id"]
        verbose_name = "作业"
        verbose_name_plural = "作业"
        indexes = [
            models.Index(fields=["student", "due_date", "status"], name="hw_student_due_status_idx"),
            models.Index(fields=["teacher", "due_date", "status"], name="hw_teacher_due_status_idx"),
            models.Index(fields=["content", "status"], name="hw_content_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student.display_name} - {self.title}"

    def mark_completed(self) -> bool:
        if not self.is_active or self.status != self.STATUS_ASSIGNED:
            return False
        self.status = self.STATUS_COMPLETED
        if not self.completed_at:
            self.completed_at = timezone.now()
        return True

    def mark_reviewed(self, *, teacher_comment: str) -> bool:
        if not self.is_active or self.status == self.STATUS_CANCELLED:
            return False
        self.teacher_comment = teacher_comment.strip()
        if self.status in {self.STATUS_COMPLETED, self.STATUS_REVIEWED} and teacher_comment.strip():
            self.status = self.STATUS_REVIEWED
            self.reviewed_at = timezone.now()
            return True
        return False

    def cancel(self) -> bool:
        if not self.is_active or self.status == self.STATUS_CANCELLED:
            return False
        self.status = self.STATUS_CANCELLED
        return True


class HomeworkImportJob(models.Model):
    SOURCE_TYPE_PDF = "pdf"
    SOURCE_TYPE_IMAGE = "image"
    SOURCE_TYPE_HTML = "html"
    SOURCE_TYPE_TEXT = "text"
    SOURCE_TYPE_DOCX = "docx"
    SOURCE_TYPE_XLSX = "xlsx"

    SOURCE_TYPE_CHOICES = [
        (SOURCE_TYPE_PDF, "PDF"),
        (SOURCE_TYPE_IMAGE, "图片"),
        (SOURCE_TYPE_HTML, "HTML"),
        (SOURCE_TYPE_TEXT, "TXT"),
        (SOURCE_TYPE_DOCX, "DOCX"),
        (SOURCE_TYPE_XLSX, "XLSX"),
    ]

    STATUS_UPLOADED = "uploaded"
    STATUS_PARSING = "parsing"
    STATUS_PARSED = "parsed"
    STATUS_CONFIRMED = "confirmed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    PARSE_STATUS_CHOICES = [
        (STATUS_UPLOADED, "已上传"),
        (STATUS_PARSING, "解析中"),
        (STATUS_PARSED, "待确认"),
        (STATUS_CONFIRMED, "已确认"),
        (STATUS_FAILED, "解析失败"),
        (STATUS_CANCELLED, "已取消"),
    ]

    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.CASCADE,
        related_name="homework_import_jobs",
    )
    assignment = models.ForeignKey(
        HomeworkAssignment,
        on_delete=models.CASCADE,
        related_name="import_jobs",
    )
    source_file = models.FileField("源文件", upload_to="homework_imports/%Y/%m/%d")
    source_filename = models.CharField("原始文件名", max_length=255)
    source_sha256 = models.CharField("源文件内容哈希", max_length=64, blank=True, default="")
    source_type = models.CharField("源文件类型", max_length=16, choices=SOURCE_TYPE_CHOICES)
    parse_status = models.CharField("解析状态", max_length=16, choices=PARSE_STATUS_CHOICES, default=STATUS_UPLOADED)
    candidates_json = models.JSONField("候选题目", default=list, blank=True)
    parse_notes = models.TextField("解析备注", blank=True)
    confirmed_at = models.DateTimeField("确认时间", null=True, blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "作业导入任务"
        verbose_name_plural = "作业导入任务"
        indexes = [
            models.Index(fields=["assignment", "parse_status", "is_active"], name="hw_imp_assign_status_idx"),
            models.Index(fields=["teacher", "parse_status", "is_active"], name="hw_import_teacher_status_idx"),
            models.Index(fields=["assignment", "source_sha256", "created_at"], name="hw_imp_assign_sha_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.assignment.title} - {self.source_filename}"


class HomeworkQuestion(models.Model):
    QUESTION_TYPE_SINGLE_CHOICE = "single_choice"

    QUESTION_TYPE_CHOICES = [
        (QUESTION_TYPE_SINGLE_CHOICE, "单选题"),
    ]

    assignment = models.ForeignKey(
        HomeworkAssignment,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    import_job = models.ForeignKey(
        HomeworkImportJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
    )
    question_no = models.PositiveIntegerField("题号")
    question_type = models.CharField("题型", max_length=32, choices=QUESTION_TYPE_CHOICES, default=QUESTION_TYPE_SINGLE_CHOICE)
    stem = models.TextField("题干")
    options_json = models.JSONField("选项", default=dict, blank=True)
    correct_answer = models.CharField("正确答案", max_length=1)
    analysis = models.TextField("解析", blank=True)
    source_snapshot_json = models.JSONField("来源快照", default=dict, blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["question_no", "id"]
        verbose_name = "作业题目"
        verbose_name_plural = "作业题目"
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "question_no"],
                condition=models.Q(is_active=True),
                name="hw_question_assignment_no_active_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["assignment", "is_active", "question_no"], name="hw_question_assign_idx"),
            models.Index(fields=["import_job", "is_active", "question_no"], name="hw_question_import_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.assignment.title} - 第{self.question_no}题"


class HomeworkSubmission(models.Model):
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_SUBMITTED = "submitted"
    STATUS_AUTO_CHECKED = "auto_checked"
    STATUS_REVIEWED = "reviewed"

    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, "作答中"),
        (STATUS_SUBMITTED, "已提交"),
        (STATUS_AUTO_CHECKED, "已自动判分"),
        (STATUS_REVIEWED, "已复核"),
    ]

    assignment = models.ForeignKey(
        HomeworkAssignment,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="homework_submissions",
    )
    status = models.CharField("提交状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    total_count = models.PositiveIntegerField("总题数", default=0)
    correct_count = models.PositiveIntegerField("正确数", default=0)
    wrong_count = models.PositiveIntegerField("错误数", default=0)
    score = models.DecimalField("分数", max_digits=5, decimal_places=2, default=0)
    started_at = models.DateTimeField("开始时间", default=timezone.now)
    submitted_at = models.DateTimeField("提交时间", null=True, blank=True)
    checked_at = models.DateTimeField("判分时间", null=True, blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        verbose_name = "作业提交"
        verbose_name_plural = "作业提交"
        constraints = [
            models.UniqueConstraint(fields=["assignment", "student"], name="hw_submission_assignment_student_unique"),
        ]
        indexes = [
            models.Index(fields=["student", "status", "is_active"], name="hw_sub_student_status_idx"),
            models.Index(fields=["assignment", "status", "is_active"], name="hw_sub_assign_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student.display_name} - {self.assignment.title}"


class HomeworkSubmissionAnswer(models.Model):
    submission = models.ForeignKey(
        HomeworkSubmission,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    homework_question = models.ForeignKey(
        HomeworkQuestion,
        on_delete=models.CASCADE,
        related_name="submission_answers",
    )
    selected_answer = models.CharField("学生答案", max_length=1, blank=True)
    is_correct = models.BooleanField("是否正确", default=False)
    correct_answer_snapshot = models.CharField("正确答案快照", max_length=1)
    analysis_snapshot = models.TextField("解析快照", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["homework_question_id", "id"]
        verbose_name = "作业作答明细"
        verbose_name_plural = "作业作答明细"
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "homework_question"],
                name="hw_submission_answer_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["submission", "is_correct"], name="hw_ans_sub_correct_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.submission} - 第{self.homework_question.question_no}题"


class TeacherEvaluation(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="teacher_evaluations")
    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_evaluations",
    )
    evaluation_text = models.TextField("教师评价")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "教师评价"
        verbose_name_plural = "教师评价"

    def __str__(self) -> str:
        return f"{self.student.display_name} - 评价"


class RewardRecord(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="reward_records")
    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_rewards",
    )
    reward_text = models.CharField("奖励说明", max_length=255)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "奖励记录"
        verbose_name_plural = "奖励记录"

    def __str__(self) -> str:
        return f"{self.student.display_name} - {self.reward_text}"


class LessonHourLedger(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="lesson_hour_ledgers")
    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_lesson_hour_ledgers",
    )
    delta_hours = models.IntegerField("课时变动")
    note = models.CharField("备注", max_length=255, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "课时变动记录"
        verbose_name_plural = "课时变动记录"

    def __str__(self) -> str:
        return f"{self.student.display_name} - {self.delta_hours:+d}课时"


class TeacherStudentAssignment(models.Model):
    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.CASCADE,
        related_name="student_assignments",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="teacher_assignments",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="teacher_student_assignments",
    )
    level_code = models.CharField("级别编码", max_length=16)
    is_active = models.BooleanField("是否生效", default=True)
    assigned_at = models.DateTimeField("分配时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["teacher_id", "student_id", "course_id", "level_code", "id"]
        verbose_name = "教师学生负责关系"
        verbose_name_plural = "教师学生负责关系"
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "student", "course", "level_code"],
                name="teacher_student_course_level_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["teacher", "is_active"], name="tsa_teacher_active_idx"),
            models.Index(fields=["student", "is_active"], name="tsa_student_active_idx"),
            models.Index(fields=["course", "level_code", "is_active"], name="tsa_course_level_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.teacher.full_name} -> {self.student.display_name} ({self.course.title} {self.level_code})"
