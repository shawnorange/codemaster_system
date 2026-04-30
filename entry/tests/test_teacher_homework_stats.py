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
    HomeworkSubmissionAnswer,
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

        self.student_a = self.create_student("stats_student_a", "学生甲", self.teacher)
        self.student_b = self.create_student("stats_student_b", "学生乙", self.teacher)
        self.student_c = self.create_student("stats_student_c", "学生丙", self.teacher)
        self.out_of_scope_student = self.create_student("stats_student_d", "学生丁", self.other_teacher)

        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.student_a,
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

    def make_local_datetime(self, day_offset: int = 0) -> datetime:
        base = timezone.localtime()
        target = base + timedelta(days=day_offset)
        return target.replace(hour=10, minute=0, second=0, microsecond=0)

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
        created_at: datetime,
    ) -> HomeworkAssignment:
        assignment = HomeworkAssignment.objects.create(
            teacher=teacher,
            student=student,
            content=self.content,
            title=title,
            description="统计测试作业",
            due_date=timezone.localdate() + timedelta(days=3),
            status=HomeworkAssignment.STATUS_ASSIGNED,
            assigned_at=created_at,
            is_active=True,
        )
        HomeworkAssignment.objects.filter(id=assignment.id).update(created_at=created_at)
        assignment.refresh_from_db()
        return assignment

    def attach_source_import_job(
        self,
        *,
        assignment: HomeworkAssignment,
        source_filename: str,
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
        return import_job

    def create_submission(
        self,
        *,
        assignment: HomeworkAssignment,
        student: Student,
        status: str,
        correct_count: int = 5,
        wrong_count: int = 0,
        submitted_at: datetime | None = None,
        created_at: datetime | None = None,
    ) -> HomeworkSubmission:
        if status == HomeworkSubmission.STATUS_IN_PROGRESS:
            submitted_at = None
        elif submitted_at is None:
            submitted_at = timezone.localtime()
        total_count = max(int(correct_count or 0), 0) + max(int(wrong_count or 0), 0)
        submission = HomeworkSubmission.objects.create(
            assignment=assignment,
            student=student,
            status=status,
            total_count=total_count,
            correct_count=correct_count,
            wrong_count=wrong_count,
            score=Decimal("100.00") if total_count and wrong_count == 0 else Decimal("0.00"),
            started_at=(submitted_at - timedelta(minutes=10)) if submitted_at else timezone.localtime() - timedelta(minutes=10),
            submitted_at=submitted_at,
            checked_at=submitted_at if submitted_at else None,
            is_active=True,
        )
        if created_at is not None:
            HomeworkSubmission.objects.filter(id=submission.id).update(created_at=created_at)
            submission.refresh_from_db()
        return submission

    def create_submission_answer(
        self,
        *,
        submission: HomeworkSubmission,
        question_no: int = 1,
        stem: str | None = None,
        options_json: dict[str, str] | None = None,
        selected_answer: str = "A",
        correct_answer_snapshot: str = "B",
        is_correct: bool = False,
    ) -> HomeworkSubmissionAnswer:
        assignment = submission.assignment
        question = HomeworkQuestion.objects.create(
            assignment=assignment,
            import_job=assignment.source_import_job,
            question_no=question_no,
            question_type=HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem=stem or f"第 {question_no} 题",
            options_json=options_json or {"A": "选项 A", "B": "选项 B"},
            correct_answer=correct_answer_snapshot or "A",
            analysis="测试解析",
            is_active=True,
        )
        return HomeworkSubmissionAnswer.objects.create(
            submission=submission,
            homework_question=question,
            selected_answer=selected_answer,
            is_correct=is_correct,
            correct_answer_snapshot=correct_answer_snapshot or "A",
            analysis_snapshot="答案解析",
        )

    def create_scored_assignment(
        self,
        *,
        teacher: PortalUser,
        student: Student,
        title: str,
        created_at: datetime,
        source_filename: str,
        correct_count: int,
        wrong_count: int,
        submitted_after: timedelta = timedelta(days=1),
    ) -> tuple[HomeworkAssignment, HomeworkSubmission]:
        assignment = self.create_assignment(
            teacher=teacher,
            student=student,
            title=title,
            created_at=created_at,
        )
        self.attach_source_import_job(
            assignment=assignment,
            source_filename=source_filename,
        )
        submission = self.create_submission(
            assignment=assignment,
            student=student,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            correct_count=correct_count,
            wrong_count=wrong_count,
            submitted_at=created_at + submitted_after,
        )
        return assignment, submission

    def get_student_row(self, response, student_name: str) -> dict[str, object]:
        return next(row for row in response.context["students"] if row["student_name"] == student_name)

    def get_student_names(self, response) -> list[str]:
        return [row["student_name"] for row in response.context["students"]]

    def extract_json_script(self, response, script_id: str):
        content = response.content.decode("utf-8")
        match = re.search(
            rf'<script id="{re.escape(script_id)}" type="application/json">(.*?)</script>',
            content,
            re.S,
        )
        self.assertIsNotNone(match, f"missing json_script {script_id}")
        return json.loads(match.group(1))

    def test_teacher_homework_stats_page_uses_teacher_scope_and_dedupes_submissions(self) -> None:
        self.sign_in(self.teacher)
        week_assignment_at = self.make_local_datetime()

        assignment_a1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 1",
            created_at=week_assignment_at,
        )
        assignment_a2 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 2",
            created_at=week_assignment_at + timedelta(minutes=5),
        )
        assignment_b1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="学生乙作业 1",
            created_at=week_assignment_at + timedelta(minutes=10),
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.out_of_scope_student,
            title="越界学生作业",
            created_at=week_assignment_at + timedelta(minutes=15),
        )

        self.create_submission(
            assignment=assignment_a1,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
        )
        self.create_submission(
            assignment=assignment_a1,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
        )
        self.create_submission(
            assignment=assignment_a2,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
        )
        self.create_submission(
            assignment=assignment_b1,
            student=self.student_b,
            status=HomeworkSubmission.STATUS_IN_PROGRESS,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "学生作业统计")
        self.assertContains(response, "完成作业数量 Top10")
        self.assertContains(response, "未完成作业 Top10")
        self.assertContains(response, "完成率 = 已交作业数 ÷ 应交作业数。同一份作业多次提交只计 1 次。")
        self.assertEqual(response.context["period_field_label"], "创建时间")

        summary = response.context["summary"]
        self.assertEqual(summary["assigned_count"], 3)
        self.assertEqual(summary["submitted_count"], 2)
        self.assertEqual(summary["missing_count"], 1)
        self.assertEqual(summary["completion_rate_text"], "66.7%")

        students = {row["student_name"]: row for row in response.context["students"]}
        self.assertEqual(set(students), {"学生甲", "学生乙", "学生丙"})
        self.assertEqual(students["学生甲"]["submitted_count"], 2)
        self.assertEqual(students["学生甲"]["missing_count"], 0)
        self.assertEqual(students["学生乙"]["assigned_count"], 1)
        self.assertEqual(students["学生乙"]["submitted_count"], 0)
        self.assertEqual(students["学生丙"]["assigned_count"], 0)

        done_top10 = response.context["done_top10"]
        self.assertEqual(len(done_top10), 1)
        self.assertEqual(done_top10[0]["student_name"], "学生甲")
        self.assertEqual(done_top10[0]["submitted_count"], 2)

        missing_top10_names = [row["student_name"] for row in response.context["missing_top10"]]
        self.assertEqual(missing_top10_names, ["学生乙"])
        self.assertNotIn("学生丙", missing_top10_names)
        self.assertNotIn("学生丁", students)

    def test_done_top10_sorts_by_submitted_count_then_completion_rate_then_correct_rate(self) -> None:
        self.sign_in(self.teacher)
        week_assignment_at = self.make_local_datetime()

        assignment_a1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 1",
            created_at=week_assignment_at,
        )
        assignment_a2 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 2",
            created_at=week_assignment_at + timedelta(minutes=5),
        )
        assignment_b1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="学生乙作业 1",
            created_at=week_assignment_at + timedelta(minutes=10),
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="学生乙作业 2",
            created_at=week_assignment_at + timedelta(minutes=12),
        )
        assignment_c1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_c,
            title="学生丙作业 1",
            created_at=week_assignment_at + timedelta(minutes=15),
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_c,
            title="学生丙作业 2",
            created_at=week_assignment_at + timedelta(minutes=20),
        )

        self.create_submission(
            assignment=assignment_a1,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
        )
        self.create_submission(
            assignment=assignment_a2,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
        )
        self.create_submission(
            assignment=assignment_b1,
            student=self.student_b,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            correct_count=4,
            wrong_count=1,
        )
        self.create_submission(
            assignment=assignment_c1,
            student=self.student_c,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            correct_count=1,
            wrong_count=4,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        done_top10_names = [row["student_name"] for row in response.context["done_top10"]]
        self.assertEqual(done_top10_names, ["学生甲", "学生乙", "学生丙"])
        self.assertEqual(response.context["done_top10"][1]["submitted_count"], 1)
        self.assertEqual(response.context["done_top10"][2]["submitted_count"], 1)
        self.assertEqual(
            response.context["done_top10"][1]["completion_rate"],
            response.context["done_top10"][2]["completion_rate"],
        )
        self.assertGreater(
            response.context["done_top10"][1]["overall_correct_rate"],
            response.context["done_top10"][2]["overall_correct_rate"],
        )

    def test_missing_top10_sorts_by_missing_count_then_completion_rate_and_excludes_zero_assigned(self) -> None:
        self.sign_in(self.teacher)
        week_assignment_at = self.make_local_datetime()

        assignment_a1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 1",
            created_at=week_assignment_at,
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 2",
            created_at=week_assignment_at + timedelta(minutes=5),
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 3",
            created_at=week_assignment_at + timedelta(minutes=10),
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="学生乙作业 1",
            created_at=week_assignment_at + timedelta(minutes=15),
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="学生乙作业 2",
            created_at=week_assignment_at + timedelta(minutes=20),
        )

        self.create_submission(
            assignment=assignment_a1,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        missing_top10_names = [row["student_name"] for row in response.context["missing_top10"]]
        self.assertEqual(missing_top10_names, ["学生甲", "学生乙"])
        self.assertEqual(response.context["missing_top10"][0]["missing_count"], 2)
        self.assertEqual(response.context["missing_top10"][1]["missing_count"], 2)
        self.assertGreater(
            response.context["missing_top10"][0]["completion_rate"],
            response.context["missing_top10"][1]["completion_rate"],
        )
        self.assertNotIn("学生丙", missing_top10_names)

    def test_teacher_homework_stats_supports_all_period_filters_and_uses_tabulator_student_grid(self) -> None:
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
        self.assertContains(week_response, "本周")
        self.assertContains(month_response, "本月")
        self.assertContains(quarter_response, "本季度")
        self.assertNotContains(week_response, "知识点周度做题量")
        self.assertNotContains(month_response, "知识点周度做题量")
        self.assertNotContains(week_response, "知识点正确率 / 错误率统计")
        self.assertNotContains(month_response, "本周知识点学生正确率 / 错误率 Top5")
        self.assertContains(week_response, "正确率 / 错误率")
        self.assertContains(month_response, "正确率 / 错误率")
        self.assertContains(week_response, "teacher-homework-stats-students-table")
        self.assertContains(week_response, "teacher-homework-stats-students-data")
        self.assertContains(week_response, "initTeacherHomeworkStatsStudents")
        self.assertContains(week_response, "teacher-tabulator-container")
        self.assertNotContains(week_response, "teacher-table--homework-stats")
        self.assertContains(week_response, "teacher-homework-stats-students-fallback")

    def test_week_student_detail_shows_knowledge_point_names(self) -> None:
        self.sign_in(self.teacher)
        week_created_at = self.make_local_datetime()

        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="二维数组训练 1",
            created_at=week_created_at,
            source_filename="  二维数组.txt  ",
            correct_count=8,
            wrong_count=2,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="递归训练",
            created_at=week_created_at + timedelta(minutes=5),
            source_filename="递归.xlsx",
            correct_count=2,
            wrong_count=3,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertEqual(response.status_code, 200)
        student_row = self.get_student_row(response, "学生甲")
        self.assertEqual(len(student_row["answer_rate_details"]), 2)
        self.assertEqual(
            student_row["answer_rate_details"][0]["knowledge_point_name"],
            "二维数组",
        )
        self.assertTrue(student_row["answer_rate_details"][0]["show_knowledge_point_name"])
        self.assertEqual(
            student_row["answer_rate_details"][0]["correct_rate_text"],
            "80%",
        )
        self.assertEqual(
            student_row["answer_rate_details"][1]["knowledge_point_name"],
            "递归",
        )
        self.assertContains(response, "二维数组")
        self.assertContains(response, "递归")
        self.assertIn("二维数组：", student_row["answer_rate_search_text"])
        self.assertIn("递归：", student_row["answer_rate_search_text"])

        json_rows = self.extract_json_script(response, "teacher-homework-stats-students-data")
        student_json_rows = [row for row in json_rows if row["student_name"] == "学生甲"]
        self.assertEqual(len(student_json_rows), 2)
        self.assertEqual(student_json_rows[0]["knowledge_point"], "递归")
        self.assertEqual(student_json_rows[1]["knowledge_point"], "二维数组")
        self.assertEqual(student_json_rows[0]["answer_rate_details"][0]["knowledge_point"], "递归")
        self.assertEqual(student_json_rows[1]["answer_rate_details"][0]["knowledge_point_name"], "二维数组")
        self.assertTrue(student_json_rows[0]["answer_rate_details"][0]["show_knowledge_point_name"])

    def test_week_student_detail_uses_assignment_source_import_job_source_filename_chain(self) -> None:
        self.sign_in(self.teacher)
        week_created_at = self.make_local_datetime()
        assignment = self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="二维数组专项",
            created_at=week_created_at,
        )
        import_job = self.attach_source_import_job(
            assignment=assignment,
            source_filename="  二维数组.txt  ",
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.source_import_job_id, import_job.id)

        self.create_submission(
            assignment=assignment,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            correct_count=12,
            wrong_count=3,
            submitted_at=week_created_at + timedelta(days=1),
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertContains(response, "二维数组")
        student_json = next(
            row
            for row in self.extract_json_script(response, "teacher-homework-stats-students-data")
            if row["student_name"] == "学生甲"
        )
        self.assertEqual(student_json["knowledge_point"], "二维数组")
        self.assertEqual(student_json["answer_rate_details"][0]["knowledge_point"], "二维数组")
        self.assertEqual(student_json["answer_rate_details"][0]["knowledge_point_name"], "二维数组")
        self.assertTrue(student_json["answer_rate_details"][0]["show_knowledge_point_name"])

    def test_week_student_detail_datagrid_includes_independent_knowledge_point_column(self) -> None:
        self.sign_in(self.teacher)
        week_created_at = self.make_local_datetime()

        assignment, submission = self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="二维数组专项",
            created_at=week_created_at,
            source_filename="二维数组.txt",
            correct_count=6,
            wrong_count=2,
        )
        second_submission = self.create_submission(
            assignment=assignment,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
            correct_count=3,
            wrong_count=1,
            submitted_at=week_created_at + timedelta(days=2),
        )
        self.create_submission_answer(
            submission=submission,
            question_no=1,
            stem="二维数组中 arr[2][3] 表示什么？",
            options_json={"A": "第2行第3列", "B": "第3行第2列"},
            selected_answer="B",
            correct_answer_snapshot="A",
            is_correct=False,
        )
        self.create_submission_answer(
            submission=submission,
            question_no=2,
            stem="二维数组一共有多少维？",
            options_json={"A": "一维", "B": "二维"},
            selected_answer="B",
            correct_answer_snapshot="B",
            is_correct=True,
        )
        self.create_submission_answer(
            submission=second_submission,
            question_no=3,
            stem="二维数组可以有几次提交？",
            options_json={"A": "一次", "B": "多次"},
            selected_answer="B",
            correct_answer_snapshot="B",
            is_correct=True,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertContains(response, "知识点")
        self.assertContains(response, "本周提交 2 条")
        column_titles = self.extract_json_script(response, "teacher-homework-stats-student-columns")
        self.assertIn("知识点", column_titles)
        self.assertIn("Homework Submission Detail", column_titles)
        student_json = next(
            row
            for row in self.extract_json_script(response, "teacher-homework-stats-students-data")
            if row["student_name"] == "学生甲"
        )
        self.assertEqual(student_json["knowledge_point"], "二维数组")
        self.assertEqual(student_json["assignment_id"], assignment.id)
        self.assertEqual(student_json["submission_record_count"], 2)
        self.assertEqual(student_json["submission_record_count_text"], "本周提交 2 条")
        self.assertTrue(
            student_json["detail_href"].endswith(
                f"{reverse('teacher-homework-stats-assignment-submissions')}?student_id={self.student_a.id}&assignment_id={assignment.id}&period=month"
            )
        )

    def test_month_student_detail_includes_assignment_submission_detail_link_and_quarter_does_not(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        created_at = self.make_local_datetime_for_date(month_start + timedelta(days=2))

        assignment, _ = self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="月度专项",
            created_at=created_at,
            source_filename="数组.txt",
            correct_count=3,
            wrong_count=1,
        )

        month_response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})
        quarter_response = self.client.get(reverse("teacher-homework-stats"), {"period": "quarter"})

        self.assertContains(month_response, "查看详情")
        self.assertContains(
            month_response,
            f"{reverse('teacher-homework-stats-assignment-submissions')}?student_id={self.student_a.id}&amp;assignment_id={assignment.id}&amp;period=month",
            html=False,
        )
        self.assertNotContains(quarter_response, "查看详情")
        self.assertContains(month_response, "本月提交 1 条")
        self.assertNotContains(quarter_response, "本周提交")
        self.assertNotContains(quarter_response, "本月提交")
        month_titles = self.extract_json_script(month_response, "teacher-homework-stats-student-columns")
        quarter_titles = self.extract_json_script(quarter_response, "teacher-homework-stats-student-columns")
        self.assertNotIn("知识点", month_titles)
        self.assertNotIn("知识点", quarter_titles)
        self.assertIn("Homework Submission Detail", month_titles)
        self.assertNotIn("Homework Submission Detail", quarter_titles)
        month_json = next(row for row in self.extract_json_script(month_response, "teacher-homework-stats-students-data") if row["student_name"] == "学生甲")
        quarter_json = next(row for row in self.extract_json_script(quarter_response, "teacher-homework-stats-students-data") if row["student_name"] == "学生甲")
        self.assertEqual(month_json["knowledge_point"], "")
        self.assertEqual(month_json["assignment_id"], assignment.id)
        self.assertEqual(month_json["detail_label"], "查看详情")
        self.assertEqual(month_json["submission_record_count_text"], "本月提交 1 条")
        self.assertTrue(
            month_json["detail_href"].endswith(
                f"{reverse('teacher-homework-stats-assignment-submissions')}?student_id={self.student_a.id}&assignment_id={assignment.id}&period=month"
            )
        )
        self.assertEqual(quarter_json["knowledge_point"], "")
        self.assertEqual(quarter_json["assignment_id"], 0)
        self.assertEqual(quarter_json["detail_href"], "")

    def test_assignment_submission_detail_page_lists_all_month_submissions_for_same_student_and_assignment(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        created_at = self.make_local_datetime_for_date(month_start + timedelta(days=2))
        assignment, submission = self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="二维数组专项",
            created_at=created_at,
            source_filename="二维数组.txt",
            correct_count=2,
            wrong_count=1,
        )
        second_submission = self.create_submission(
            assignment=assignment,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
            correct_count=1,
            wrong_count=1,
            submitted_at=created_at + timedelta(days=2),
            created_at=created_at + timedelta(days=2),
        )
        previous_month_date = month_start - timedelta(days=3)
        out_of_month_submission = self.create_submission(
            assignment=assignment,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
            correct_count=5,
            wrong_count=0,
            submitted_at=self.make_local_datetime_for_date(previous_month_date),
            created_at=self.make_local_datetime_for_date(previous_month_date),
        )

        _, out_submission = self.create_scored_assignment(
            teacher=self.other_teacher,
            student=self.out_of_scope_student,
            title="越界专项",
            created_at=created_at + timedelta(minutes=5),
            source_filename="越界知识点.xlsx",
            correct_count=1,
            wrong_count=0,
        )

        detail_response = self.client.get(
            reverse("teacher-homework-stats-assignment-submissions"),
            {"student_id": self.student_a.id, "assignment_id": assignment.id, "period": "month"},
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Homework Submission Detail")
        self.assertContains(detail_response, "本月提交记录列表")
        self.assertContains(detail_response, "二维数组")
        self.assertContains(detail_response, "assignment_id")
        self.assertContains(detail_response, "查看逐题详情")
        self.assertContains(detail_response, f"提交记录 {submission.id}")
        self.assertContains(detail_response, f"提交记录 {second_submission.id}")
        self.assertNotContains(detail_response, "越界知识点")
        detail_rows = self.extract_json_script(detail_response, "teacher-homework-assignment-submission-detail-data")
        self.assertEqual(len(detail_rows), 2)
        self.assertEqual({row["submission_id"] for row in detail_rows}, {submission.id, second_submission.id})
        self.assertEqual(detail_rows[0]["submission_id"], second_submission.id)
        self.assertEqual(detail_rows[0]["assignment_id"], assignment.id)
        self.assertEqual(detail_rows[0]["submitted_at_text"], timezone.localtime(second_submission.submitted_at).strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(detail_rows[0]["knowledge_point"], "二维数组")
        self.assertEqual(detail_rows[0]["status_text"], "已复核")
        self.assertEqual(detail_rows[0]["total_count"], 2)
        self.assertEqual(detail_rows[0]["correct_count"], 1)
        self.assertEqual(detail_rows[0]["wrong_count"], 1)
        self.assertEqual(detail_rows[0]["score_text"], "0.00")
        self.assertEqual(detail_rows[0]["correct_rate_text"], "50%")
        self.assertIn(f"submission-answer-detail?submission_id={second_submission.id}&period=month", detail_rows[0]["detail_answer_href"])
        self.assertEqual(detail_rows[1]["submission_id"], submission.id)
        self.assertEqual(detail_rows[1]["submitted_at_text"], timezone.localtime(submission.submitted_at).strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(detail_rows[1]["knowledge_point"], "二维数组")
        self.assertEqual(detail_rows[1]["correct_count"], 2)
        self.assertEqual(detail_rows[1]["wrong_count"], 1)
        self.assertEqual(detail_rows[1]["correct_rate_text"], "66.7%")
        self.assertNotIn(out_of_month_submission.id, {row["submission_id"] for row in detail_rows})
        self.assertNotIn(out_submission.id, {row["submission_id"] for row in detail_rows})

    def test_submission_answer_detail_page_shows_only_answers_for_requested_submission(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        created_at = self.make_local_datetime_for_date(month_start + timedelta(days=2))
        assignment, submission = self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="二维数组专项",
            created_at=created_at,
            source_filename="二维数组.txt",
            correct_count=2,
            wrong_count=1,
        )
        self.create_submission_answer(
            submission=submission,
            question_no=1,
            stem="二维数组中 arr[2][3] 表示什么？",
            options_json={"A": "第2行第3列", "B": "第3行第2列", "C": "第3行第3列"},
            selected_answer="A",
            correct_answer_snapshot="B",
            is_correct=False,
        )
        second_submission = self.create_submission(
            assignment=assignment,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
            correct_count=1,
            wrong_count=0,
            submitted_at=created_at + timedelta(days=1),
            created_at=created_at + timedelta(days=1),
        )
        self.create_submission_answer(
            submission=second_submission,
            question_no=2,
            stem="第二次提交的题目",
            options_json={"A": "第一次", "B": "第二次"},
            selected_answer="B",
            correct_answer_snapshot="B",
            is_correct=True,
        )

        response = self.client.get(
            reverse("teacher-homework-stats-submission-answer-detail"),
            {"submission_id": submission.id, "period": "month"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Homework Submission Answer Detail")
        self.assertContains(response, "本次做题详情")
        self.assertContains(response, "二维数组")
        self.assertContains(response, "题目详情")
        self.assertContains(response, "二维数组中 arr[2][3] 表示什么？")
        self.assertContains(response, "A. 第2行第3列")
        self.assertContains(response, "B. 第3行第2列")
        self.assertContains(response, "【学生答案】A")
        self.assertContains(response, "【正确答案】B")
        self.assertContains(response, "【结果】错误")
        self.assertNotContains(response, "第二次提交的题目")
        self.assertNotContains(response, "学生姓名")
        self.assertNotContains(response, "submission_id")

        answer_rows = self.extract_json_script(response, "teacher-homework-submission-answer-detail-data")
        self.assertEqual(len(answer_rows), 1)
        self.assertEqual(answer_rows[0]["knowledge_point"], "二维数组")
        self.assertIn("二维数组中 arr[2][3] 表示什么？", answer_rows[0]["question_detail"])
        self.assertNotIn("student_name", answer_rows[0])
        self.assertNotIn("submission_id", answer_rows[0])
        self.assertNotIn("assignment_id", answer_rows[0])

    def test_assignment_submission_detail_page_rejects_students_outside_current_teacher_scope(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        created_at = self.make_local_datetime_for_date(month_start + timedelta(days=2))
        assignment = self.create_assignment(
            teacher=self.other_teacher,
            student=self.out_of_scope_student,
            title="越界专项",
            created_at=created_at,
        )

        response = self.client.get(
            reverse("teacher-homework-stats-assignment-submissions"),
            {"student_id": self.out_of_scope_student.id, "assignment_id": assignment.id, "period": "month"},
        )

        self.assertEqual(response.status_code, 404)

    def test_submission_answer_detail_page_rejects_cross_teacher_submission(self) -> None:
        self.sign_in(self.teacher)
        week_created_at = self.make_local_datetime()
        _, out_submission = self.create_scored_assignment(
            teacher=self.other_teacher,
            student=self.out_of_scope_student,
            title="越界专项",
            created_at=week_created_at,
            source_filename="越界知识点.txt",
            correct_count=1,
            wrong_count=0,
        )

        response = self.client.get(
            reverse("teacher-homework-stats-submission-answer-detail"),
            {"submission_id": out_submission.id},
        )

        self.assertEqual(response.status_code, 404)

    def test_student_detail_sorts_by_completion_rate_then_overall_correct_rate(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        created_at = self.make_local_datetime_for_date(month_start + timedelta(days=2))

        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 1",
            created_at=created_at,
            source_filename="甲-1.txt",
            correct_count=3,
            wrong_count=3,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 2",
            created_at=created_at + timedelta(minutes=5),
            source_filename="甲-2.txt",
            correct_count=2,
            wrong_count=2,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="学生乙作业 1",
            created_at=created_at + timedelta(minutes=10),
            source_filename="乙-1.txt",
            correct_count=4,
            wrong_count=1,
        )
        assignment_c1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_c,
            title="学生丙作业 1",
            created_at=created_at + timedelta(minutes=15),
        )
        self.attach_source_import_job(
            assignment=assignment_c1,
            source_filename="丙-1.txt",
        )
        self.create_submission(
            assignment=assignment_c1,
            student=self.student_c,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            correct_count=3,
            wrong_count=0,
            submitted_at=created_at + timedelta(days=1),
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_c,
            title="学生丙作业 2",
            created_at=created_at + timedelta(minutes=20),
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})

        self.assertEqual(self.get_student_names(response), ["学生乙", "学生甲", "学生丙"])
        self.assertEqual(self.get_student_row(response, "学生乙")["completion_rate"], 100.0)
        self.assertEqual(self.get_student_row(response, "学生甲")["completion_rate"], 100.0)
        self.assertGreater(
            self.get_student_row(response, "学生乙")["overall_correct_rate"],
            self.get_student_row(response, "学生甲")["overall_correct_rate"],
        )
        self.assertEqual(self.get_student_row(response, "学生丙")["completion_rate"], 50.0)

    def test_month_student_detail_merges_multiple_knowledge_points_without_showing_names(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        first_created_at = self.make_local_datetime_for_date(month_start + timedelta(days=2))
        second_created_at = self.make_local_datetime_for_date(month_start + timedelta(days=8))

        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="数组训练 1",
            created_at=first_created_at,
            source_filename="数组.txt",
            correct_count=10,
            wrong_count=2,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="递归训练 2",
            created_at=second_created_at,
            source_filename="递归.txt",
            correct_count=2,
            wrong_count=2,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})

        self.assertEqual(response.status_code, 200)
        student_row = self.get_student_row(response, "学生甲")
        self.assertEqual(len(student_row["answer_rate_details"]), 1)
        detail = student_row["answer_rate_details"][0]
        self.assertFalse(detail["show_knowledge_point_name"])
        self.assertEqual(detail["correct_count"], 12)
        self.assertEqual(detail["wrong_count"], 4)
        self.assertEqual(detail["correct_rate_text"], "75%")
        self.assertEqual(detail["wrong_rate_text"], "25%")
        self.assertEqual(
            student_row["answer_rate_search_text"],
            "正确 12，错误 4，正确率 75%，错误率 25%",
        )
        self.assertNotContains(response, "数组：")
        self.assertNotContains(response, "递归：")

        json_rows = self.extract_json_script(response, "teacher-homework-stats-students-data")
        student_json = next(row for row in json_rows if row["student_name"] == "学生甲")
        self.assertEqual(student_json["answer_rate_details"][0]["knowledge_point"], "")
        self.assertFalse(student_json["answer_rate_details"][0]["show_knowledge_point_name"])
        self.assertEqual(student_json["answer_rate_details"][0]["knowledge_point_name"], "")

    def test_quarter_student_detail_merges_multiple_knowledge_points_without_showing_names(self) -> None:
        self.sign_in(self.teacher)
        today = timezone.localdate()
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = today.replace(month=quarter_start_month, day=1)
        first_created_at = self.make_local_datetime_for_date(quarter_start + timedelta(days=3))
        second_created_at = self.make_local_datetime_for_date(quarter_start + timedelta(days=16))

        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="数组训练",
            created_at=first_created_at,
            source_filename="数组.txt",
            correct_count=6,
            wrong_count=2,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="递归训练",
            created_at=second_created_at,
            source_filename="递归.txt",
            correct_count=3,
            wrong_count=2,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "quarter"})

        student_row = self.get_student_row(response, "学生甲")
        self.assertEqual(len(student_row["answer_rate_details"]), 1)
        detail = student_row["answer_rate_details"][0]
        self.assertFalse(detail["show_knowledge_point_name"])
        self.assertEqual(detail["correct_count"], 9)
        self.assertEqual(detail["wrong_count"], 4)
        self.assertEqual(detail["correct_rate_text"], "69.2%")
        self.assertEqual(detail["wrong_rate_text"], "30.8%")
        self.assertNotContains(response, "数组：")
        self.assertNotContains(response, "递归：")

    def test_student_detail_rate_calculation_is_correct(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        created_at = self.make_local_datetime_for_date(month_start + timedelta(days=5))

        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="递归训练",
            created_at=created_at,
            source_filename="递归.txt",
            correct_count=3,
            wrong_count=1,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})

        detail = self.get_student_row(response, "学生甲")["answer_rate_details"][0]
        self.assertEqual(detail["correct_count"], 3)
        self.assertEqual(detail["wrong_count"], 1)
        self.assertEqual(detail["correct_rate"], 75.0)
        self.assertEqual(detail["wrong_rate"], 25.0)
        self.assertEqual(detail["correct_rate_text"], "75%")
        self.assertEqual(detail["wrong_rate_text"], "25%")

    def test_page_render_contains_student_done_and_missing_names(self) -> None:
        self.sign_in(self.teacher)
        week_assignment_at = self.make_local_datetime()

        assignment_a1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 1",
            created_at=week_assignment_at,
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="学生乙作业 1",
            created_at=week_assignment_at + timedelta(minutes=5),
        )
        self.create_submission(
            assignment=assignment_a1,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            correct_count=3,
            wrong_count=1,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertContains(response, "学生甲")
        self.assertContains(response, "学生乙")
        done_rows = self.extract_json_script(response, "teacher-homework-stats-done-data")
        missing_rows = self.extract_json_script(response, "teacher-homework-stats-missing-data")
        self.assertEqual(done_rows[0]["student_name"], "学生甲")
        self.assertEqual(missing_rows[0]["student_name"], "学生乙")

    def test_student_detail_only_counts_current_teacher_students(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        in_scope_created_at = self.make_local_datetime_for_date(month_start + timedelta(days=4))
        out_of_scope_created_at = self.make_local_datetime_for_date(month_start + timedelta(days=5))

        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="排序训练",
            created_at=in_scope_created_at,
            source_filename="排序.txt",
            correct_count=2,
            wrong_count=1,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.out_of_scope_student,
            title="图论训练",
            created_at=out_of_scope_created_at,
            source_filename="图论.xlsx",
            correct_count=1,
            wrong_count=4,
        )
        response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})

        student_a_row = self.get_student_row(response, "学生甲")
        student_b_row = self.get_student_row(response, "学生乙")
        self.assertEqual(student_a_row["answer_rate_details"][0]["correct_count"], 2)
        self.assertEqual(student_a_row["answer_rate_details"][0]["wrong_count"], 1)
        self.assertFalse(student_b_row["has_answer_rate_details"])
        self.assertNotIn("学生丁", [row["student_name"] for row in response.context["students"]])

    def test_zero_total_knowledge_points_and_students_are_excluded(self) -> None:
        self.sign_in(self.teacher)
        month_start = timezone.localdate().replace(day=1)
        week_created_at = self.make_local_datetime()

        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="空白知识点",
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=2)),
            source_filename="空白.txt",
            correct_count=0,
            wrong_count=0,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="有效知识点",
            created_at=self.make_local_datetime_for_date(month_start + timedelta(days=3)),
            source_filename="有效.txt",
            correct_count=2,
            wrong_count=1,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_c,
            title="周度空白学生",
            created_at=week_created_at,
            source_filename="数组.txt",
            correct_count=0,
            wrong_count=0,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="周度有效学生",
            created_at=week_created_at + timedelta(minutes=5),
            source_filename="数组.txt",
            correct_count=1,
            wrong_count=4,
        )

        month_response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})
        week_response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        month_student_b = self.get_student_row(month_response, "学生乙")
        month_student_c = self.get_student_row(month_response, "学生丙")
        week_student_a = self.get_student_row(week_response, "学生甲")
        week_student_c = self.get_student_row(week_response, "学生丙")

        self.assertTrue(month_student_b["has_answer_rate_details"])
        self.assertEqual(month_student_b["answer_rate_details"][0]["correct_count"], 2)
        self.assertEqual(month_student_b["answer_rate_details"][0]["wrong_count"], 1)
        self.assertFalse(month_student_c["has_answer_rate_details"])
        self.assertTrue(week_student_a["has_answer_rate_details"])
        self.assertFalse(week_student_c["has_answer_rate_details"])
        self.assertEqual(month_student_c["answer_rate_search_text"], "暂无答题统计")
        self.assertEqual(week_student_c["answer_rate_search_text"], "暂无答题统计")

    def test_all_periods_keep_student_rows_and_json_scripts_non_empty_when_data_exists(self) -> None:
        self.sign_in(self.teacher)
        week_created_at = self.make_local_datetime()
        month_start = timezone.localdate().replace(day=1)
        month_created_at = self.make_local_datetime_for_date(month_start + timedelta(days=2))
        today = timezone.localdate()
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = today.replace(month=quarter_start_month, day=1)
        quarter_created_at = self.make_local_datetime_for_date(quarter_start + timedelta(days=3))

        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="周度训练",
            created_at=week_created_at,
            source_filename="周度知识点.txt",
            correct_count=2,
            wrong_count=1,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="月度训练",
            created_at=month_created_at,
            source_filename="月度知识点.xlsx",
            correct_count=3,
            wrong_count=1,
        )
        self.create_scored_assignment(
            teacher=self.teacher,
            student=self.student_c,
            title="季度训练",
            created_at=quarter_created_at,
            source_filename="季度知识点.txt",
            correct_count=4,
            wrong_count=2,
        )

        week_response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})
        month_response = self.client.get(reverse("teacher-homework-stats"), {"period": "month"})
        quarter_response = self.client.get(reverse("teacher-homework-stats"), {"period": "quarter"})

        self.assertTrue(week_response.context["students"])
        self.assertTrue(month_response.context["students"])
        self.assertTrue(quarter_response.context["students"])

        week_rows = self.extract_json_script(week_response, "teacher-homework-stats-students-data")
        month_rows = self.extract_json_script(month_response, "teacher-homework-stats-students-data")
        quarter_rows = self.extract_json_script(quarter_response, "teacher-homework-stats-students-data")

        self.assertTrue(week_rows)
        self.assertTrue(month_rows)
        self.assertTrue(quarter_rows)
        self.assertEqual(week_rows[0]["student_name"], "学生甲")
        self.assertTrue(any(detail["knowledge_point_name"] == "周度知识点" for detail in week_rows[0]["answer_rate_details"]))
        self.assertTrue(all(detail["knowledge_point_name"] == "" for row in month_rows for detail in row["answer_rate_details"]))
        self.assertTrue(all(detail["knowledge_point_name"] == "" for row in quarter_rows for detail in row["answer_rate_details"]))

    def test_student_detail_json_includes_level_codes_and_filter_options_for_current_teacher(self) -> None:
        self.sign_in(self.teacher)
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.student_b,
            course=self.course,
            level_code="C2",
            is_active=True,
        )
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.out_of_scope_student,
            course=self.course,
            level_code="C9",
            is_active=True,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        self.assertContains(response, "teacher-homework-stats-level-filter")
        self.assertContains(response, "全部")
        self.assertContains(response, "P1")
        self.assertContains(response, "C2")
        self.assertContains(response, "未分组")
        self.assertNotContains(response, "C9")

        option_labels = [item["label"] for item in response.context["student_level_filter_options"]]
        self.assertEqual(option_labels, ["全部", "C2", "P1", "未分组"])

        json_rows = self.extract_json_script(response, "teacher-homework-stats-students-data")
        student_a = next(row for row in json_rows if row["student_name"] == "学生甲")
        student_b = next(row for row in json_rows if row["student_name"] == "学生乙")
        student_c = next(row for row in json_rows if row["student_name"] == "学生丙")

        self.assertEqual(student_a["level_code"], "P1")
        self.assertEqual(student_a["level_code_display"], "P1")
        self.assertEqual(student_b["level_code"], "C2")
        self.assertEqual(student_b["level_code_display"], "C2")
        self.assertEqual(student_c["level_code"], "")
        self.assertEqual(student_c["level_code_display"], "未分组")
        self.assertNotIn("学生丁", [row["student_name"] for row in json_rows])

    def test_student_detail_picks_latest_current_teacher_level_assignment_without_duplicates(self) -> None:
        self.sign_in(self.teacher)
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.student_a,
            course=self.course,
            level_code="C2",
            is_active=True,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        json_rows = self.extract_json_script(response, "teacher-homework-stats-students-data")
        student_a_rows = [row for row in json_rows if row["student_name"] == "学生甲"]
        self.assertEqual(len(student_a_rows), 1)
        self.assertEqual(student_a_rows[0]["level_code"], "C2")
        self.assertEqual(student_a_rows[0]["level_code_display"], "C2")
        self.assertEqual(sum(1 for row in response.context["students"] if row["student_name"] == "学生甲"), 1)

    def test_level_filter_metadata_does_not_change_done_and_missing_top10_data(self) -> None:
        self.sign_in(self.teacher)
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.student_b,
            course=self.course,
            level_code="C2",
            is_active=True,
        )
        week_assignment_at = self.make_local_datetime()

        assignment_a1 = self.create_assignment(
            teacher=self.teacher,
            student=self.student_a,
            title="学生甲作业 1",
            created_at=week_assignment_at,
        )
        self.create_assignment(
            teacher=self.teacher,
            student=self.student_b,
            title="学生乙作业 1",
            created_at=week_assignment_at + timedelta(minutes=5),
        )
        self.create_submission(
            assignment=assignment_a1,
            student=self.student_a,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            correct_count=4,
            wrong_count=1,
        )

        response = self.client.get(reverse("teacher-homework-stats"), {"period": "week"})

        done_rows = self.extract_json_script(response, "teacher-homework-stats-done-data")
        missing_rows = self.extract_json_script(response, "teacher-homework-stats-missing-data")
        self.assertEqual(done_rows[0]["student_name"], "学生甲")
        self.assertEqual(missing_rows[0]["student_name"], "学生乙")
        self.assertEqual(done_rows[0]["level_code_display"], "P1")
        self.assertEqual(missing_rows[0]["level_code_display"], "C2")

    def test_teacher_workbench_shows_homework_stats_tab(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-students"), {"tab": "students"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "学生作业统计")
        self.assertContains(response, reverse("teacher-homework-stats"))
