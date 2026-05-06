from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
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
    HomeworkSummary,
    PortalUser,
    Student,
    TeacherStudentAssignment,
)


class TeacherHomeworkStatsTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.teacher_tabulator_js = Path(__file__).resolve().parents[1] / "static" / "entry" / "js" / "teacher_tabulator.js"

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
        self.parent = PortalUser.objects.create(
            username="stats_parent",
            role=PortalUser.ROLE_PARENT,
            full_name="家长甲",
        )
        self.principal = PortalUser.objects.create(
            username="stats_principal",
            role=PortalUser.ROLE_PRINCIPAL,
            full_name="校长甲",
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

    def serialize_assignment_due_date(self, assignment: HomeworkAssignment) -> str:
        assignment.refresh_from_db()
        return timezone.localtime(assignment.due_date).isoformat()

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
        summary: HomeworkSummary | None = None,
        highlights: str = "",
        areas_for_growth: str = "",
    ) -> HomeworkAssignment:
        assignment = HomeworkAssignment.objects.create(
            teacher=teacher,
            student=student,
            content=self.content,
            title=title,
            description="统计测试作业",
            due_date=due_date,
            status=status,
            summary=summary,
            highlights=highlights,
            areas_for_growth=areas_for_growth,
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

    def create_summary(
        self,
        *,
        title: str = "课堂总结",
        created_by: PortalUser | None = None,
    ) -> HomeworkSummary:
        return HomeworkSummary.objects.create(
            title=title,
            summary_html="<p>课堂总结</p>",
            created_by=created_by or self.teacher,
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

    def read_teacher_tabulator_js(self) -> str:
        return self.teacher_tabulator_js.read_text(encoding="utf-8")

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
        week_titles = self.extract_json_script(week_response, "teacher-homework-stats-student-columns")
        month_titles = self.extract_json_script(month_response, "teacher-homework-stats-student-columns")
        quarter_titles = self.extract_json_script(quarter_response, "teacher-homework-stats-student-columns")
        self.assertIn("学生姓名", week_titles)
        self.assertIn("当前级别", week_titles)
        self.assertIn("正确率", week_titles)
        self.assertIn("教师评价", week_titles)
        self.assertIn("应完成", month_titles)
        self.assertIn("已完成", month_titles)
        self.assertIn("未完成", month_titles)
        self.assertIn("按时完成", month_titles)
        self.assertIn("延迟完成", month_titles)
        self.assertNotIn("正确率", month_titles)
        self.assertNotIn("教师评价", month_titles)
        self.assertIn("应完成", quarter_titles)
        self.assertNotIn("正确率", quarter_titles)

    def test_week_page_removes_explanatory_copy_and_keeps_three_filters(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            "这里按学生聚合展示本周完成情况，统计周期统一按 due_date 归属；知识点掌握保留在主表中，教师评价按本周 summary_id 去重后编辑。",
        )
        self.assertNotContains(
            response,
            "week 主表按学生聚合展示；知识点掌握来自 knowledge_points_by_period.week，教师评价按 summary_id 去重后编辑。",
        )
        self.assertContains(response, 'id="teacher-homework-stats-student-search"', html=False)
        self.assertContains(response, "搜索学生姓名")
        self.assertContains(response, 'id="teacher-homework-stats-assigned-at-search"', html=False)
        self.assertContains(response, 'type="date"', html=False)
        self.assertContains(response, "布置日期")
        self.assertContains(response, 'id="teacher-homework-stats-level-filter"', html=False)
        self.assertContains(response, "级别分组筛选")

    def test_teacher_homework_stats_js_filters_and_week_columns_follow_new_rules(self) -> None:
        source = self.read_teacher_tabulator_js()

        self.assertIn('return ["display_name", "student_name"].some', source)
        self.assertIn("var dates = Array.isArray(rowData.assigned_at_dates) ? rowData.assigned_at_dates : [];", source)
        self.assertIn('title: "正确率"', source)
        self.assertIn('field: "knowledge_points_short_text"', source)
        self.assertIn('title: "教师评价"', source)
        self.assertRegex(source, r'title: "教师评价"[\s\S]{0,220}?responsive: 0')
        self.assertRegex(source, r'title: "操作"[\s\S]{0,220}?responsive: 0')
        self.assertNotIn('field: "knowledge_points_text"', source)

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
        self.assertEqual(student_row["display_name"], "学生甲")
        self.assertEqual(student_row["primary_level_name"], "P1")
        self.assertEqual(student_row["assigned_count"], 1)
        self.assertEqual(student_row["completed_count"], 1)
        self.assertEqual(student_row["on_time_completed_count"], 1)
        self.assertEqual(student_row["delayed_completed_count"], 0)
        table_rows = [row for row in response.context["student_table_rows"] if row["student_id"] == self.student.id]
        self.assertEqual(len(table_rows), 1)
        self.assertEqual(table_rows[0]["assignment_count"], 1)
        self.assertEqual(table_rows[0]["completed_count"], 1)
        self.assertEqual(table_rows[0]["lesson_feedback_status_text"], "本周无作业不可评价")
        self.assertEqual(table_rows[0]["knowledge_points_by_period"]["week"][0]["assignment_id"], requirement_assignment.id)

    def test_week_student_detail_keeps_student_level_knowledge_points_and_teacher_feedback_button(self) -> None:
        self.sign_in(self.teacher)
        today = timezone.localdate()
        summary = self.create_summary(title="本周课堂总结")
        online_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="循环结构练习",
            due_date=today,
            summary=summary,
            highlights="课堂专注",
            areas_for_growth="边界条件需要加强",
        )
        import_job = self.attach_source_import_job(
            assignment=online_assignment,
            source_filename="week-feedback.txt",
        )
        self.add_direct_question(assignment=online_assignment)
        self.create_submission(
            assignment=online_assignment,
            student=self.student,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(today),
            correct_count=3,
            wrong_count=0,
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="无 summary 额外作业",
            due_date=today,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "教师评价")
        row = next(item for item in response.context["student_table_rows"] if item["student_id"] == self.student.id)
        self.assertEqual(row["display_name"], "学生甲")
        self.assertEqual(row["primary_level_name"], "P1")
        self.assertIn("knowledge_points_by_period", row)
        self.assertIn("week", row["knowledge_points_by_period"])
        self.assertEqual(len(row["knowledge_points_by_period"]["week"]), 2)
        self.assertEqual(row["knowledge_points_short_text"], "100%\n—")
        self.assertTrue(row["lesson_feedback_available"])
        self.assertEqual(row["lesson_feedback_count"], 1)
        self.assertEqual(row["lesson_feedback_status_text"], "本周已评价")
        self.assertEqual(
            row["lesson_feedbacks"][0],
            {
                "assignment_id": online_assignment.id,
                "title": "循环结构练习",
                "due_date": self.serialize_assignment_due_date(online_assignment),
                "source_import_job_id": import_job.id,
                "highlights": "课堂专注",
                "areas_for_growth": "边界条件需要加强",
            },
        )

    def test_week_student_detail_marks_lesson_feedback_as_pending_when_assignment_not_evaluated(self) -> None:
        self.sign_in(self.teacher)
        today = timezone.localdate()
        pending_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="待评价作业",
            due_date=today,
        )
        import_job = self.attach_source_import_job(
            assignment=pending_assignment,
            source_filename="pending-week-feedback.txt",
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.context["student_table_rows"] if item["student_id"] == self.student.id)
        self.assertTrue(row["lesson_feedback_available"])
        self.assertEqual(row["lesson_feedback_count"], 1)
        self.assertEqual(row["lesson_feedback_status_text"], "本周未评价")
        self.assertEqual(
            row["lesson_feedbacks"],
            [
                {
                    "assignment_id": pending_assignment.id,
                    "title": "待评价作业",
                    "due_date": self.serialize_assignment_due_date(pending_assignment),
                    "source_import_job_id": import_job.id,
                    "highlights": "",
                    "areas_for_growth": "",
                }
            ],
        )

    def test_teacher_lesson_feedback_api_returns_ordered_assignment_candidates(self) -> None:
        self.sign_in(self.teacher)
        today = timezone.localdate()
        older_source_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="课堂主作业",
            due_date=today,
            highlights="主动提问",
            areas_for_growth="边界条件要更稳",
        )
        older_import_job = self.attach_source_import_job(
            assignment=older_source_assignment,
            source_filename="teacher-feedback-main.txt",
        )
        latest_source_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="课堂附加作业",
            due_date=today + timedelta(days=1),
        )
        latest_import_job = self.attach_source_import_job(
            assignment=latest_source_assignment,
            source_filename="teacher-feedback-latest.txt",
        )
        feedback_only_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="仅有教师评价的作业",
            due_date=today + timedelta(days=2),
            highlights="口头表达清晰",
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="无总结作业",
            due_date=today + timedelta(days=2),
        )
        leaked_assignment = self.create_assignment(
            teacher=self.other_teacher,
            student=self.student,
            title="其他老师作业",
            due_date=today,
            highlights="不应展示",
            areas_for_growth="不应展示",
        )
        self.attach_source_import_job(
            assignment=leaked_assignment,
            source_filename="teacher-feedback-leaked.txt",
        )

        response = self.client.get(
            reverse("teacher-homework-stats-lesson-feedback"),
            {"student_id": self.student.id, "period": "week", "anchor_date": today.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["student"]["student_id"], self.student.id)
        self.assertEqual(
            payload["week"]["lesson_feedbacks"],
            [
                {
                    "assignment_id": latest_source_assignment.id,
                    "title": "课堂附加作业",
                    "due_date": self.serialize_assignment_due_date(latest_source_assignment),
                    "source_import_job_id": latest_import_job.id,
                    "highlights": "",
                    "areas_for_growth": "",
                },
                {
                    "assignment_id": older_source_assignment.id,
                    "title": "课堂主作业",
                    "due_date": self.serialize_assignment_due_date(older_source_assignment),
                    "source_import_job_id": older_import_job.id,
                    "highlights": "主动提问",
                    "areas_for_growth": "边界条件要更稳",
                },
                {
                    "assignment_id": feedback_only_assignment.id,
                    "title": "仅有教师评价的作业",
                    "due_date": self.serialize_assignment_due_date(feedback_only_assignment),
                    "source_import_job_id": None,
                    "highlights": "口头表达清晰",
                    "areas_for_growth": "",
                },
            ],
        )
        self.assertEqual(
            payload["week"]["highlights"],
            ["主动提问", "口头表达清晰"],
        )
        self.assertEqual(payload["week"]["areas_for_growth"], ["边界条件要更稳"])

    def test_teacher_lesson_feedback_save_updates_assignment_and_enforces_permissions(self) -> None:
        today = timezone.localdate()
        assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student,
            title="待编辑作业",
            due_date=today,
        )
        self.attach_source_import_job(
            assignment=assignment,
            source_filename="teacher-feedback-save.txt",
        )
        other_student_assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="其他学生作业",
            due_date=today,
        )
        self.attach_source_import_job(
            assignment=other_student_assignment,
            source_filename="teacher-feedback-other-student.txt",
        )

        self.sign_in(self.teacher)
        response = self.client.post(
            reverse("teacher-homework-stats-lesson-feedback-save"),
            {
                "student_id": self.student.id,
                "assignment_id": assignment.id,
                "period": "week",
                "anchor_date": today.isoformat(),
                "highlights": "本周闪光点",
                "areas_for_growth": "本周待改进点",
            },
        )

        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.highlights, "本周闪光点")
        self.assertEqual(assignment.areas_for_growth, "本周待改进点")

        self.sign_in(self.other_teacher)
        forbidden_update = self.client.post(
            reverse("teacher-homework-stats-lesson-feedback-save"),
            {
                "student_id": self.student.id,
                "assignment_id": assignment.id,
                "period": "week",
                "anchor_date": today.isoformat(),
                "highlights": "不应成功",
                "areas_for_growth": "不应成功",
            },
        )
        self.assertEqual(forbidden_update.status_code, 404)
        assignment.refresh_from_db()
        self.assertEqual(assignment.highlights, "本周闪光点")
        self.assertEqual(assignment.areas_for_growth, "本周待改进点")

        self.sign_in(self.teacher)
        wrong_student_update = self.client.post(
            reverse("teacher-homework-stats-lesson-feedback-save"),
            {
                "student_id": self.student.id,
                "assignment_id": other_student_assignment.id,
                "period": "week",
                "anchor_date": today.isoformat(),
                "highlights": "不应成功",
                "areas_for_growth": "不应成功",
            },
        )
        self.assertEqual(wrong_student_update.status_code, 404)
        other_student_assignment.refresh_from_db()
        self.assertEqual(other_student_assignment.highlights, "")
        self.assertEqual(other_student_assignment.areas_for_growth, "")

        for user in (self.parent, self.principal, self.student.user):
            self.sign_in(user)
            role_response = self.client.post(
                reverse("teacher-homework-stats-lesson-feedback-save"),
                {
                    "student_id": self.student.id,
                    "assignment_id": assignment.id,
                    "period": "week",
                    "anchor_date": today.isoformat(),
                    "highlights": "越权",
                    "areas_for_growth": "越权",
                },
            )
            self.assertEqual(role_response.status_code, 403)

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
        self.assertEqual(row["display_name"], "学生甲")
        self.assertEqual(row["primary_level_name"], "P1")
        self.assertEqual(row["assignment_count"], 5)
        self.assertEqual(row["completed_count"], 2)
        self.assertEqual(row["on_time_completed_count"], 2)
        self.assertEqual(row["delayed_completed_count"], 0)
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
        self.assertNotIn("knowledge_points_by_period", row)
        self.assertNotIn("lesson_feedbacks", row)
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
        self.assertEqual(row["display_name"], "学生甲")
        self.assertEqual(row["primary_level_name"], "P1")
        self.assertEqual(row["assignment_count"], 2)
        self.assertEqual(row["completed_count"], 1)
        self.assertEqual(row["on_time_completed_count"], 1)
        self.assertEqual(row["delayed_completed_count"], 0)
        self.assertEqual(row["incomplete_count"], 1)
        self.assertEqual(row["online_assignment_count"], 1)
        self.assertEqual(row["requirement_assignment_count"], 1)
        self.assertEqual(row["online_completed_count"], 1)
        self.assertEqual(row["requirement_completed_count"], 0)
        self.assertNotIn("assignment_id", row)
        self.assertNotIn("knowledge_points_by_period", row)

        json_rows = self.extract_json_script(response, "teacher-homework-stats-students-data")
        student_json_rows = [item for item in json_rows if item["student_name"] == "学生甲"]
        self.assertEqual(len(student_json_rows), 1)
        self.assertNotIn("submission_id", student_json_rows[0])

    def test_anchor_date_2026_05_04_counts_due_date_into_week_month_and_quarter(self) -> None:
        self.sign_in(self.teacher)
        zhang = self.create_student("stats_student_zhang", "张逸帆", self.teacher)
        due_date = datetime(2026, 5, 4).date()
        assignment = self.create_assignment(
            teacher=self.teacher,
            student=zhang,
            title="5月4日作业",
            due_date=due_date,
            created_at=self.make_local_datetime_for_date(due_date - timedelta(days=2)),
            assigned_at=self.make_local_datetime_for_date(due_date - timedelta(days=2)),
        )
        self.add_direct_question(assignment=assignment)
        self.create_submission(
            assignment=assignment,
            student=zhang,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            submitted_at=self.make_local_datetime_for_date(due_date),
            correct_count=4,
            wrong_count=1,
        )

        params = {"anchor_date": "2026-05-04"}
        week_response = self.client.get(reverse("teacher-homework-stats"), {"period": "week", **params})
        month_response = self.client.get(reverse("teacher-homework-stats"), {"period": "month", **params})
        quarter_response = self.client.get(reverse("teacher-homework-stats"), {"period": "quarter", **params})

        week_row = next(item for item in week_response.context["student_table_rows"] if item["student_id"] == zhang.id)
        month_row = next(item for item in month_response.context["student_table_rows"] if item["student_id"] == zhang.id)
        quarter_row = next(item for item in quarter_response.context["student_table_rows"] if item["student_id"] == zhang.id)
        self.assertEqual(week_response.context["period_range_text"], "2026-05-04 至 2026-05-10")
        self.assertEqual(week_row["assignment_count"], 1)
        self.assertEqual(week_row["completed_count"], 1)
        self.assertEqual(month_row["assignment_count"], 1)
        self.assertEqual(quarter_row["assignment_count"], 1)
        self.assertEqual(week_row["knowledge_points_by_period"]["week"][0]["assignment_id"], assignment.id)

    def test_week_counts_due_date_even_when_created_at_and_assigned_at_are_outside_week(self) -> None:
        self.sign_in(self.teacher)
        anchor_date = datetime(2026, 5, 4).date()
        student = self.create_student("stats_student_due_scope", "跨周学生", self.teacher)
        assignment = self.create_assignment(
            teacher=self.teacher,
            student=student,
            title="due_date 在本周",
            due_date=anchor_date,
            created_at=self.make_local_datetime_for_date(anchor_date - timedelta(days=10)),
            assigned_at=self.make_local_datetime_for_date(anchor_date - timedelta(days=10)),
        )
        self.add_direct_question(assignment=assignment)

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week", "anchor_date": "2026-05-04"})

        row = next(item for item in response.context["student_table_rows"] if item["student_id"] == student.id)
        self.assertEqual(row["assignment_count"], 1)
        self.assertEqual(row["completed_count"], 0)
        self.assertEqual(row["knowledge_points_by_period"]["week"][0]["assignment_id"], assignment.id)

    def test_week_excludes_due_date_outside_week_even_if_submitted_at_is_inside_week(self) -> None:
        self.sign_in(self.teacher)
        anchor_date = datetime(2026, 5, 4).date()
        student = self.create_student("stats_student_outside_week", "周外学生", self.teacher)
        assignment = self.create_assignment(
            teacher=self.teacher,
            student=student,
            title="due_date 在下周",
            due_date=anchor_date + timedelta(days=7),
            created_at=self.make_local_datetime_for_date(anchor_date),
            assigned_at=self.make_local_datetime_for_date(anchor_date),
        )
        self.add_direct_question(assignment=assignment)
        self.create_submission(
            assignment=assignment,
            student=student,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(anchor_date),
            correct_count=3,
            wrong_count=0,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week", "anchor_date": "2026-05-04"})

        row = next(item for item in response.context["student_table_rows"] if item["student_id"] == student.id)
        self.assertEqual(row["assignment_count"], 0)
        self.assertEqual(row["completed_count"], 0)
        self.assertEqual(row["knowledge_points_by_period"]["week"], [])

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
