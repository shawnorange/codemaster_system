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


class CourseContent(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="contents")
    slug = models.SlugField("内容标识", unique=True)
    title = models.CharField("内容名称", max_length=128)
    phase = models.CharField("所属阶段", max_length=64, blank=True)
    route_path = models.CharField("访问路由", max_length=255, unique=True)
    summary = models.TextField("内容说明", blank=True)
    is_active = models.BooleanField("是否启用", default=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "课程内容"
        verbose_name_plural = "课程内容"

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
