from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

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
)


class StudentLearningApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.anchor_date = date(2026, 5, 4)
        self.principal = self.create_portal_user("principal_api", PortalUser.ROLE_PRINCIPAL, "校长")
        self.parent = self.create_portal_user("parent_api", PortalUser.ROLE_PARENT, "家长甲")
        self.other_parent = self.create_portal_user("parent_api_b", PortalUser.ROLE_PARENT, "家长乙")
        self.lonely_parent = self.create_portal_user("parent_api_empty", PortalUser.ROLE_PARENT, "空家长")
        self.teacher = self.create_portal_user("teacher_api", PortalUser.ROLE_TEACHER, "李老师")
        self.other_teacher = self.create_portal_user("teacher_api_b", PortalUser.ROLE_TEACHER, "王老师")

        self.course = Course.objects.create(slug="api-cpp", title="CPP", summary="API 统计课程")
        self.content = CourseContent.objects.create(
            course=self.course,
            slug="api-cpp-loop",
            title="循环结构",
            route_path="/student/cpp/api/loop",
            summary="API 统计知识点",
            is_active=True,
        )

        self.child_a = self.create_student("student_api_a", "张三", self.teacher, self.parent, "GESP 2")
        self.child_b = self.create_student("student_api_b", "李四", self.teacher, self.parent, "GESP 3")
        self.other_child = self.create_student("student_api_c", "王五", self.other_teacher, self.other_parent, "GESP 1")

    def create_portal_user(self, username: str, role: str, full_name: str) -> PortalUser:
        return PortalUser.objects.create(
            username=username,
            role=role,
            full_name=full_name,
        )

    def create_student(
        self,
        username: str,
        display_name: str,
        teacher_user: PortalUser,
        parent_user: PortalUser | None,
        primary_level_name: str,
    ) -> Student:
        portal_user = self.create_portal_user(username, PortalUser.ROLE_STUDENT, display_name)
        return Student.objects.create(
            user=portal_user,
            parent_user=parent_user,
            teacher_user=teacher_user,
            display_name=display_name,
            grade="四年级",
            campus="张江校区",
            primary_course_name=self.course.title,
            primary_track_name="基础体系",
            primary_level_name=primary_level_name,
        )

    def sign_in(self, user: PortalUser) -> None:
        self.client.cookies[AUTH_COOKIE_NAME] = signing.dumps(
            {"username": user.username, "role": user.role},
            salt=AUTH_COOKIE_SALT,
        )

    def make_local_datetime_for_date(self, target_date: date, *, hour: int = 10) -> datetime:
        tz = timezone.get_current_timezone()
        target = datetime.combine(target_date, datetime.min.time())
        return timezone.make_aware(target, tz).replace(hour=hour, minute=0, second=0, microsecond=0)

    def create_assignment(
        self,
        *,
        student: Student,
        title: str,
        due_date: date | datetime,
        teacher: PortalUser | None = None,
        created_at: datetime | None = None,
        assigned_at: datetime | None = None,
        status: str = HomeworkAssignment.STATUS_ASSIGNED,
        completed_at: datetime | None = None,
        summary: HomeworkSummary | None = None,
        highlights: str = "",
        areas_for_growth: str = "",
    ) -> HomeworkAssignment:
        assignment = HomeworkAssignment.objects.create(
            teacher=teacher or student.teacher_user,
            student=student,
            content=self.content,
            title=title,
            description="API 测试作业",
            due_date=due_date,
            status=status,
            highlights=highlights,
            areas_for_growth=areas_for_growth,
            summary=summary,
            assigned_at=assigned_at or created_at or timezone.now(),
            completed_at=completed_at,
            is_active=True,
        )
        if created_at is not None:
            HomeworkAssignment.objects.filter(id=assignment.id).update(created_at=created_at)
        assignment.refresh_from_db()
        return assignment

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

    def attach_source_import_job(
        self,
        *,
        assignment: HomeworkAssignment,
        source_filename: str,
    ) -> HomeworkImportJob:
        import_job = HomeworkImportJob.objects.create(
            teacher=assignment.teacher,
            assignment=assignment,
            source_file=SimpleUploadedFile(
                "student-learning-api-source.txt",
                b"student-learning-api",
                content_type="text/plain",
            ),
            source_filename=source_filename,
            source_sha256=f"student-learning-api-{assignment.id}",
            source_type=HomeworkImportJob.SOURCE_TYPE_TEXT,
            parse_status=HomeworkImportJob.STATUS_CONFIRMED,
            confirmed_at=timezone.now(),
            is_active=True,
        )
        assignment.source_import_job = import_job
        assignment.save(update_fields=["source_import_job", "updated_at"])
        assignment.refresh_from_db()
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
        correct_count: int = 0,
        wrong_count: int = 0,
        total_count: int | None = None,
    ) -> HomeworkSubmission:
        if status == HomeworkSubmission.STATUS_IN_PROGRESS:
            submitted_at = None
        elif submitted_at is None:
            submitted_at = timezone.now()
        if total_count is None:
            total_count = max(int(correct_count or 0), 0) + max(int(wrong_count or 0), 0)
        submission = HomeworkSubmission.objects.create(
            assignment=assignment,
            student=student,
            status=status,
            total_count=max(int(total_count or 0), 0),
            correct_count=max(int(correct_count or 0), 0),
            wrong_count=max(int(wrong_count or 0), 0),
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

    def principal_url(self) -> str:
        return reverse("api-principal-get-students-info")

    def parent_url(self) -> str:
        return reverse("api-parent-get-my-child")

    def get_student_row(self, payload: dict, student: Student) -> dict:
        return next(item for item in payload["students"] if item["student_id"] == student.id)

    def get_child_row(self, payload: dict, student: Student) -> dict:
        return next(item for item in payload["children"] if item["student_id"] == student.id)

    def serialize_assignment_due_date(self, assignment: HomeworkAssignment) -> str:
        assignment.refresh_from_db()
        return timezone.localtime(assignment.due_date).isoformat()

    def test_principal_api_requires_principal_role_and_returns_period_boundaries(self) -> None:
        response = self.client.get(self.principal_url())
        self.assertEqual(response.status_code, 401)

        for user in (self.parent, self.teacher, self.child_a.user):
            self.sign_in(user)
            forbidden_response = self.client.get(self.principal_url())
            self.assertEqual(forbidden_response.status_code, 403)

        self.sign_in(self.principal)
        response = self.client.get(self.principal_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["anchor_date"], "2026-05-04")
        self.assertEqual(payload["periods"]["week"], {"start": "2026-05-04", "end": "2026-05-10"})
        self.assertEqual(payload["periods"]["month"], {"start": "2026-05-01", "end": "2026-05-31"})
        self.assertEqual(payload["periods"]["quarter"], {"start": "2026-04-01", "end": "2026-06-30"})
        student_row = self.get_student_row(payload, self.child_a)
        self.assertEqual(student_row["teacher_id"], self.teacher.id)
        self.assertEqual(student_row["teacher_name"], self.teacher.full_name)

    def test_parent_api_requires_parent_role_and_returns_all_bound_children(self) -> None:
        response = self.client.get(self.parent_url())
        self.assertEqual(response.status_code, 401)

        for user in (self.principal, self.teacher, self.child_a.user):
            self.sign_in(user)
            forbidden_response = self.client.get(self.parent_url())
            self.assertEqual(forbidden_response.status_code, 403)

        self.sign_in(self.parent)
        response = self.client.get(self.parent_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        child_ids = {item["student_id"] for item in payload["children"]}
        self.assertEqual(child_ids, {self.child_a.id, self.child_b.id})
        self.assertNotIn(self.other_child.id, child_ids)
        child_row = self.get_child_row(payload, self.child_a)
        self.assertEqual(child_row["display_name"], self.child_a.display_name)
        self.assertEqual(child_row["primary_level_name"], self.child_a.primary_level_name)
        self.assertNotIn("teacher_id", child_row)

        self.sign_in(self.lonely_parent)
        empty_response = self.client.get(self.parent_url(), {"anchor_date": self.anchor_date.isoformat()})
        self.assertEqual(empty_response.status_code, 200)
        self.assertEqual(empty_response.json(), {"anchor_date": "2026-05-04", "children": []})

    def test_due_date_controls_period_membership_instead_of_created_at(self) -> None:
        self.create_assignment(
            student=self.child_a,
            title="本周作业",
            due_date=date(2026, 5, 6),
            created_at=self.make_local_datetime_for_date(date(2026, 4, 20)),
        )
        self.create_assignment(
            student=self.child_a,
            title="创建在本周但六月截止",
            due_date=date(2026, 6, 1),
            created_at=self.make_local_datetime_for_date(date(2026, 5, 5)),
        )
        self.create_assignment(
            student=self.child_a,
            title="创建在本周但七月截止",
            due_date=date(2026, 7, 1),
            created_at=self.make_local_datetime_for_date(date(2026, 5, 5)),
        )

        self.sign_in(self.principal)
        response = self.client.get(self.principal_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        row = self.get_student_row(response.json(), self.child_a)
        self.assertEqual(row["week"]["assignment_count"], 1)
        self.assertEqual(row["month"]["assignment_count"], 1)
        self.assertEqual(row["quarter"]["assignment_count"], 2)
        self.assertEqual(row["week"]["excluded_undated_count"], 0)
        self.assertEqual(row["month"]["excluded_undated_count"], 0)
        self.assertEqual(row["quarter"]["excluded_undated_count"], 0)

    def test_homework_assignment_model_owns_lesson_feedback_fields(self) -> None:
        summary = self.create_summary(title="本周总结")
        assignment = self.create_assignment(
            student=self.child_a,
            title="课堂反馈作业",
            due_date=date(2026, 5, 6),
            summary=summary,
            highlights="课堂专注",
            areas_for_growth="边界条件需要加强",
        )

        assignment.refresh_from_db()
        self.assertEqual(assignment.highlights, "课堂专注")
        self.assertEqual(assignment.areas_for_growth, "边界条件需要加强")
        self.assertEqual(assignment.summary_id, summary.id)
        assignment_field_names = {field.name for field in HomeworkAssignment._meta.fields}
        summary_field_names = {field.name for field in HomeworkSummary._meta.fields}
        self.assertIn("highlights", assignment_field_names)
        self.assertIn("areas_for_growth", assignment_field_names)
        self.assertNotIn("highlights", summary_field_names)
        self.assertNotIn("areas_for_growth", summary_field_names)
        self.assertEqual(HomeworkAssignment._meta.get_field("due_date").get_internal_type(), "DateTimeField")
        self.assertEqual(
            timezone.localtime(assignment.due_date).strftime("%H:%M:%S"),
            "23:59:59",
        )

    def test_due_datetime_controls_delayed_completion_on_same_day(self) -> None:
        due_at = self.make_local_datetime_for_date(date(2026, 5, 5), hour=9)
        online_assignment = self.create_assignment(
            student=self.child_a,
            title="同日延迟在线作业",
            due_date=due_at,
            status=HomeworkAssignment.STATUS_REVIEWED,
            completed_at=self.make_local_datetime_for_date(date(2026, 5, 5), hour=10),
        )
        self.add_direct_question(assignment=online_assignment)
        self.create_submission(
            assignment=online_assignment,
            student=self.child_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(date(2026, 5, 5), hour=10),
            correct_count=2,
            wrong_count=0,
        )

        requirement_assignment = self.create_assignment(
            student=self.child_a,
            title="同日延迟要求型作业",
            due_date=due_at,
            status=HomeworkAssignment.STATUS_COMPLETED,
            completed_at=self.make_local_datetime_for_date(date(2026, 5, 5), hour=10),
        )

        self.sign_in(self.principal)
        response = self.client.get(self.principal_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        row = self.get_student_row(response.json(), self.child_a)
        self.assertEqual(row["week"]["assignment_count"], 2)
        self.assertEqual(row["week"]["completed_count"], 2)
        self.assertEqual(row["week"]["on_time_completed_count"], 0)
        self.assertEqual(row["week"]["delayed_completed_count"], 2)

    def test_principal_week_returns_assignment_level_lesson_feedbacks(self) -> None:
        included_assignment = self.create_assignment(
            student=self.child_a,
            title="循环结构练习",
            due_date=date(2026, 5, 6),
            highlights="课堂专注，能主动复盘错题。",
            areas_for_growth="循环边界条件还需要加强。",
        )
        included_import_job = self.attach_source_import_job(
            assignment=included_assignment,
            source_filename="loop-feedback.txt",
        )
        empty_feedback_assignment = self.create_assignment(
            student=self.child_a,
            title="数组练习",
            due_date=date(2026, 5, 7),
        )
        empty_feedback_import_job = self.attach_source_import_job(
            assignment=empty_feedback_assignment,
            source_filename="array-feedback.txt",
        )
        feedback_only_assignment = self.create_assignment(
            student=self.child_a,
            title="额外练习",
            due_date=date(2026, 5, 8),
            highlights="口头表达积极。",
        )
        self.create_assignment(
            student=self.child_a,
            title="额外作业",
            due_date=date(2026, 5, 8),
        )
        out_of_week_assignment = self.create_assignment(
            student=self.child_a,
            title="六月总结作业",
            due_date=date(2026, 6, 2),
            highlights="不应进入本周",
        )
        self.attach_source_import_job(
            assignment=out_of_week_assignment,
            source_filename="june-feedback.txt",
        )

        self.sign_in(self.principal)
        response = self.client.get(self.principal_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        row = self.get_student_row(response.json(), self.child_a)
        week = row["week"]
        self.assertIn("lesson_feedbacks", week)
        self.assertIn("highlights", week)
        self.assertIn("areas_for_growth", week)
        self.assertNotIn("lesson_feedbacks", row["month"])
        self.assertNotIn("lesson_feedbacks", row["quarter"])

        lesson_feedbacks = week["lesson_feedbacks"]
        self.assertEqual(len(lesson_feedbacks), 3)
        self.assertEqual(
            lesson_feedbacks[0],
            {
                "assignment_id": empty_feedback_assignment.id,
                "title": "数组练习",
                "due_date": self.serialize_assignment_due_date(empty_feedback_assignment),
                "source_import_job_id": empty_feedback_import_job.id,
                "highlights": "",
                "areas_for_growth": "",
            },
        )
        self.assertEqual(
            next(item for item in lesson_feedbacks if item["assignment_id"] == included_assignment.id),
            {
                "assignment_id": included_assignment.id,
                "title": "循环结构练习",
                "due_date": self.serialize_assignment_due_date(included_assignment),
                "source_import_job_id": included_import_job.id,
                "highlights": "课堂专注，能主动复盘错题。",
                "areas_for_growth": "循环边界条件还需要加强。",
            },
        )
        self.assertEqual(
            next(item for item in lesson_feedbacks if item["assignment_id"] == feedback_only_assignment.id),
            {
                "assignment_id": feedback_only_assignment.id,
                "title": "额外练习",
                "due_date": self.serialize_assignment_due_date(feedback_only_assignment),
                "source_import_job_id": None,
                "highlights": "口头表达积极。",
                "areas_for_growth": "",
            },
        )
        self.assertTrue(all(item["title"] != "额外作业" for item in lesson_feedbacks))
        self.assertEqual(
            week["highlights"],
            ["课堂专注，能主动复盘错题。", "口头表达积极。"],
        )
        self.assertEqual(week["areas_for_growth"], ["循环边界条件还需要加强。"])

    def test_online_homework_completion_delay_and_mastery_rules(self) -> None:
        on_time_mastered = self.create_assignment(
            student=self.child_a,
            title="循环结构练习",
            due_date=date(2026, 5, 5),
            status=HomeworkAssignment.STATUS_REVIEWED,
            completed_at=self.make_local_datetime_for_date(date(2026, 5, 5)),
        )
        self.add_direct_question(assignment=on_time_mastered)
        self.create_submission(
            assignment=on_time_mastered,
            student=self.child_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(date(2026, 5, 5)),
            correct_count=5,
            wrong_count=0,
        )

        on_time_basic = self.create_assignment(
            student=self.child_a,
            title="数组练习",
            due_date=date(2026, 5, 6),
            status=HomeworkAssignment.STATUS_COMPLETED,
            completed_at=self.make_local_datetime_for_date(date(2026, 5, 6), hour=11),
        )
        self.add_direct_question(assignment=on_time_basic)
        basic_submission = self.create_submission(
            assignment=on_time_basic,
            student=self.child_a,
            status=HomeworkSubmission.STATUS_AUTO_CHECKED,
            submitted_at=self.make_local_datetime_for_date(date(2026, 5, 6)),
            correct_count=4,
            wrong_count=1,
        )
        HomeworkSubmission.objects.filter(id=basic_submission.id).update(
            submitted_at=None,
            created_at=self.make_local_datetime_for_date(date(2026, 5, 6), hour=9),
        )

        delayed_not_mastered = self.create_assignment(
            student=self.child_a,
            title="递归练习",
            due_date=date(2026, 5, 7),
            status=HomeworkAssignment.STATUS_COMPLETED,
            completed_at=self.make_local_datetime_for_date(date(2026, 5, 8)),
        )
        self.add_direct_question(assignment=delayed_not_mastered)
        self.create_submission(
            assignment=delayed_not_mastered,
            student=self.child_a,
            status=HomeworkSubmission.STATUS_SUBMITTED,
            submitted_at=self.make_local_datetime_for_date(date(2026, 5, 8)),
            correct_count=3,
            wrong_count=2,
        )

        in_progress_assignment = self.create_assignment(
            student=self.child_a,
            title="条件判断练习",
            due_date=date(2026, 5, 8),
        )
        self.add_direct_question(assignment=in_progress_assignment)
        self.create_submission(
            assignment=in_progress_assignment,
            student=self.child_a,
            status=HomeworkSubmission.STATUS_IN_PROGRESS,
            created_at=self.make_local_datetime_for_date(date(2026, 5, 8)),
        )

        no_submission_assignment = self.create_assignment(
            student=self.child_a,
            title="函数练习",
            due_date=date(2026, 5, 9),
        )
        self.add_direct_question(assignment=no_submission_assignment)

        zero_total_assignment = self.create_assignment(
            student=self.child_a,
            title="字符串练习",
            due_date=date(2026, 5, 10),
            status=HomeworkAssignment.STATUS_REVIEWED,
            completed_at=self.make_local_datetime_for_date(date(2026, 5, 10)),
        )
        self.add_direct_question(assignment=zero_total_assignment)
        self.create_submission(
            assignment=zero_total_assignment,
            student=self.child_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(date(2026, 5, 10)),
            total_count=0,
            correct_count=0,
            wrong_count=0,
        )

        self.sign_in(self.principal)
        response = self.client.get(self.principal_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        row = self.get_student_row(response.json(), self.child_a)
        self.assertEqual(row["week"]["assignment_count"], 6)
        self.assertEqual(row["week"]["completed_count"], 4)
        self.assertEqual(row["week"]["on_time_completed_count"], 3)
        self.assertEqual(row["week"]["delayed_completed_count"], 1)
        self.assertEqual(row["week"]["incomplete_count"], 2)
        self.assertEqual(row["month"]["completed_count"], 4)
        self.assertEqual(row["quarter"]["completed_count"], 4)

        knowledge_points = {
            item["name"]: item
            for item in row["knowledge_points_by_period"]["week"]
        }
        self.assertEqual(knowledge_points["循环结构练习"]["source"], "HomeworkAssignment.title")
        self.assertEqual(knowledge_points["循环结构练习"]["mastery_status"], "已掌握")
        self.assertEqual(knowledge_points["循环结构练习"]["correct_rate"], 1.0)
        self.assertEqual(knowledge_points["循环结构练习"]["correct_rate_text"], "100%")
        self.assertEqual(knowledge_points["数组练习"]["mastery_status"], "基本掌握")
        self.assertEqual(knowledge_points["数组练习"]["correct_rate"], 0.8)
        self.assertEqual(knowledge_points["数组练习"]["correct_rate_text"], "80%")
        self.assertEqual(knowledge_points["递归练习"]["mastery_status"], "未掌握")
        self.assertEqual(knowledge_points["递归练习"]["correct_rate"], 0.6)
        self.assertEqual(knowledge_points["递归练习"]["correct_rate_text"], "60%")
        self.assertEqual(knowledge_points["条件判断练习"]["mastery_status"], "未作答")
        self.assertIsNone(knowledge_points["条件判断练习"]["correct_rate"])
        self.assertEqual(knowledge_points["条件判断练习"]["correct_rate_text"], "暂无")
        self.assertEqual(knowledge_points["函数练习"]["mastery_status"], "未作答")
        self.assertIsNone(knowledge_points["函数练习"]["correct_rate"])
        self.assertEqual(knowledge_points["函数练习"]["correct_rate_text"], "暂无")
        self.assertEqual(knowledge_points["字符串练习"]["mastery_status"], "未掌握")
        self.assertIsNone(knowledge_points["字符串练习"]["correct_rate"])
        self.assertEqual(knowledge_points["字符串练习"]["correct_rate_text"], "暂无")
        self.assertEqual(knowledge_points["循环结构练习"]["assignment_id"], on_time_mastered.id)
        self.assertEqual(knowledge_points["数组练习"]["assignment_id"], on_time_basic.id)
        self.assertEqual(knowledge_points["递归练习"]["assignment_id"], delayed_not_mastered.id)
        self.assertEqual(knowledge_points["条件判断练习"]["assignment_id"], in_progress_assignment.id)
        self.assertEqual(knowledge_points["函数练习"]["assignment_id"], no_submission_assignment.id)
        self.assertEqual(knowledge_points["字符串练习"]["assignment_id"], zero_total_assignment.id)

    def test_assignment_status_drives_completion_counts_while_submission_only_affects_mastery(self) -> None:
        assigned_with_completed_submission = self.create_assignment(
            student=self.child_a,
            title="已作答但未标完成",
            due_date=date(2026, 5, 5),
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )
        self.add_direct_question(assignment=assigned_with_completed_submission)
        self.create_submission(
            assignment=assigned_with_completed_submission,
            student=self.child_a,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(date(2026, 5, 5)),
            correct_count=3,
            wrong_count=0,
        )

        reviewed_without_submission = self.create_assignment(
            student=self.child_a,
            title="已评阅但未提交客观题",
            due_date=date(2026, 5, 6),
            status=HomeworkAssignment.STATUS_REVIEWED,
        )
        self.add_direct_question(assignment=reviewed_without_submission)

        completed_without_completed_at = self.create_assignment(
            student=self.child_a,
            title="状态完成但无完成时间",
            due_date=date(2026, 5, 7),
            status=HomeworkAssignment.STATUS_COMPLETED,
        )

        self.create_assignment(
            student=self.child_a,
            title="已取消作业",
            due_date=date(2026, 5, 8),
            status=HomeworkAssignment.STATUS_CANCELLED,
        )

        self.sign_in(self.principal)
        response = self.client.get(self.principal_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        row = self.get_student_row(response.json(), self.child_a)
        self.assertEqual(row["week"]["assignment_count"], 3)
        self.assertEqual(row["week"]["completed_count"], 2)
        self.assertEqual(row["week"]["incomplete_count"], 1)
        self.assertEqual(row["week"]["on_time_completed_count"], 0)
        self.assertEqual(row["week"]["delayed_completed_count"], 0)

        knowledge_points = {
            item["name"]: item
            for item in row["knowledge_points_by_period"]["week"]
        }
        self.assertEqual(knowledge_points["已作答但未标完成"]["mastery_status"], "已掌握")
        self.assertEqual(knowledge_points["已评阅但未提交客观题"]["mastery_status"], "未作答")
        self.assertIsNone(knowledge_points["状态完成但无完成时间"]["mastery_status"])

    def test_requirement_homework_completion_and_delayed_rules(self) -> None:
        self.create_assignment(
            student=self.child_a,
            title="阅读打卡",
            due_date=date(2026, 5, 5),
            status=HomeworkAssignment.STATUS_COMPLETED,
            completed_at=self.make_local_datetime_for_date(date(2026, 5, 5)),
        )
        self.create_assignment(
            student=self.child_a,
            title="背诵作业",
            due_date=date(2026, 5, 6),
            status=HomeworkAssignment.STATUS_REVIEWED,
            completed_at=self.make_local_datetime_for_date(date(2026, 5, 7)),
        )
        self.create_assignment(
            student=self.child_a,
            title="录音作业",
            due_date=date(2026, 5, 7),
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )

        self.sign_in(self.parent)
        response = self.client.get(self.parent_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        child_row = self.get_child_row(response.json(), self.child_a)
        self.assertEqual(child_row["week"]["assignment_count"], 3)
        self.assertEqual(child_row["week"]["completed_count"], 2)
        self.assertEqual(child_row["week"]["on_time_completed_count"], 1)
        self.assertEqual(child_row["week"]["delayed_completed_count"], 1)
        self.assertEqual(child_row["week"]["incomplete_count"], 1)
        self.assertEqual(child_row["month"]["assignment_count"], 3)
        self.assertEqual(child_row["month"]["completed_count"], 2)
        self.assertEqual(child_row["quarter"]["assignment_count"], 3)
        self.assertEqual(child_row["quarter"]["completed_count"], 2)

        knowledge_points = {
            item["name"]: item
            for item in child_row["knowledge_points_by_period"]["week"]
        }
        self.assertIsNone(knowledge_points["阅读打卡"]["mastery_status"])
        self.assertIsNone(knowledge_points["阅读打卡"]["correct_rate"])
        self.assertEqual(knowledge_points["阅读打卡"]["correct_rate_text"], "暂无")
        self.assertIsNone(knowledge_points["背诵作业"]["mastery_status"])
        self.assertIsNone(knowledge_points["录音作业"]["mastery_status"])

    def test_parent_week_returns_only_current_child_lesson_feedbacks(self) -> None:
        feedback_assignment = self.create_assignment(
            student=self.child_a,
            title="张三课堂作业",
            due_date=date(2026, 5, 6),
            highlights="回答问题积极。",
            areas_for_growth="审题速度还可以更快。",
        )
        feedback_import_job = self.attach_source_import_job(
            assignment=feedback_assignment,
            source_filename="child-a-feedback.txt",
        )
        sibling_assignment = self.create_assignment(
            student=self.child_b,
            title="李四课堂作业",
            due_date=date(2026, 5, 7),
            highlights="不应泄露",
            areas_for_growth="不应泄露",
        )
        self.attach_source_import_job(
            assignment=sibling_assignment,
            source_filename="child-b-feedback.txt",
        )
        self.create_assignment(
            student=self.other_child,
            title="王五课堂作业",
            due_date=date(2026, 5, 6),
            teacher=self.other_teacher,
            highlights="更不应泄露",
            areas_for_growth="更不应泄露",
        )

        self.sign_in(self.parent)
        response = self.client.get(self.parent_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        child_row = self.get_child_row(response.json(), self.child_a)
        week = child_row["week"]
        self.assertNotIn("lesson_feedbacks", child_row["month"])
        self.assertNotIn("lesson_feedbacks", child_row["quarter"])
        self.assertEqual(
            week["lesson_feedbacks"],
            [
                {
                    "assignment_id": feedback_assignment.id,
                    "title": "张三课堂作业",
                    "due_date": self.serialize_assignment_due_date(feedback_assignment),
                    "source_import_job_id": feedback_import_job.id,
                    "highlights": "回答问题积极。",
                    "areas_for_growth": "审题速度还可以更快。",
                }
            ],
        )
        self.assertEqual(week["highlights"], ["回答问题积极。"])
        self.assertEqual(week["areas_for_growth"], ["审题速度还可以更快。"])
        sibling_row = self.get_child_row(response.json(), self.child_b)
        self.assertEqual(
            sibling_row["week"]["lesson_feedbacks"][0]["title"],
            "李四课堂作业",
        )
        self.assertEqual(sibling_row["week"]["highlights"], ["不应泄露"])
        self.assertNotIn("李四课堂作业", [item["title"] for item in week["lesson_feedbacks"]])

    def test_parent_api_only_returns_current_parents_students_with_stats(self) -> None:
        assignment = self.create_assignment(
            student=self.other_child,
            title="其他家长孩子的作业",
            due_date=date(2026, 5, 6),
            teacher=self.other_teacher,
        )
        self.add_direct_question(assignment=assignment)
        self.create_submission(
            assignment=assignment,
            student=self.other_child,
            status=HomeworkSubmission.STATUS_REVIEWED,
            submitted_at=self.make_local_datetime_for_date(date(2026, 5, 6)),
            correct_count=3,
            wrong_count=0,
        )

        self.sign_in(self.parent)
        response = self.client.get(self.parent_url(), {"anchor_date": self.anchor_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        child_ids = {item["student_id"] for item in payload["children"]}
        self.assertNotIn(self.other_child.id, child_ids)

    def test_invalid_anchor_date_returns_400_json(self) -> None:
        self.sign_in(self.principal)
        response = self.client.get(self.principal_url(), {"anchor_date": "2026-13-40"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "anchor_date 参数无效，应为 YYYY-MM-DD。")
