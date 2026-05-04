from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from entry.homework_completion_stats import build_homework_completion_stats
from entry.models import (
    Course,
    CourseContent,
    HomeworkAssignment,
    HomeworkCompletionStat,
    HomeworkCompletionStatMissingAssignment,
    HomeworkImportJob,
    HomeworkQuestion,
    HomeworkSubmission,
    PortalUser,
    Student,
)


class HomeworkCompletionStatsHelperTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teacher = PortalUser.objects.create(
            username="completion_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="完成率老师",
        )
        self.other_teacher = PortalUser.objects.create(
            username="completion_other_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="其他老师",
        )
        self.course = Course.objects.create(slug="completion-stats", title="Python", summary="completion stats")
        self.content = CourseContent.objects.create(
            course=self.course,
            slug="completion-stats-homework",
            title="流程控制",
            route_path="/student/python/completion-stats-homework",
            summary="completion stats content",
            is_active=True,
        )
        self.student = self.create_student("completion_student", "学生甲", self.teacher)
        self.other_student = self.create_student("completion_student_b", "学生乙", self.other_teacher)

    def create_student(self, username: str, display_name: str, teacher: PortalUser) -> Student:
        portal_user = PortalUser.objects.create(
            username=username,
            role=PortalUser.ROLE_STUDENT,
            full_name=display_name,
        )
        return Student.objects.create(
            user=portal_user,
            teacher_user=teacher,
            display_name=display_name,
            grade="四年级",
            campus="张江校区",
            primary_course_name=self.course.title,
            primary_track_name="基础语法",
            primary_level_name="P1",
        )

    def make_local_datetime_for_date(self, target_date) -> datetime:
        tz = timezone.get_current_timezone()
        target = datetime.combine(target_date, datetime.min.time())
        return timezone.make_aware(target, tz).replace(hour=10, minute=0, second=0, microsecond=0)

    def create_assignment(
        self,
        *,
        title: str,
        due_date,
        created_at: datetime | None = None,
        assigned_at: datetime | None = None,
        status: str = HomeworkAssignment.STATUS_ASSIGNED,
        completed_at: datetime | None = None,
    ) -> HomeworkAssignment:
        assignment = HomeworkAssignment.objects.create(
            teacher=self.teacher,
            student=self.student,
            content=self.content,
            title=title,
            description="helper test",
            due_date=due_date,
            status=status,
            assigned_at=assigned_at or created_at or timezone.now(),
            completed_at=completed_at,
            is_active=True,
        )
        if created_at is not None:
            HomeworkAssignment.objects.filter(id=assignment.id).update(created_at=created_at)
        assignment.refresh_from_db()
        return assignment

    def add_direct_question(self, *, assignment: HomeworkAssignment, question_no: int = 1) -> HomeworkQuestion:
        return HomeworkQuestion.objects.create(
            assignment=assignment,
            question_no=question_no,
            question_type=HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem=f"第 {question_no} 题",
            options_json={"A": "选项 A", "B": "选项 B"},
            correct_answer="A",
            analysis="测试解析",
            is_active=True,
        )

    def attach_source_import_job(
        self,
        *,
        assignment: HomeworkAssignment,
        source_filename: str,
        with_questions: bool = False,
    ) -> HomeworkImportJob:
        import_job = HomeworkImportJob.objects.create(
            teacher=assignment.teacher,
            assignment=assignment,
            source_file=SimpleUploadedFile("helper-source.txt", b"helper-source", content_type="text/plain"),
            source_filename=source_filename,
            source_sha256=f"helper-{assignment.id}",
            source_type=HomeworkImportJob.SOURCE_TYPE_TEXT,
            parse_status=HomeworkImportJob.STATUS_CONFIRMED,
            confirmed_at=timezone.now(),
            is_active=True,
        )
        assignment.source_import_job = import_job
        assignment.save(update_fields=["source_import_job", "updated_at"])
        if with_questions:
            HomeworkQuestion.objects.create(
                assignment=assignment,
                import_job=import_job,
                question_no=1,
                question_type=HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                stem="第 1 题",
                options_json={"A": "选项 A", "B": "选项 B"},
                correct_answer="A",
                analysis="测试解析",
                is_active=True,
            )
        return import_job

    def create_submission(
        self,
        *,
        assignment: HomeworkAssignment,
        status: str,
        submitted_at: datetime | None = None,
        created_at: datetime | None = None,
        correct_count: int = 2,
        wrong_count: int = 1,
    ) -> HomeworkSubmission:
        if status == HomeworkSubmission.STATUS_IN_PROGRESS:
            submitted_at = None
        elif submitted_at is None:
            submitted_at = timezone.now()
        total_count = max(int(correct_count or 0), 0) + max(int(wrong_count or 0), 0)
        submission = HomeworkSubmission.objects.create(
            assignment=assignment,
            student=self.student,
            status=status,
            total_count=total_count,
            correct_count=correct_count,
            wrong_count=wrong_count,
            score=Decimal("100.00") if total_count and wrong_count == 0 else Decimal("80.00"),
            started_at=(submitted_at - timedelta(minutes=10)) if submitted_at else timezone.now(),
            submitted_at=submitted_at,
            checked_at=submitted_at if submitted_at else None,
            is_active=True,
        )
        if created_at is not None:
            HomeworkSubmission.objects.filter(id=submission.id).update(created_at=created_at)
            submission.refresh_from_db()
        return submission

    def test_week_period_uses_due_date_not_created_at(self) -> None:
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        this_week_due = week_start + timedelta(days=1)
        next_week_due = week_start + timedelta(days=7)

        out_of_scope = self.create_assignment(
            title="created this week but due next week",
            due_date=next_week_due,
            created_at=self.make_local_datetime_for_date(this_week_due),
        )
        in_scope = self.create_assignment(
            title="created last week but due this week",
            due_date=this_week_due,
            created_at=self.make_local_datetime_for_date(week_start - timedelta(days=6)),
        )

        stats = build_homework_completion_stats(self.teacher, student=self.student, period_type="week")

        assignment_ids = [item["assignment_id"] for item in stats["student_stats"][0]["assignments"]]
        self.assertIn(in_scope.id, assignment_ids)
        self.assertNotIn(out_of_scope.id, assignment_ids)

    def test_month_and_quarter_periods_use_due_date(self) -> None:
        today = timezone.localdate()
        month_start = today.replace(day=1)
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = today.replace(month=quarter_start_month, day=1)

        month_in_scope = self.create_assignment(
            title="month due in scope",
            due_date=month_start + timedelta(days=2),
            created_at=self.make_local_datetime_for_date(month_start - timedelta(days=10)),
        )
        month_out_of_scope = self.create_assignment(
            title="month due out of scope",
            due_date=month_start - timedelta(days=1),
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=2)),
        )
        quarter_in_scope = self.create_assignment(
            title="quarter due in scope",
            due_date=quarter_start + timedelta(days=10),
            created_at=self.make_local_datetime_for_date(quarter_start - timedelta(days=20)),
        )

        month_stats = build_homework_completion_stats(self.teacher, student=self.student, period_type="month")
        quarter_stats = build_homework_completion_stats(self.teacher, student=self.student, period_type="quarter")

        month_ids = {item["assignment_id"] for item in month_stats["student_stats"][0]["assignments"]}
        quarter_ids = {item["assignment_id"] for item in quarter_stats["student_stats"][0]["assignments"]}
        self.assertIn(month_in_scope.id, month_ids)
        self.assertNotIn(month_out_of_scope.id, month_ids)
        self.assertIn(quarter_in_scope.id, quarter_ids)

    def test_online_and_requirement_completion_rules_and_question_detection(self) -> None:
        today = timezone.localdate()

        completed_online_direct = self.create_assignment(title="completed online direct", due_date=today)
        self.add_direct_question(assignment=completed_online_direct)
        self.create_submission(
            assignment=completed_online_direct,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            submitted_at=self.make_local_datetime_for_date(today),
        )

        in_progress_online = self.create_assignment(title="in progress online", due_date=today)
        self.add_direct_question(assignment=in_progress_online)
        self.create_submission(
            assignment=in_progress_online,
            status=HomeworkSubmission.STATUS_IN_PROGRESS,
            created_at=self.make_local_datetime_for_date(today),
        )

        missing_online = self.create_assignment(title="missing online", due_date=today)
        self.attach_source_import_job(
            assignment=missing_online,
            source_filename="missing-online.txt",
            with_questions=True,
        )

        completed_requirement = self.create_assignment(
            title="completed requirement",
            due_date=today,
            status=HomeworkAssignment.STATUS_COMPLETED,
            completed_at=self.make_local_datetime_for_date(today),
        )

        incomplete_requirement = self.create_assignment(
            title="incomplete requirement",
            due_date=today,
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )

        import_job_without_questions = self.create_assignment(
            title="import job without questions",
            due_date=today,
            status=HomeworkAssignment.STATUS_COMPLETED,
            completed_at=self.make_local_datetime_for_date(today),
        )
        self.attach_source_import_job(
            assignment=import_job_without_questions,
            source_filename="requirement-only.txt",
            with_questions=False,
        )

        stats = build_homework_completion_stats(self.teacher, student=self.student, period_type="week")
        items_by_title = {
            item["assignment"].title: item
            for item in stats["student_stats"][0]["assignments"]
        }

        self.assertTrue(items_by_title["completed online direct"]["has_online_questions"])
        self.assertTrue(items_by_title["completed online direct"]["is_completed"])
        self.assertFalse(items_by_title["in progress online"]["is_completed"])
        self.assertEqual(
            items_by_title["in progress online"]["completion_reason"],
            HomeworkCompletionStatMissingAssignment.REASON_ONLINE_MISSING,
        )
        self.assertFalse(items_by_title["missing online"]["is_completed"])
        self.assertTrue(items_by_title["completed requirement"]["is_completed"])
        self.assertFalse(items_by_title["incomplete requirement"]["is_completed"])
        self.assertFalse(items_by_title["import job without questions"]["has_online_questions"])
        self.assertTrue(items_by_title["import job without questions"]["is_completed"])
        self.assertEqual(stats["summary"]["completed_count"], 3)
        self.assertEqual(stats["summary"]["incomplete_count"], 3)

    def test_persist_creates_stat_and_missing_assignment_rows_without_duplicates(self) -> None:
        today = timezone.localdate()
        completed_online = self.create_assignment(title="completed online", due_date=today)
        self.add_direct_question(assignment=completed_online)
        self.create_submission(
            assignment=completed_online,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(today),
        )
        incomplete_online = self.create_assignment(title="incomplete online", due_date=today)
        self.add_direct_question(assignment=incomplete_online)
        incomplete_requirement = self.create_assignment(title="incomplete requirement", due_date=today)

        build_homework_completion_stats(
            self.teacher,
            student=self.student,
            period_type="week",
            persist=True,
        )
        build_homework_completion_stats(
            self.teacher,
            student=self.student,
            period_type="week",
            persist=True,
        )

        stat = HomeworkCompletionStat.objects.get(
            teacher=self.teacher,
            student=self.student,
            period_type="week",
        )
        self.assertEqual(stat.teacher_id, self.teacher.id)
        self.assertEqual(stat.student_id, self.student.id)
        self.assertEqual(stat.completed_count, 1)
        self.assertEqual(stat.incomplete_count, 2)
        self.assertEqual(stat.excluded_undated_count, 0)

        missing_rows = list(
            HomeworkCompletionStatMissingAssignment.objects.filter(stat=stat).order_by("assignment_id")
        )
        self.assertEqual(len(missing_rows), 2)
        self.assertEqual(
            {row.assignment_id for row in missing_rows},
            {incomplete_online.id, incomplete_requirement.id},
        )
        self.assertEqual(
            {row.reason for row in missing_rows},
            {
                HomeworkCompletionStatMissingAssignment.REASON_ONLINE_MISSING,
                HomeworkCompletionStatMissingAssignment.REASON_REQUIREMENT_NOT_MARKED_COMPLETED,
            },
        )
        self.assertEqual(
            HomeworkCompletionStatMissingAssignment.objects.filter(stat=stat, assignment=incomplete_online).count(),
            1,
        )

    def test_teacher_cannot_build_stats_for_other_teachers_student(self) -> None:
        with self.assertRaises(ValueError):
            build_homework_completion_stats(
                self.teacher,
                student=self.other_student,
                period_type="week",
            )
