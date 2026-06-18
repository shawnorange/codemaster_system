from __future__ import annotations

from datetime import date, datetime, time, timedelta
import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

from .content_visibility import CONTENT_PERMISSION_CHOICES


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


class StudentOjWeeklyStat(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="oj_weekly_stats")
    source = models.CharField(max_length=64, default="dashima-oj")
    oj_username = models.CharField(max_length=128, blank=True)
    week_start = models.DateField()
    week_end = models.DateField()
    submission_count = models.PositiveIntegerField(default=0, verbose_name="本周 OJ 提交数量")
    accepted_count = models.PositiveIntegerField(default=0, verbose_name="本周 OJ 通过数量")
    raw_record_count = models.PositiveIntegerField(default=0, verbose_name="本次拉取原始记录数")
    synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-week_start", "student_id", "source"]
        verbose_name = "学生 OJ 周统计"
        verbose_name_plural = "学生 OJ 周统计"
        constraints = [
            models.UniqueConstraint(fields=["student", "source", "week_start"], name="uniq_student_oj_weekly_stat"),
        ]
        indexes = [
            models.Index(fields=["source", "week_start"], name="oj_stat_source_week_idx"),
            models.Index(fields=["student", "week_start"], name="oj_stat_student_week_idx"),
            models.Index(fields=["oj_username"], name="oj_stat_username_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student.display_name} - {self.source} - {self.week_start}"


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


class Teacher(models.Model):
    user = models.OneToOneField(PortalUser, on_delete=models.CASCADE, related_name="teacher_profile")
    display_name = models.CharField("教师姓名", max_length=64)
    phone = models.CharField("手机号", max_length=32, blank=True)
    subject = models.CharField("负责学科", max_length=64, blank=True)
    is_active = models.BooleanField("是否在职", default=True)
    courses = models.ManyToManyField(Course, related_name="teacher_profiles", blank=True, verbose_name="关联课程")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "教师"
        verbose_name_plural = "教师"

    def __str__(self) -> str:
        return self.display_name


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
    permission_code = models.CharField(
        "权限归属",
        max_length=4,
        choices=CONTENT_PERMISSION_CHOICES,
        blank=True,
        default="",
    )
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
            models.Index(fields=["permission_code", "is_active", "sort_order"], name="content_perm_active_sort_idx"),
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
    due_date = models.DateTimeField("截止日期")
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_ASSIGNED)
    teacher_comment = models.TextField("教师评语", blank=True)
    highlights = models.TextField("亮点表现", blank=True, default="")
    areas_for_growth = models.TextField("待提升点", blank=True, default="")
    summary = models.ForeignKey(
        "HomeworkSummary",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments",
    )
    source_import_job = models.ForeignKey(
        "HomeworkImportJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shared_assignments",
    )
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

    @staticmethod
    def build_due_datetime_for_date(target_date: date) -> datetime:
        tz = timezone.get_current_timezone()
        return timezone.make_aware(datetime.combine(target_date, time(23, 59, 59)), tz)

    @classmethod
    def normalize_due_date_value(cls, value: date | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if timezone.is_aware(value):
                return value
            return timezone.make_aware(value, timezone.get_current_timezone())
        return cls.build_due_datetime_for_date(value)

    @staticmethod
    def get_due_localdate(value: date | datetime | None) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if timezone.is_aware(value):
                return timezone.localtime(value).date()
            return value.date()
        return value

    def save(self, *args, **kwargs):
        self.due_date = self.normalize_due_date_value(self.due_date)
        super().save(*args, **kwargs)

    def get_effective_questions_queryset(self):
        direct_questions = self.questions.filter(is_active=True).order_by("question_no", "id")
        if direct_questions.exists():
            return direct_questions
        if self.source_import_job_id and self.source_import_job and self.source_import_job.is_active:
            return self.source_import_job.questions.filter(is_active=True).order_by("question_no", "id")
        return self.questions.none()

    def get_effective_online_question_count(self) -> int:
        direct_count = getattr(self, "direct_online_question_count", None)
        if direct_count is None:
            direct_count = self.questions.filter(is_active=True).count()
        if direct_count:
            return int(direct_count)

        source_count = getattr(self, "source_online_question_count", None)
        if source_count is None:
            if not self.source_import_job_id or not self.source_import_job or not self.source_import_job.is_active:
                return 0
            source_count = self.source_import_job.questions.filter(is_active=True).count()
        return int(source_count or 0)

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


class HomeworkSummary(models.Model):
    title = models.CharField("总结标题", max_length=255, blank=True, default="")
    summary_html = models.TextField("总结 HTML", blank=True, default="")
    created_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_homework_summaries",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        verbose_name = "作业周总结"
        verbose_name_plural = "作业周总结"

    def __str__(self) -> str:
        return self.title or f"作业周总结 #{self.pk}"


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
        null=True,
        blank=True,
    )
    content = models.ForeignKey(
        CourseContent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="homework_import_jobs",
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
        owner_label = (
            self.assignment.title
            if self.assignment_id and self.assignment
            else self.content.title
            if self.content_id and self.content
            else "公共题池"
        )
        return f"{owner_label} - {self.source_filename}"


class HomeworkQuestion(models.Model):
    QUESTION_TYPE_SINGLE_CHOICE = "single_choice"

    QUESTION_TYPE_CHOICES = [
        (QUESTION_TYPE_SINGLE_CHOICE, "单选题"),
    ]

    assignment = models.ForeignKey(
        HomeworkAssignment,
        on_delete=models.CASCADE,
        related_name="questions",
        null=True,
        blank=True,
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
        owner_label = (
            self.assignment.title
            if self.assignment_id and self.assignment
            else self.import_job.source_filename
            if self.import_job_id and self.import_job
            else "公共题池题目"
        )
        return f"{owner_label} - 第{self.question_no}题"


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
        indexes = [
            models.Index(fields=["student", "status", "is_active"], name="hw_sub_student_status_idx"),
            models.Index(fields=["assignment", "status", "is_active"], name="hw_sub_assign_status_idx"),
            models.Index(fields=["assignment", "student", "created_at"], name="hw_sub_assign_student_idx"),
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


class ExamPaper(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"
    MODE_TIMED = "timed"
    MODE_DEADLINE = "deadline"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "草稿"),
        (STATUS_PUBLISHED, "已发布"),
        (STATUS_ARCHIVED, "已归档"),
    ]
    MODE_CHOICES = [
        (MODE_TIMED, "定时考试"),
        (MODE_DEADLINE, "DL考试"),
    ]

    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.CASCADE,
        related_name="exam_papers",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_papers",
    )
    title = models.CharField("考试标题", max_length=255)
    description = models.TextField("考试说明", blank=True)
    mode = models.CharField("考试模式", max_length=16, choices=MODE_CHOICES, default=MODE_DEADLINE)
    duration_minutes = models.PositiveIntegerField("考试时长（分钟）", default=60)
    start_at = models.DateTimeField("允许开始时间", null=True, blank=True)
    end_at = models.DateTimeField("截止时间", null=True, blank=True)
    proctoring_enabled = models.BooleanField("是否开启监考记录", default=False)
    access_code = models.CharField("考试入口口令", max_length=6, blank=True)
    access_code_generated_at = models.DateTimeField("口令生成时间", null=True, blank=True)
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "考试试卷"
        verbose_name_plural = "考试试卷"
        indexes = [
            models.Index(fields=["teacher", "status", "is_active"], name="exam_paper_teacher_status_idx"),
            models.Index(fields=["course", "status", "is_active"], name="exam_paper_course_status_idx"),
            models.Index(fields=["access_code", "is_active"], name="exam_paper_access_code_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    def publish(self) -> bool:
        if not self.is_active or self.status == self.STATUS_PUBLISHED:
            return False
        self.status = self.STATUS_PUBLISHED
        return True


class ExamQuestion(models.Model):
    QUESTION_TYPE_SINGLE_CHOICE = "single_choice"
    QUESTION_TYPE_TRUE_FALSE = "true_false"
    QUESTION_TYPE_PROGRAMMING = "programming"

    QUESTION_TYPE_CHOICES = [
        (QUESTION_TYPE_SINGLE_CHOICE, "单选题"),
        (QUESTION_TYPE_TRUE_FALSE, "判断题"),
        (QUESTION_TYPE_PROGRAMMING, "编程题"),
    ]

    paper = models.ForeignKey(ExamPaper, on_delete=models.CASCADE, related_name="questions")
    question_no = models.PositiveIntegerField("题号")
    question_type = models.CharField("题型", max_length=32, choices=QUESTION_TYPE_CHOICES, default=QUESTION_TYPE_SINGLE_CHOICE)
    stem = models.TextField("题干")
    options_json = models.JSONField("选项", default=dict, blank=True)
    correct_answer = models.CharField("正确答案", max_length=1)
    analysis = models.TextField("解析", blank=True)
    score = models.DecimalField("分值", max_digits=6, decimal_places=2, default=1)
    wrong_point_label = models.CharField("错点标签", max_length=128, blank=True)
    image_path = models.CharField("题目图片相对路径", max_length=500, blank=True)
    source_snapshot_json = models.JSONField("来源快照", default=dict, blank=True)
    is_important = models.BooleanField("是否标记重点", default=False)
    important_note = models.TextField("重点说明", blank=True)
    important_marked_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="important_exam_questions",
    )
    important_marked_at = models.DateTimeField("重点标记时间", null=True, blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["question_no", "id"]
        verbose_name = "考试题目"
        verbose_name_plural = "考试题目"
        constraints = [
            models.UniqueConstraint(
                fields=["paper", "question_no"],
                condition=models.Q(is_active=True),
                name="exam_q_paper_no_active_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["paper", "is_active", "question_no"], name="exam_question_paper_idx"),
            models.Index(fields=["wrong_point_label", "is_active"], name="exam_question_wrong_point_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.paper.title} - 第{self.question_no}题"


class ExamQuestionAnalysisBlock(models.Model):
    SOURCE_AI = "ai"
    SOURCE_TEACHER = "teacher"
    SOURCE_STUDENT = "student"
    SOURCE_MERGED = "merged"

    SOURCE_CHOICES = [
        (SOURCE_AI, "AI 解析"),
        (SOURCE_TEACHER, "老师解析"),
        (SOURCE_STUDENT, "学生贡献"),
        (SOURCE_MERGED, "汇总解析"),
    ]

    question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE, related_name="analysis_blocks")
    source_type = models.CharField("解析来源", max_length=32, choices=SOURCE_CHOICES, default=SOURCE_TEACHER)
    content_md = models.TextField("解析 Markdown")
    contributors_json = models.JSONField("贡献者快照", default=list, blank=True)
    created_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_exam_analysis_blocks",
    )
    is_visible = models.BooleanField("是否展示", default=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["question_id", "sort_order", "id"]
        verbose_name = "考试题目解析块"
        verbose_name_plural = "考试题目解析块"
        indexes = [
            models.Index(fields=["question", "is_visible", "sort_order"], name="exam_analysis_q_visible_idx"),
            models.Index(fields=["source_type", "is_visible"], name="exam_analysis_source_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.question} - {self.get_source_type_display()}"


class ExamQuestionAnalysisSuggestion(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_MERGED = "merged"

    STATUS_CHOICES = [
        (STATUS_PENDING, "待审核"),
        (STATUS_ACCEPTED, "已采纳"),
        (STATUS_REJECTED, "已忽略"),
        (STATUS_MERGED, "已汇总"),
    ]

    question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE, related_name="analysis_suggestions")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="exam_analysis_suggestions")
    session = models.ForeignKey(
        "ExamSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analysis_suggestions",
    )
    content_md = models.TextField("学生解析 Markdown")
    status = models.CharField("状态", max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    accepted_block = models.ForeignKey(
        ExamQuestionAnalysisBlock,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_suggestions",
    )
    reviewed_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_exam_analysis_suggestions",
    )
    reviewed_at = models.DateTimeField("审核时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "考试题目解析建议"
        verbose_name_plural = "考试题目解析建议"
        indexes = [
            models.Index(fields=["question", "status", "created_at"], name="exam_analysis_sug_q_idx"),
            models.Index(fields=["student", "status", "created_at"], name="exam_analysis_sug_stu_idx"),
            models.Index(fields=["session", "status"], name="exam_analysis_sug_sess_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.question} - {self.student.display_name}"


class StudentSiteMessage(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="site_messages")
    source_suggestion = models.ForeignKey(
        ExamQuestionAnalysisSuggestion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_site_messages",
    )
    title = models.CharField("标题", max_length=160)
    body = models.TextField("正文", blank=True)
    target_href = models.CharField("跳转链接", max_length=500, blank=True)
    is_read = models.BooleanField("是否已读", default=False)
    read_at = models.DateTimeField("阅读时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "学生站内信"
        verbose_name_plural = "学生站内信"
        indexes = [
            models.Index(fields=["student", "is_read", "created_at"], name="student_msg_unread_idx"),
            models.Index(fields=["source_suggestion"], name="student_msg_suggestion_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student.display_name} - {self.title}"


class ExamQuestionBankItem(models.Model):
    QUESTION_TYPE_SINGLE_CHOICE = "single_choice"
    SOURCE_MANUAL = "manual"
    SOURCE_IMPORT = "import"
    SOURCE_SCRAPE = "scrape"

    QUESTION_TYPE_CHOICES = [
        (QUESTION_TYPE_SINGLE_CHOICE, "单选题"),
    ]
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "手动录入"),
        (SOURCE_IMPORT, "文件导入"),
        (SOURCE_SCRAPE, "统一爬取"),
    ]

    course = models.ForeignKey(
        Course,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_question_bank_items",
    )
    content = models.ForeignKey(
        CourseContent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_question_bank_items",
    )
    level_code = models.CharField("等级", max_length=32, blank=True)
    knowledge_point = models.CharField("知识点", max_length=128)
    source = models.CharField("来源", max_length=32, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    source_label = models.CharField("来源说明", max_length=255, blank=True)
    source_url = models.CharField("来源链接", max_length=500, blank=True)
    question_type = models.CharField("题型", max_length=32, choices=QUESTION_TYPE_CHOICES, default=QUESTION_TYPE_SINGLE_CHOICE)
    stem = models.TextField("题面")
    options_json = models.JSONField("选项", default=dict, blank=True)
    correct_answer = models.CharField("答案", max_length=1)
    analysis = models.TextField("解析", blank=True)
    score = models.DecimalField("默认分值", max_digits=6, decimal_places=2, default=1)
    image_path = models.CharField("题目图片相对路径", max_length=500, blank=True)
    source_snapshot_json = models.JSONField("来源快照", default=dict, blank=True)
    created_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_exam_question_bank_items",
    )
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["course_id", "level_code", "knowledge_point", "id"]
        verbose_name = "考试题库题目"
        verbose_name_plural = "考试题库题目"
        indexes = [
            models.Index(fields=["course", "is_active", "level_code"], name="exam_bank_course_lvl_idx"),
            models.Index(fields=["knowledge_point", "is_active"], name="exam_bank_kp_idx"),
            models.Index(fields=["source", "is_active"], name="exam_bank_source_idx"),
            models.Index(fields=["created_by", "is_active"], name="exam_bank_creator_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.knowledge_point} - {self.stem[:32]}"


class ExamQuestionBankImportJob(models.Model):
    STATUS_UPLOADED = "uploaded"
    STATUS_RENDERING = "rendering"
    STATUS_OCR_RUNNING = "ocr_running"
    STATUS_OCR_DONE = "ocr_done"
    STATUS_FAILED = "failed"
    STATUS_IMPORTED = "imported"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_UPLOADED, "已上传"),
        (STATUS_RENDERING, "页面截图中"),
        (STATUS_OCR_RUNNING, "Qwen OCR 中"),
        (STATUS_OCR_DONE, "OCR 已完成"),
        (STATUS_FAILED, "识别失败"),
        (STATUS_IMPORTED, "已入题库"),
        (STATUS_CANCELLED, "已取消"),
    ]

    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.CASCADE,
        related_name="exam_question_bank_import_jobs",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_question_bank_import_jobs",
    )
    level_code = models.CharField("级别/类别", max_length=32)
    title = models.CharField("试卷标题", max_length=255)
    year = models.PositiveSmallIntegerField("年份", null=True, blank=True)
    month = models.PositiveSmallIntegerField("月份", null=True, blank=True)
    source_pdf_id = models.CharField("来源 PDF ID", max_length=128)
    source_pdf = models.FileField("源 PDF", upload_to="exam_paper_imports/%Y/%m/%d")
    source_filename = models.CharField("原始文件名", max_length=255)
    source_sha256 = models.CharField("源文件内容哈希", max_length=64, blank=True, default="")
    status = models.CharField("状态", max_length=32, choices=STATUS_CHOICES, default=STATUS_UPLOADED)
    status_notes = models.TextField("状态备注", blank=True)
    error_message = models.TextField("错误信息", blank=True)
    workspace_relative_path = models.CharField("中间产物相对路径", max_length=500, blank=True)
    rendered_pages_json = models.JSONField("页面截图", default=list, blank=True)
    raw_ocr_json = models.JSONField("逐页 OCR 结果", default=list, blank=True)
    qwen_model = models.CharField("Qwen OCR 模型", max_length=128, blank=True)
    qwen_base_url = models.CharField("Qwen Base URL", max_length=500, blank=True)
    page_count = models.PositiveIntegerField("PDF 页数", default=0)
    rendered_page_count = models.PositiveIntegerField("已截图页数", default=0)
    ocr_page_count = models.PositiveIntegerField("已 OCR 页数", default=0)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "考试题库 PDF 导入任务"
        verbose_name_plural = "考试题库 PDF 导入任务"
        indexes = [
            models.Index(fields=["teacher", "status", "is_active"], name="exam_qb_imp_teacher_idx"),
            models.Index(fields=["course", "level_code", "status"], name="exam_qb_imp_course_idx"),
            models.Index(fields=["source_pdf_id", "status"], name="exam_qb_imp_pdf_idx"),
            models.Index(fields=["source_sha256", "created_at"], name="exam_qb_imp_sha_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(month__isnull=True) | (models.Q(month__gte=1) & models.Q(month__lte=12)),
                name="exam_qb_imp_month_ck",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} - {self.get_status_display()}"


class ExamQuestionBankPaper(models.Model):
    SOURCE_HERMES = "hermes"
    SOURCE_LOCAL_OCR = "local_ocr"
    ANALYSIS_STATUS_NOT_STARTED = "not_started"
    ANALYSIS_STATUS_RUNNING = "running"
    ANALYSIS_STATUS_COMPLETED = "completed"
    ANALYSIS_STATUS_PARTIAL = "partial"
    ANALYSIS_STATUS_FAILED = "failed"

    SOURCE_CHOICES = [
        (SOURCE_HERMES, "Hermes 题库"),
        (SOURCE_LOCAL_OCR, "本地 OCR 导入"),
    ]
    ANALYSIS_STATUS_CHOICES = [
        (ANALYSIS_STATUS_NOT_STARTED, "未生成"),
        (ANALYSIS_STATUS_RUNNING, "解析中"),
        (ANALYSIS_STATUS_COMPLETED, "解析完成"),
        (ANALYSIS_STATUS_PARTIAL, "部分完成"),
        (ANALYSIS_STATUS_FAILED, "解析失败"),
    ]

    level = models.CharField("级别", max_length=32)
    year = models.PositiveSmallIntegerField("年份")
    month = models.PositiveSmallIntegerField("月份")
    source_pdf_id = models.CharField("来源 PDF ID", max_length=128)
    source_file = models.CharField("来源文件", max_length=255, blank=True)
    title = models.CharField("试卷标题", max_length=255)
    import_batch_uid = models.CharField("导入批次 UID", max_length=128, blank=True)
    source = models.CharField("来源", max_length=32, choices=SOURCE_CHOICES, default=SOURCE_HERMES)
    analysis_generation_status = models.CharField(
        "AI 解析状态",
        max_length=32,
        choices=ANALYSIS_STATUS_CHOICES,
        default=ANALYSIS_STATUS_NOT_STARTED,
    )
    analysis_generation_started_at = models.DateTimeField("AI 解析开始时间", null=True, blank=True)
    analysis_generation_completed_at = models.DateTimeField("AI 解析完成时间", null=True, blank=True)
    analysis_generation_total_count = models.PositiveIntegerField("AI 解析总题数", default=0)
    analysis_generation_done_count = models.PositiveIntegerField("AI 解析完成题数", default=0)
    analysis_generation_failed_count = models.PositiveIntegerField("AI 解析失败题数", default=0)
    analysis_generation_error = models.TextField("AI 解析错误", blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-year", "-month", "level", "source_pdf_id"]
        verbose_name = "考试题库试卷快照"
        verbose_name_plural = "考试题库试卷快照"
        constraints = [
            models.UniqueConstraint(fields=["source", "source_pdf_id"], name="exam_bank_paper_src_uniq"),
            models.CheckConstraint(
                check=models.Q(month__gte=1, month__lte=12),
                name="exam_bank_paper_month_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["level", "year", "month", "is_active"], name="exam_bank_paper_lookup_idx"),
            models.Index(fields=["source", "source_pdf_id"], name="exam_bank_paper_src_idx"),
        ]

    def __str__(self) -> str:
        return self.title


class ExamKnowledgePointMap(models.Model):
    subject = models.CharField("所属学科", max_length=32)
    category_code = models.CharField("所属类别", max_length=32)
    level_1 = models.CharField("一级目录", max_length=128)
    level_2 = models.CharField("二级目录", max_length=128)
    level_3 = models.CharField("三级目录", max_length=255, blank=True)
    source_path = models.CharField("来源文件", max_length=255, blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["subject", "category_code", "sort_order", "id"]
        verbose_name = "考试知识点映射"
        verbose_name_plural = "考试知识点映射"
        constraints = [
            models.UniqueConstraint(
                fields=["subject", "category_code", "level_1", "level_2", "level_3"],
                name="exam_kp_map_unique_path",
            ),
        ]
        indexes = [
            models.Index(fields=["subject", "category_code", "is_active"], name="exam_kp_map_scope_idx"),
            models.Index(fields=["level_1", "level_2"], name="exam_kp_map_l1_l2_idx"),
            models.Index(fields=["level_3"], name="exam_kp_map_l3_idx"),
        ]

    def __str__(self) -> str:
        parts = [self.subject, self.category_code, self.level_1, self.level_2, self.level_3]
        return " / ".join(part for part in parts if part)


class ExamQuestionBankQuestion(models.Model):
    QUESTION_TYPE_SINGLE_CHOICE = "single_choice"
    QUESTION_TYPE_TRUE_FALSE = "true_false"
    QUESTION_TYPE_PROGRAMMING = "programming"
    QUESTION_TYPE_RAW_MARKDOWN = "raw_markdown"

    QUESTION_TYPE_CHOICES = [
        (QUESTION_TYPE_SINGLE_CHOICE, "单选题"),
        (QUESTION_TYPE_TRUE_FALSE, "判断题"),
        (QUESTION_TYPE_PROGRAMMING, "编程题"),
        (QUESTION_TYPE_RAW_MARKDOWN, "OCR Markdown 页"),
    ]

    paper = models.ForeignKey(ExamQuestionBankPaper, on_delete=models.CASCADE, related_name="questions")
    question_uid = models.CharField("外部题目 UID", max_length=128, db_index=True)
    question_no = models.PositiveIntegerField("题号")
    question_type = models.CharField("题型", max_length=32, choices=QUESTION_TYPE_CHOICES)
    stem_md = models.TextField("题干 Markdown")
    answer_json = models.JSONField("标准答案", default=dict, blank=True)
    analysis_md = models.TextField("解析 Markdown", blank=True)
    programming_json = models.JSONField("编程题结构", default=dict, blank=True)
    full_json = models.JSONField("Hermes 原始结构", default=dict, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["paper_id", "question_no", "id"]
        verbose_name = "考试题库题目快照"
        verbose_name_plural = "考试题库题目快照"
        constraints = [
            models.UniqueConstraint(fields=["paper", "question_uid"], name="exam_bank_q_uid_uniq"),
            models.UniqueConstraint(fields=["paper", "question_no"], name="exam_bank_q_no_uniq"),
        ]
        indexes = [
            models.Index(fields=["paper", "question_no"], name="exam_bank_q_paper_no_idx"),
            models.Index(fields=["question_type"], name="exam_bank_q_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.paper.title} - 第{self.question_no}题"


class ExamQuestionBankOption(models.Model):
    question = models.ForeignKey(ExamQuestionBankQuestion, on_delete=models.CASCADE, related_name="options")
    option_key = models.CharField("选项", max_length=16)
    option_text_md = models.TextField("选项 Markdown", blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)

    class Meta:
        ordering = ["question_id", "sort_order", "option_key"]
        verbose_name = "考试题库选项快照"
        verbose_name_plural = "考试题库选项快照"
        constraints = [
            models.UniqueConstraint(fields=["question", "option_key"], name="exam_bank_opt_key_uniq"),
        ]
        indexes = [
            models.Index(fields=["question", "sort_order"], name="exam_bank_opt_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.question_id} / {self.option_key}"


class ExamQuestionBankAsset(models.Model):
    question = models.ForeignKey(ExamQuestionBankQuestion, on_delete=models.CASCADE, related_name="assets")
    asset_uid = models.CharField("外部资源 UID", max_length=128, blank=True, default="")
    asset_role = models.CharField("资源角色", max_length=32)
    asset_type = models.CharField("资源类型", max_length=32, blank=True)
    relative_path = models.CharField("相对路径", max_length=500, blank=True)
    public_url = models.CharField("公开 URL", max_length=500, blank=True)
    alt = models.CharField("替代文本", max_length=255, blank=True)
    width = models.PositiveIntegerField("宽度", null=True, blank=True)
    height = models.PositiveIntegerField("高度", null=True, blank=True)

    class Meta:
        ordering = ["question_id", "id"]
        verbose_name = "考试题库图片资源快照"
        verbose_name_plural = "考试题库图片资源快照"
        constraints = [
            models.UniqueConstraint(
                fields=["question", "asset_uid"],
                condition=~models.Q(asset_uid=""),
                name="exam_bank_asset_uid_uniq",
            ),
            models.UniqueConstraint(
                fields=["question", "asset_role", "relative_path"],
                condition=~models.Q(relative_path=""),
                name="exam_bank_asset_path_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["question", "asset_role"], name="exam_bank_asset_role_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.question_id} / {self.asset_role} / {self.relative_path or self.asset_uid}"


class PublishedExam(models.Model):
    MODE_TIMED = "timed"
    MODE_DEADLINE = "deadline"
    STATUS_DRAFT = "draft"
    STATUS_SCHEDULED = "scheduled"
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"
    STATUS_ARCHIVED = "archived"

    MODE_CHOICES = [
        (MODE_TIMED, "定时考试"),
        (MODE_DEADLINE, "DL考试"),
    ]
    STATUS_CHOICES = [
        (STATUS_DRAFT, "草稿"),
        (STATUS_SCHEDULED, "已定时"),
        (STATUS_ACTIVE, "进行中"),
        (STATUS_CLOSED, "已结束"),
        (STATUS_ARCHIVED, "已归档"),
    ]

    paper = models.ForeignKey(ExamQuestionBankPaper, on_delete=models.PROTECT, related_name="published_exams")
    title = models.CharField("考试标题", max_length=255)
    level = models.CharField("级别", max_length=32)
    mode = models.CharField("考试模式", max_length=16, choices=MODE_CHOICES, default=MODE_DEADLINE)
    duration_minutes = models.PositiveIntegerField("考试时长（分钟）", default=60)
    starts_at = models.DateTimeField("开始时间", null=True, blank=True)
    ends_at = models.DateTimeField("结束/截止时间", null=True, blank=True)
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_published_exams",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "发布考试"
        verbose_name_plural = "发布考试"
        indexes = [
            models.Index(fields=["level", "status"], name="pub_exam_level_status_idx"),
            models.Index(fields=["status", "starts_at"], name="pub_exam_status_start_idx"),
            models.Index(fields=["paper", "status"], name="pub_exam_paper_status_idx"),
        ]

    def __str__(self) -> str:
        return self.title


class PublishedExamSession(models.Model):
    STATUS_NOT_STARTED = "not_started"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_SUBMITTED = "submitted"
    STATUS_GRADED = "graded"

    STATUS_CHOICES = [
        (STATUS_NOT_STARTED, "未开始"),
        (STATUS_IN_PROGRESS, "考试中"),
        (STATUS_SUBMITTED, "已提交"),
        (STATUS_GRADED, "已判分"),
    ]

    published_exam = models.ForeignKey(PublishedExam, on_delete=models.CASCADE, related_name="sessions")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="published_exam_sessions")
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_NOT_STARTED)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    submitted_at = models.DateTimeField("提交时间", null=True, blank=True)
    score = models.DecimalField("得分", max_digits=7, decimal_places=2, default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "发布考试学生场次"
        verbose_name_plural = "发布考试学生场次"
        constraints = [
            models.UniqueConstraint(fields=["published_exam", "student"], name="pub_exam_sess_unique"),
        ]
        indexes = [
            models.Index(fields=["student", "status"], name="pub_exam_sess_student_idx"),
            models.Index(fields=["published_exam", "status"], name="pub_exam_sess_exam_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student.display_name} - {self.published_exam.title}"


class PublishedExamAnswer(models.Model):
    session = models.ForeignKey(PublishedExamSession, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(ExamQuestionBankQuestion, on_delete=models.PROTECT, related_name="published_answers")
    answer_json = models.JSONField("学生答案", default=dict, blank=True)
    is_correct = models.BooleanField("是否正确", default=False)
    score = models.DecimalField("得分", max_digits=7, decimal_places=2, default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["question_id", "id"]
        verbose_name = "发布考试作答"
        verbose_name_plural = "发布考试作答"
        constraints = [
            models.UniqueConstraint(fields=["session", "question"], name="pub_exam_answer_unique"),
        ]
        indexes = [
            models.Index(fields=["session", "is_correct"], name="pub_exam_ans_correct_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} - 第{self.question.question_no}题"


class ExamSession(models.Model):
    SESSION_TYPE_EXAM = "exam"
    SESSION_TYPE_FULL_PRACTICE = "full_practice"
    SESSION_TYPE_WRONG_PRACTICE = "wrong_practice"
    SESSION_TYPE_FREE_PRACTICE = "free_practice"

    STATUS_ASSIGNED = "assigned"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_SUBMITTED = "submitted"
    STATUS_AUTO_CHECKED = "auto_checked"
    STATUS_EXPIRED = "expired"
    STATUS_INVALIDATED = "invalidated"

    STATUS_CHOICES = [
        (STATUS_ASSIGNED, "待开始"),
        (STATUS_IN_PROGRESS, "考试中"),
        (STATUS_SUBMITTED, "已交卷"),
        (STATUS_AUTO_CHECKED, "已判分"),
        (STATUS_EXPIRED, "已过期"),
        (STATUS_INVALIDATED, "已作废"),
    ]
    SESSION_TYPE_CHOICES = [
        (SESSION_TYPE_EXAM, "正式考试"),
        (SESSION_TYPE_FULL_PRACTICE, "整卷练习"),
        (SESSION_TYPE_WRONG_PRACTICE, "错题练习"),
        (SESSION_TYPE_FREE_PRACTICE, "自由练习"),
    ]

    paper = models.ForeignKey(ExamPaper, on_delete=models.CASCADE, related_name="sessions")
    exam_run = models.ForeignKey(
        "ExamRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="exam_sessions")
    assigned_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_exam_sessions",
    )
    attempt_no = models.PositiveIntegerField("考试次数", default=1)
    session_type = models.CharField("场次类型", max_length=32, choices=SESSION_TYPE_CHOICES, default=SESSION_TYPE_EXAM)
    question_scope_json = models.JSONField("练习题目范围", default=dict, blank=True)
    status = models.CharField("考试状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_ASSIGNED)
    total_count = models.PositiveIntegerField("总题数", default=0)
    correct_count = models.PositiveIntegerField("正确数", default=0)
    wrong_count = models.PositiveIntegerField("错误数", default=0)
    total_score = models.DecimalField("总分", max_digits=7, decimal_places=2, default=0)
    earned_score = models.DecimalField("得分", max_digits=7, decimal_places=2, default=0)
    switch_count = models.PositiveIntegerField("切屏次数", default=0)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    submitted_at = models.DateTimeField("提交时间", null=True, blank=True)
    checked_at = models.DateTimeField("判分时间", null=True, blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "考试场次"
        verbose_name_plural = "考试场次"
        constraints = [
            models.UniqueConstraint(
                fields=["paper", "student", "attempt_no"],
                condition=models.Q(is_active=True),
                name="exam_sess_student_attempt_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "status", "is_active"], name="exam_sess_student_status_idx"),
            models.Index(fields=["paper", "status", "is_active"], name="exam_sess_paper_status_idx"),
            models.Index(fields=["exam_run", "status", "is_active"], name="exam_sess_run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student.display_name} - {self.paper.title}"


class ExamRun(models.Model):
    paper = models.ForeignKey(ExamPaper, on_delete=models.CASCADE, related_name="runs")
    access_code = models.CharField("考试入口口令", max_length=6)
    generated_at = models.DateTimeField("口令生成时间", default=timezone.now)
    starts_at = models.DateTimeField("场次开始时间", null=True, blank=True)
    ends_at = models.DateTimeField("场次结束时间", null=True, blank=True)
    created_by = models.ForeignKey(
        PortalUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_exam_runs",
    )
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-generated_at", "-id"]
        verbose_name = "考试场次"
        verbose_name_plural = "考试场次"
        indexes = [
            models.Index(fields=["paper", "is_active", "generated_at"], name="exam_run_paper_time_idx"),
            models.Index(fields=["access_code", "is_active"], name="exam_run_code_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.paper.title} - {self.generated_at:%Y-%m-%d %H:%M}"


class ExamSubmissionAnswer(models.Model):
    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE, related_name="submission_answers")
    selected_answer = models.CharField("学生答案", max_length=1, blank=True)
    explanation_text = models.TextField("学生解析", blank=True)
    is_correct = models.BooleanField("是否正确", default=False)
    score = models.DecimalField("得分", max_digits=6, decimal_places=2, default=0)
    correct_answer_snapshot = models.CharField("正确答案快照", max_length=1)
    analysis_snapshot = models.TextField("解析快照", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["question_id", "id"]
        verbose_name = "考试作答明细"
        verbose_name_plural = "考试作答明细"
        constraints = [
            models.UniqueConstraint(fields=["session", "question"], name="exam_answer_unique"),
        ]
        indexes = [
            models.Index(fields=["session", "is_correct"], name="exam_ans_session_correct_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session} - 第{self.question.question_no}题"


class ExamProctorEvent(models.Model):
    EVENT_VISIBILITY_HIDDEN = "visibility_hidden"
    EVENT_BLUR = "blur"
    EVENT_FOCUS = "focus"
    EVENT_SCREEN_SHARE_STOPPED = "screen_share_stopped"
    EVENT_PASTE = "paste"

    EVENT_CHOICES = [
        (EVENT_VISIBILITY_HIDDEN, "页面隐藏"),
        (EVENT_BLUR, "窗口失焦"),
        (EVENT_FOCUS, "窗口聚焦"),
        (EVENT_SCREEN_SHARE_STOPPED, "停止共享屏幕"),
        (EVENT_PASTE, "粘贴"),
    ]

    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, related_name="proctor_events")
    event_type = models.CharField("事件类型", max_length=32, choices=EVENT_CHOICES)
    occurred_at = models.DateTimeField("发生时间", default=timezone.now)
    metadata_json = models.JSONField("事件元数据", default=dict, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        ordering = ["occurred_at", "id"]
        verbose_name = "考试监考事件"
        verbose_name_plural = "考试监考事件"
        indexes = [
            models.Index(fields=["session", "event_type", "occurred_at"], name="exam_proc_session_event_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} / {self.event_type}"


class HomeworkCompletionStat(models.Model):
    PERIOD_WEEK = "week"
    PERIOD_MONTH = "month"
    PERIOD_QUARTER = "quarter"

    PERIOD_TYPE_CHOICES = [
        (PERIOD_WEEK, "本周"),
        (PERIOD_MONTH, "本月"),
        (PERIOD_QUARTER, "本季度"),
    ]

    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.CASCADE,
        related_name="homework_completion_stats",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="homework_completion_stats",
    )
    period_type = models.CharField("周期类型", max_length=16, choices=PERIOD_TYPE_CHOICES)
    period_start = models.DateField("周期开始日期")
    period_end = models.DateField("周期结束日期")
    completed_count = models.PositiveIntegerField("已完成数量", default=0)
    incomplete_count = models.PositiveIntegerField("未完成数量", default=0)
    excluded_undated_count = models.PositiveIntegerField("排除的无截止日期数量", default=0)
    generated_at = models.DateTimeField("统计生成时间", default=timezone.now)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["period_start", "period_type", "teacher_id", "student_id", "id"]
        verbose_name = "作业完成统计"
        verbose_name_plural = "作业完成统计"
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "student", "period_type", "period_start", "period_end"],
                name="hw_completion_stat_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["teacher", "period_type", "period_start", "period_end"],
                name="hwc_stat_teacher_pd_idx",
            ),
            models.Index(
                fields=["student", "period_type", "period_start", "period_end"],
                name="hwc_stat_student_pd_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.teacher.full_name or self.teacher.username} / "
            f"{self.student.display_name} / {self.period_type}"
        )


class HomeworkCompletionStatMissingAssignment(models.Model):
    REASON_ONLINE_MISSING = "online_missing"
    REASON_REQUIREMENT_NOT_MARKED_COMPLETED = "requirement_not_marked_completed"
    REASON_UNDATED = "undated"

    REASON_CHOICES = [
        (REASON_ONLINE_MISSING, "在线题未形成完成态提交"),
        (REASON_REQUIREMENT_NOT_MARKED_COMPLETED, "要求型作业未标记完成"),
        (REASON_UNDATED, "无截止日期"),
    ]

    stat = models.ForeignKey(
        HomeworkCompletionStat,
        on_delete=models.CASCADE,
        related_name="missing_assignments",
    )
    assignment = models.ForeignKey(
        HomeworkAssignment,
        on_delete=models.CASCADE,
        related_name="completion_stat_missing_links",
    )
    reason = models.CharField("未完成原因", max_length=48, choices=REASON_CHOICES, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["stat_id", "assignment_id", "id"]
        verbose_name = "作业完成统计未完成作业"
        verbose_name_plural = "作业完成统计未完成作业"
        constraints = [
            models.UniqueConstraint(
                fields=["stat", "assignment"],
                name="hw_comp_stat_missing_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"stat={self.stat_id}, assignment={self.assignment_id}"


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


class ClassroomLiveSession(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_ENDED = "ended"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "上课中"),
        (STATUS_ENDED, "已下课"),
        (STATUS_FAILED, "异常结束"),
    ]

    VIEW_MODE_NORMAL = "normal"
    VIEW_MODE_SPOTLIGHT_STUDENT = "spotlight_student"
    VIEW_MODE_SPLIT_COMPARE = "split_compare"
    VIEW_MODE_CHOICES = [
        (VIEW_MODE_NORMAL, "常规视图"),
        (VIEW_MODE_SPOTLIGHT_STUDENT, "学生主屏"),
        (VIEW_MODE_SPLIT_COMPARE, "并列对比"),
    ]

    WORKSPACE_SCREEN_SHARE = "screen_share"
    WORKSPACE_CHOICES = [
        (WORKSPACE_SCREEN_SHARE, "屏幕共享"),
    ]

    teacher = models.ForeignKey(
        PortalUser,
        on_delete=models.CASCADE,
        related_name="classroom_live_sessions",
    )
    title = models.CharField("课堂标题", max_length=128, blank=True)
    status = models.CharField("课堂状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    workspace_type = models.CharField("工作区类型", max_length=32, choices=WORKSPACE_CHOICES, default=WORKSPACE_SCREEN_SHARE)
    livekit_room_name = models.CharField("LiveKit 房间名", max_length=128, unique=True)
    view_mode = models.CharField("老师视图模式", max_length=32, choices=VIEW_MODE_CHOICES, default=VIEW_MODE_NORMAL)
    spotlight_participant = models.ForeignKey(
        "ClassroomLiveParticipant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spotlight_sessions",
    )
    pinned_participant = models.ForeignKey(
        "ClassroomLiveParticipant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pinned_sessions",
    )
    started_at = models.DateTimeField("开始时间", default=timezone.now)
    ended_at = models.DateTimeField("结束时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        verbose_name = "实时课堂"
        verbose_name_plural = "实时课堂"
        constraints = [
            models.UniqueConstraint(
                fields=["teacher"],
                condition=models.Q(status="active"),
                name="uniq_active_classroom_session_per_teacher",
            ),
        ]
        indexes = [
            models.Index(fields=["teacher", "status", "-started_at"], name="cls_teacher_status_started_idx"),
            models.Index(fields=["status", "-started_at"], name="cls_status_started_idx"),
        ]

    def __str__(self) -> str:
        return self.title or f"{self.teacher.full_name} 实时课堂"

    @classmethod
    def build_room_name(cls, teacher: PortalUser) -> str:
        return f"codemaster-live-{teacher.id}-{uuid.uuid4().hex[:12]}"

    def end(self, *, failed: bool = False) -> bool:
        if self.status != self.STATUS_ACTIVE:
            return False
        self.status = self.STATUS_FAILED if failed else self.STATUS_ENDED
        self.ended_at = timezone.now()
        return True

    def set_teacher_view(
        self,
        *,
        view_mode: str,
        spotlight_participant: "ClassroomLiveParticipant | None" = None,
        pinned_participant: "ClassroomLiveParticipant | None" = None,
    ) -> None:
        if view_mode not in {choice[0] for choice in self.VIEW_MODE_CHOICES}:
            raise ValueError("invalid_view_mode")
        if spotlight_participant and spotlight_participant.session_id != self.id:
            raise ValueError("spotlight_participant_not_in_session")
        if pinned_participant and pinned_participant.session_id != self.id:
            raise ValueError("pinned_participant_not_in_session")
        if view_mode == self.VIEW_MODE_NORMAL:
            spotlight_participant = None
        self.view_mode = view_mode
        self.spotlight_participant = spotlight_participant
        self.pinned_participant = pinned_participant


class ClassroomLiveParticipant(models.Model):
    ROLE_TEACHER = "teacher"
    ROLE_STUDENT = "student"
    ROLE_CHOICES = [
        (ROLE_TEACHER, "老师"),
        (ROLE_STUDENT, "学生"),
    ]

    CONNECTION_INVITED = "invited"
    CONNECTION_JOINED = "joined"
    CONNECTION_LEFT = "left"
    CONNECTION_CHOICES = [
        (CONNECTION_INVITED, "待加入"),
        (CONNECTION_JOINED, "已加入"),
        (CONNECTION_LEFT, "已离开"),
    ]

    SCREEN_NONE = "none"
    SCREEN_PENDING = "pending"
    SCREEN_SHARING = "sharing"
    SCREEN_STOPPED = "stopped"
    SCREEN_REJECTED = "rejected"
    SCREEN_CHOICES = [
        (SCREEN_NONE, "未投屏"),
        (SCREEN_PENDING, "等待投屏"),
        (SCREEN_SHARING, "投屏中"),
        (SCREEN_STOPPED, "已停止"),
        (SCREEN_REJECTED, "已拒绝"),
    ]

    DISPLAY_MONITOR = "monitor"

    session = models.ForeignKey(ClassroomLiveSession, on_delete=models.CASCADE, related_name="participants")
    portal_user = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="classroom_live_participants")
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="classroom_live_participants",
    )
    role = models.CharField("课堂角色", max_length=16, choices=ROLE_CHOICES)
    livekit_identity = models.CharField("LiveKit 身份", max_length=128)
    connection_state = models.CharField("连接状态", max_length=16, choices=CONNECTION_CHOICES, default=CONNECTION_INVITED)
    screen_state = models.CharField("投屏状态", max_length=16, choices=SCREEN_CHOICES, default=SCREEN_NONE)
    display_surface = models.CharField("共享屏幕类型", max_length=32, blank=True)
    joined_at = models.DateTimeField("加入时间", null=True, blank=True)
    left_at = models.DateTimeField("离开时间", null=True, blank=True)
    screen_started_at = models.DateTimeField("投屏开始时间", null=True, blank=True)
    screen_stopped_at = models.DateTimeField("投屏停止时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["role", "joined_at", "id"]
        verbose_name = "实时课堂成员"
        verbose_name_plural = "实时课堂成员"
        constraints = [
            models.UniqueConstraint(fields=["session", "portal_user"], name="uniq_classroom_participant_user"),
            models.UniqueConstraint(fields=["session", "livekit_identity"], name="uniq_classroom_participant_identity"),
        ]
        indexes = [
            models.Index(fields=["session", "role"], name="clp_session_role_idx"),
            models.Index(fields=["student", "connection_state"], name="clp_student_connection_idx"),
            models.Index(fields=["portal_user", "connection_state"], name="clp_user_connection_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} / {self.portal_user.full_name} / {self.role}"

    @classmethod
    def build_identity(cls, portal_user: PortalUser, session: ClassroomLiveSession) -> str:
        return f"{portal_user.role}-{portal_user.id}-session-{session.id}"

    def mark_joined(self) -> bool:
        if self.connection_state == self.CONNECTION_JOINED:
            return False
        self.connection_state = self.CONNECTION_JOINED
        self.joined_at = timezone.now()
        self.left_at = None
        return True

    def mark_left(self) -> bool:
        if self.connection_state == self.CONNECTION_LEFT:
            return False
        self.connection_state = self.CONNECTION_LEFT
        self.left_at = timezone.now()
        if self.screen_state == self.SCREEN_SHARING:
            self.screen_state = self.SCREEN_STOPPED
            self.screen_stopped_at = self.left_at
        return True

    def set_screen_state(self, *, screen_state: str, display_surface: str = "") -> None:
        if screen_state not in {choice[0] for choice in self.SCREEN_CHOICES}:
            raise ValueError("invalid_screen_state")
        normalized_surface = str(display_surface or "").strip()
        if screen_state == self.SCREEN_SHARING and normalized_surface != self.DISPLAY_MONITOR:
            raise ValueError("screen_share_requires_monitor")
        self.screen_state = screen_state
        self.display_surface = normalized_surface
        now = timezone.now()
        if screen_state == self.SCREEN_SHARING:
            self.screen_started_at = now
            self.screen_stopped_at = None
        elif screen_state in {self.SCREEN_STOPPED, self.SCREEN_REJECTED}:
            self.screen_stopped_at = now


class ClassroomLiveRecording(models.Model):
    TYPE_AUDIO = "audio"
    TYPE_SCREEN = "screen"
    TYPE_CHOICES = [
        (TYPE_AUDIO, "音频"),
        (TYPE_SCREEN, "录屏"),
    ]

    PROVIDER_LIVEKIT_EGRESS = "livekit_egress"
    PROVIDER_BROWSER_AUDIO = "browser_audio"
    PROVIDER_BROWSER_SCREEN = "browser_screen"
    PROVIDER_CHOICES = [
        (PROVIDER_LIVEKIT_EGRESS, "LiveKit Egress"),
        (PROVIDER_BROWSER_AUDIO, "浏览器录音"),
        (PROVIDER_BROWSER_SCREEN, "浏览器录屏"),
    ]

    STATUS_STARTING = "starting"
    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_STARTING, "启动中"),
        (STATUS_ACTIVE, "录制中"),
        (STATUS_COMPLETED, "已完成"),
        (STATUS_FAILED, "失败"),
    ]

    session = models.ForeignKey(ClassroomLiveSession, on_delete=models.CASCADE, related_name="recordings")
    recording_type = models.CharField("录制类型", max_length=16, choices=TYPE_CHOICES, default=TYPE_SCREEN)
    provider = models.CharField("录制服务", max_length=32, choices=PROVIDER_CHOICES, default=PROVIDER_LIVEKIT_EGRESS)
    status = models.CharField("录制状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_STARTING)
    egress_id = models.CharField("LiveKit Egress ID", max_length=128, blank=True)
    file_url = models.URLField("录制文件 URL", max_length=500, blank=True)
    file_path = models.CharField("录制文件路径", max_length=500, blank=True)
    file_size = models.PositiveBigIntegerField("文件大小", default=0)
    content_type = models.CharField("文件类型", max_length=120, blank=True)
    error_message = models.TextField("错误信息", blank=True)
    started_at = models.DateTimeField("开始时间", default=timezone.now)
    ended_at = models.DateTimeField("结束时间", null=True, blank=True)
    expires_at = models.DateTimeField("下载失效时间", null=True, blank=True)
    deleted_at = models.DateTimeField("文件删除时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        verbose_name = "实时课堂录制"
        verbose_name_plural = "实时课堂录制"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "recording_type"],
                condition=models.Q(status__in=["starting", "active"]),
                name="uniq_active_classroom_recording_type",
            ),
        ]
        indexes = [
            models.Index(fields=["session", "status"], name="clr_session_status_idx"),
            models.Index(fields=["session", "recording_type", "status"], name="clr_session_type_status_idx"),
            models.Index(fields=["expires_at", "deleted_at"], name="clr_expires_deleted_idx"),
            models.Index(fields=["egress_id"], name="clr_egress_id_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} / {self.recording_type} / {self.provider} / {self.status}"

    def mark_active(self, *, egress_id: str = "") -> None:
        self.status = self.STATUS_ACTIVE
        if egress_id:
            self.egress_id = egress_id
        self.error_message = ""

    def mark_completed(
        self,
        *,
        file_url: str = "",
        file_path: str = "",
        file_size: int | None = None,
        content_type: str = "",
    ) -> None:
        self.status = self.STATUS_COMPLETED
        self.file_url = file_url or self.file_url
        self.file_path = file_path or self.file_path
        if file_size is not None:
            self.file_size = max(int(file_size or 0), 0)
        if content_type:
            self.content_type = str(content_type or "").strip()[:120]
        self.ended_at = timezone.now()
        self.expires_at = self.expires_at or self.ended_at + timedelta(days=15)

    def mark_failed(self, message: str) -> None:
        self.status = self.STATUS_FAILED
        self.error_message = str(message or "录制服务异常").strip()
        self.ended_at = timezone.now()

    def is_download_available(self) -> bool:
        if self.status != self.STATUS_COMPLETED or not self.file_path:
            return False
        if self.deleted_at:
            return False
        expires_at = self.expires_at
        if not expires_at and self.ended_at:
            expires_at = self.ended_at + timedelta(days=15)
        if expires_at and expires_at <= timezone.now():
            return False
        return True


class ClassroomLiveActivity(models.Model):
    TYPE_SINGLE_CHOICE = "single_choice"
    TYPE_TRUE_FALSE = "true_false"
    TYPE_CHOICES = [
        (TYPE_SINGLE_CHOICE, "选择题"),
        (TYPE_TRUE_FALSE, "判断题"),
    ]

    STATUS_PUBLISHED = "published"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_PUBLISHED, "进行中"),
        (STATUS_CLOSED, "已关闭"),
    ]

    session = models.ForeignKey(ClassroomLiveSession, on_delete=models.CASCADE, related_name="activities")
    teacher = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="classroom_live_activities")
    activity_type = models.CharField("题型", max_length=32, choices=TYPE_CHOICES)
    title = models.CharField("标题", max_length=128)
    prompt_text = models.TextField("题面")
    options_json = models.JSONField("选项", default=dict, blank=True)
    correct_answer = models.CharField("正确答案", max_length=16, blank=True)
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_PUBLISHED)
    published_at = models.DateTimeField("发布时间", default=timezone.now)
    closed_at = models.DateTimeField("关闭时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-published_at", "-id"]
        verbose_name = "实时课堂任务"
        verbose_name_plural = "实时课堂任务"
        constraints = [
            models.UniqueConstraint(
                fields=["session"],
                condition=models.Q(status="published"),
                name="uniq_published_live_activity_per_session",
            ),
        ]
        indexes = [
            models.Index(fields=["session", "status", "-published_at"], name="cla_session_status_pub_idx"),
            models.Index(fields=["teacher", "-published_at"], name="cla_teacher_pub_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} / {self.title}"

    def close(self) -> bool:
        if self.status == self.STATUS_CLOSED:
            return False
        self.status = self.STATUS_CLOSED
        self.closed_at = timezone.now()
        return True


class ClassroomLiveActivityResponse(models.Model):
    activity = models.ForeignKey(ClassroomLiveActivity, on_delete=models.CASCADE, related_name="responses")
    session = models.ForeignKey(ClassroomLiveSession, on_delete=models.CASCADE, related_name="activity_responses")
    participant = models.ForeignKey(
        ClassroomLiveParticipant,
        on_delete=models.CASCADE,
        related_name="activity_responses",
    )
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="classroom_live_activity_responses")
    answer = models.CharField("答案", max_length=16)
    is_correct = models.BooleanField("是否正确", null=True, blank=True)
    correct_answer_snapshot = models.CharField("正确答案快照", max_length=16, blank=True)
    attempt_no = models.PositiveIntegerField("提交次数", default=1)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "实时课堂答题记录"
        verbose_name_plural = "实时课堂答题记录"
        indexes = [
            models.Index(fields=["activity", "student", "-created_at"], name="clar_activity_student_idx"),
            models.Index(fields=["session", "student", "-created_at"], name="clar_session_student_idx"),
            models.Index(fields=["activity", "answer"], name="clar_activity_answer_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.activity_id} / {self.student.display_name} / {self.answer}"
