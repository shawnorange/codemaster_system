from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import json
import re

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from entry.auth import AUTH_COOKIE_NAME, AUTH_COOKIE_SALT
from entry.models import (
    Course,
    CourseContent,
    HomeworkAssignment,
    HomeworkImportJob,
    HomeworkQuestion,
    HomeworkSubmission,
    PortalUser,
    Student,
    TeacherStudentAssignment,
)


class TeacherHomeworkStatsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teacher = PortalUser.objects.create(
            username="stats_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="统计老师",
        )
        self.other_teacher = PortalUser.objects.create(
            username="stats_other_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="别的老师",
        )
        self.course = Course.objects.create(slug="python-stats", title="Python", summary="统计测试课程")
        self.content = CourseContent.objects.create(
            course=self.course,
            slug="python-stats-homework",
            title="循环训练",
            route_path="/student/python/stats-homework",
            summary="统计测试作业内容",
            is_active=True,
        )
        self.student = self.create_student("stats_student", "学生甲", self.teacher)
        self.student_b = self.create_student("stats_student_b", "学生乙", self.teacher)
        self.out_of_scope_student = self.create_student("stats_student_c", "学生丙", self.other_teacher)
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.student,
            course=self.course,
            level_code="P1",
            is_active=True,
        )

    def sign_in(self, user: PortalUser) -> None:
        self.client.cookies[AUTH_COOKIE_NAME] = signing.dumps(
            {"username": user.username, "role": user.role},
            salt=AUTH_COOKIE_SALT,
        )

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
        teacher: PortalUser,
        student: Student,
        title: str,
        due_date,
        created_at: datetime | None = None,
        assigned_at: datetime | None = None,
        status: str = HomeworkAssignment.STATUS_ASSIGNED,
        completed_at: datetime | None = None,
    ) -> HomeworkAssignment:
        assignment = HomeworkAssignment.objects.create(
            teacher=teacher,
            student=student,
            content=self.content,
            title=title,
            description="统计测试作业",
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
            source_file=SimpleUploadedFile("teacher-homework-stats-source.txt", b"teacher-homework-stats", content_type="text/plain"),
            source_filename=source_filename,
            source_sha256=f"teacher-homework-stats-{assignment.id}",
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

    def create_submission(
        self,
        *,
        assignment: HomeworkAssignment,
        student: Student,
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
            student=student,
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

    def extract_json_script(self, response, script_id: str):
        content = response.content.decode("utf-8")
        match = re.search(
            rf'<script id="{re.escape(script_id)}" type="application/json">(.*?)</script>',
            content,
            re.S,
        )
        self.assertIsNotNone(match, f"missing json_script {script_id}")
        return json.loads(match.group(1))

    def test_teacher_homework_stats_pages_load_for_all_periods_and_use_due_date_label(self) -> None:
        self.sign_in(self.teacher)

        week_response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})
        month_response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})
        quarter_response = self.client.get(reverse("teacher-homework-stats"), {"period": "quarter"})

        self.assertEqual(week_response.status_code, 200)
        self.assertEqual(month_response.status_code, 200)
        self.assertEqual(quarter_response.status_code, 200)
        self.assertEqual(week_response.context["selected_period"], "week")
        self.assertEqual(month_response.context["selected_period"], "month")
        self.assertEqual(quarter_response.context["selected_period"], "quarter")
        self.assertEqual(week_response.context["period_field_label"], "截止日期")
        self.assertContains(week_response, "teacher-homework-stats-assigned-at-search")
        self.assertContains(week_response, 'assignedAtSearchInputId: "teacher-homework-stats-assigned-at-search"')
        month_titles = self.extract_json_script(month_response, "teacher-homework-stats-student-columns")
        quarter_titles = self.extract_json_script(quarter_response, "teacher-homework-stats-student-columns")
        self.assertIn("应完成作业数", month_titles)
        self.assertIn("已完成", month_titles)
        self.assertIn("未完成", month_titles)
        self.assertNotIn("submission_id", month_titles)
        self.assertNotIn("Homework Submission Detail", month_titles)
        self.assertIn("应完成作业数", quarter_titles)

    def test_week_stats_use_due_date_and_count_requirement_completion_without_submission(self) -> None:
        self.sign_in(self.teacher)
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        next_week_date = week_start + timedelta(days=7)
        week_due_date = week_start + timedelta(days=2)

        self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="created this week but due next week",
            due_date=next_week_date,
            created_at=self.make_local_datetime_for_date(week_due_date),
        )
        requirement_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="created last week but due this week",
            due_date=week_due_date,
            created_at=self.make_local_datetime_for_date(week_start - timedelta(days=6)),
            status=HomeworkAssignment.STATUS_COMPLETED,
            completed_at=self.make_local_datetime_for_date(week_due_date),
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["assigned_count"], 1)
        self.assertEqual(response.context["summary"]["completed_count"], 1)
        self.assertEqual(response.context["summary"]["incomplete_count"], 0)
        student_row = response.context["students"][0]
        self.assertEqual(student_row["assigned_count"], 1)
        self.assertEqual(student_row["completed_count"], 1)
        table_rows = [row for row in response.context["student_table_rows"] if row["assignment_id"] == requirement_assignment.id]
        self.assertEqual(len(table_rows), 1)
        self.assertEqual(table_rows[0]["completion_state_text"], "已完成")

    def test_month_student_detail_aggregates_per_student_and_uses_due_date_with_correct_counts(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        next_month_date = (month_start + timedelta(days=32)).replace(day=1)

        direct_online_completed = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="direct online completed",
            due_date=month_start + timedelta(days=5),
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=1)),
            assigned_at=self.make_local_datetime_for_date(month_start + timedelta(days=1)),
        )
        self.add_direct_question(assignment=direct_online_completed)
        self.create_submission(
            assignment=direct_online_completed,
            student=self.student,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            submitted_at=self.make_local_datetime_for_date(month_start + timedelta(days=2)),
            correct_count=3,
            wrong_count=1,
        )
        self.create_submission(
            assignment=direct_online_completed,
            student=self.student,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(month_start + timedelta(days=3)),
            correct_count=4,
            wrong_count=0,
        )

        online_in_progress = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="online in progress",
            due_date=month_start + timedelta(days=6),
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=2)),
        )
        self.add_direct_question(assignment=online_in_progress)
        self.create_submission(
            assignment=online_in_progress,
            student=self.student,
            status=HomeworkSubmission.STATUS_IN_PROGRESS,
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=3)),
        )

        online_without_submission = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="online without submission",
            due_date=month_start + timedelta(days=7),
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=3)),
        )
        self.attach_source_import_job(
            assignment=online_without_submission,
            source_filename="online-without-submission.txt",
            with_questions=True,
        )

        requirement_completed = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="requirement completed",
            due_date=month_start + timedelta(days=8),
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=4)),
            status=HomeworkAssignment.STATUS_COMPLETED,
            completed_at=self.make_local_datetime_for_date(month_start + timedelta(days=8)),
        )

        requirement_incomplete = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="requirement incomplete",
            due_date=month_start + timedelta(days=9),
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=5)),
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )
        self.attach_source_import_job(
            assignment=requirement_incomplete,
            source_filename="requirement-only.txt",
            with_questions=False,
        )

        self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="out of month",
            due_date=next_month_date,
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=6)),
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})

        rows = [item for item in response.context["student_table_rows"] if item["student_name"] == "学生甲"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["assignment_count"], 5)
        self.assertEqual(row["completed_count"], 2)
        self.assertEqual(row["incomplete_count"], 3)
        self.assertEqual(row["online_assignment_count"], 3)
        self.assertEqual(row["online_completed_count"], 1)
        self.assertEqual(row["requirement_assignment_count"], 2)
        self.assertEqual(row["requirement_completed_count"], 1)
        self.assertEqual(
            set(row["incomplete_assignment_ids"]),
            {online_in_progress.id, online_without_submission.id, requirement_incomplete.id},
        )
        self.assertEqual(
            row["latest_due_date_text"],
            (month_start + timedelta(days=9)).strftime("%Y-%m-%d"),
        )
        self.assertNotIn("assignment_id", row)
        self.assertTrue(
            row["detail_href"].endswith(
                f"{reverse('teacher-homework-stats-student-period-assignments')}?student_id={self.student.id}&period=month"
            )
        )

        json_rows = self.extract_json_script(response, "teacher-homework-stats-students-data")
        student_json_rows = [item for item in json_rows if item["student_name"] == "学生甲"]
        self.assertEqual(len(student_json_rows), 1)
        json_row = student_json_rows[0]
        self.assertEqual(json_row["assignment_count"], 5)
        self.assertEqual(json_row["completed_count"], 2)
        self.assertEqual(json_row["incomplete_count"], 3)
        self.assertNotIn("submission_id", json_row)

    def test_student_detail_page_renders_assigned_at_search_input_and_datagrid_config(self) -> None:
        self.sign_in(self.teacher)
        due_date = timezone.localdate().replace(day=1) + timedelta(days=4)
        assigned_at = self.make_local_datetime_for_date(due_date)
        assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="assigned_at 搜索目标",
            due_date=due_date,
            created_at=assigned_at,
            assigned_at=assigned_at,
        )
        self.add_direct_question(assignment=assignment)

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})

        self.assertContains(response, 'id="teacher-homework-stats-assigned-at-search"', html=False)
        self.assertContains(response, 'assignedAtSearchInputId: "teacher-homework-stats-assigned-at-search"', html=False)
        self.assertContains(response, "latest_assigned_at_text", html=False)
        self.assertContains(response, "teacher-homework-stats-students-table")
        self.assertContains(response, "completed_count", html=False)
        self.assertContains(response, "incomplete_count", html=False)
        self.assertContains(response, "assignment_count", html=False)

    def test_quarter_student_detail_aggregates_per_student_not_submission_rows(self) -> None:
        self.sign_in(self.teacher)
        today = timezone.localdate()
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = today.replace(month=quarter_start_month, day=1)

        online_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="quarter online",
            due_date=quarter_start + timedelta(days=4),
            created_at=self.make_local_datetime_for_date(quarter_start + timedelta(days=1)),
        )
        self.add_direct_question(assignment=online_assignment)
        self.create_submission(
            assignment=online_assignment,
            student=self.student,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(quarter_start + timedelta(days=2)),
        )
        self.create_submission(
            assignment=online_assignment,
            student=self.student,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(quarter_start + timedelta(days=3)),
        )
        requirement_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="quarter requirement",
            due_date=quarter_start + timedelta(days=8),
            created_at=self.make_local_datetime_for_date(quarter_start + timedelta(days=2)),
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "quarter"})

        rows = [row for row in response.context["student_table_rows"] if row["student_name"] == "学生甲"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["assignment_count"], 2)
        self.assertEqual(row["completed_count"], 1)
        self.assertEqual(row["incomplete_count"], 1)
        self.assertEqual(row["online_assignment_count"], 1)
        self.assertEqual(row["requirement_assignment_count"], 1)
        self.assertEqual(row["online_completed_count"], 1)
        self.assertEqual(row["requirement_completed_count"], 0)
        self.assertNotIn("assignment_id", row)

        json_rows = self.extract_json_script(response, "teacher-homework-stats-students-data")
        student_json_rows = [item for item in json_rows if item["student_name"] == "学生甲"]
        self.assertEqual(len(student_json_rows), 1)
        self.assertNotIn("submission_id", student_json_rows[0])

    def test_month_student_detail_detail_page_lists_period_assignments_and_preserves_submission_history_links(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        online_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="detail online assignment",
            due_date=month_start + timedelta(days=2),
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=1)),
        )
        self.add_direct_question(assignment=online_assignment)
        submission = self.create_submission(
            assignment=online_assignment,
            student=self.student,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(month_start + timedelta(days=3)),
        )
        requirement_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="detail requirement assignment",
            due_date=month_start + timedelta(days=4),
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=2)),
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )

        response = self.client.get(
            reverse("teacher-homework-stats-student-period-assignments"),
            {"student_id": self.student.id, "period": "month"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "detail online assignment")
        self.assertContains(response, "detail requirement assignment")
        self.assertContains(response, "查看提交记录")
        rows = self.extract_json_script(response, "teacher-homework-student-period-assignments-data")
        self.assertEqual({row["assignment_id"] for row in rows}, {online_assignment.id, requirement_assignment.id})
        online_row = next(row for row in rows if row["assignment_id"] == online_assignment.id)
        requirement_row = next(row for row in rows if row["assignment_id"] == requirement_assignment.id)
        self.assertEqual(online_row["latest_submission_id"], submission.id)
        self.assertTrue(
            online_row["detail_href"].endswith(
                f"{reverse('teacher-homework-stats-assignment-submissions')}?student_id={self.student.id}&assignment_id={online_assignment.id}&period=month"
            )
        )
        self.assertEqual(requirement_row["detail_href"], "")

    def test_teacher_homework_stats_only_include_current_teacher_students_and_detail_scope_is_enforced(self) -> None:
        self.sign_in(self.teacher)
        today = timezone.localdate()
        assignment = self.create_assignment(
            teacher=self.other_teacher,
            student=self.out_of_scope_student,
            title="越权作业",
            due_date=today,
            created_at=self.make_local_datetime_for_date(today),
        )
        self.add_direct_question(assignment=assignment)

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("学生丙", [row["student_name"] for row in response.context["students"]])

        period_detail_response = self.client.get(
            reverse("teacher-homework-stats-student-period-assignments"),
            {"student_id": self.out_of_scope_student.id, "period": "month"},
        )
        self.assertEqual(period_detail_response.status_code, 404)

        detail_response = self.client.get(
            reverse("teacher-homework-stats-assignment-submissions"),
            {"student_id": self.out_of_scope_student.id, "assignment_id": assignment.id, "period": "week"},
        )
        self.assertEqual(detail_response.status_code, 404)

    def test_assignment_submission_detail_lists_all_assignment_submissions(self) -> None:
        self.sign_in(self.teacher)
        today = timezone.localdate()
        assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="提交历史作业",
            due_date=today,
            created_at=self.make_local_datetime_for_date(today),
        )
        self.add_direct_question(assignment=assignment)
        in_progress = self.create_submission(
            assignment=assignment,
            student=self.student,
            status=HomeworkSubmission.STATUS_IN_PROGRESS,
            created_at=self.make_local_datetime_for_date(today),
        )
        reviewed = self.create_submission(
            assignment=assignment,
            student=self.student,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(today + timedelta(days=2)),
            created_at=self.make_local_datetime_for_date(today + timedelta(days=2)),
        )

        response = self.client.get(
            reverse("teacher-homework-stats-assignment-submissions"),
            {"student_id": self.student.id, "assignment_id": assignment.id, "period": "week"},
        )

        self.assertEqual(response.status_code, 200)
        rows = self.extract_json_script(response, "teacher-homework-assignment-submission-detail-data")
        self.assertEqual({row["submission_id"] for row in rows}, {in_progress.id, reviewed.id})
        self.assertEqual({row["status_text"] for row in rows}, {"作答中", "已复核"})
