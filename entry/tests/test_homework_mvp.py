from __future__ import annotations

import io
import zipfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from entry.auth import AUTH_COOKIE_NAME, AUTH_COOKIE_SALT
from entry.gesp2_catalog import ASCII_CHAR_ENCODING_CONTENT_SLUG
from entry.gesp4_catalog import ARRAY_2D_CONTENT_SLUG, BINARY_SEARCH_CONTENT_SLUG, GESP4_TOPIC_DEFINITIONS
from entry.models import (
    Course,
    CourseCategory,
    CourseContent,
    CourseLevel,
    HomeworkAssignment,
    HomeworkSummary,
    PortalUser,
    Student,
    StudentContentAccess,
    TeacherStudentAssignment,
)
from entry.portal_context import student_has_content_access


class HomeworkMVPTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teacher = PortalUser.objects.create(
            username="teacher_homework",
            role=PortalUser.ROLE_TEACHER,
            full_name="作业老师",
            phone="13800000001",
        )
        self.peer_teacher = PortalUser.objects.create(
            username="peer_teacher_homework",
            role=PortalUser.ROLE_TEACHER,
            full_name="同级老师",
            phone="13800000005",
        )
        self.outsider_teacher = PortalUser.objects.create(
            username="outsider_teacher_homework",
            role=PortalUser.ROLE_TEACHER,
            full_name="无权限老师",
            phone="13800000006",
        )
        self.import_admin, _ = PortalUser.objects.update_or_create(
            username="teacher001",
            defaults={
                "role": PortalUser.ROLE_TEACHER,
                "full_name": "管理员老师",
                "phone": "13800000007",
                "is_active": True,
            },
        )
        self.parent = PortalUser.objects.create(
            username="parent_homework",
            role=PortalUser.ROLE_PARENT,
            full_name="作业家长",
            phone="13800000002",
        )
        self.student_user = PortalUser.objects.create(
            username="student_homework",
            role=PortalUser.ROLE_STUDENT,
            full_name="作业学生",
            phone="13800000003",
        )
        self.student = Student.objects.create(
            user=self.student_user,
            parent_user=self.parent,
            teacher_user=self.teacher,
            display_name="作业学生",
            grade="四年级",
            campus="虹桥校区",
            primary_course_name="C++",
            primary_track_name="GESP",
            primary_level_name="GESP4",
        )
        self.other_student_user = PortalUser.objects.create(
            username="student_other_homework",
            role=PortalUser.ROLE_STUDENT,
            full_name="别的学生",
            phone="13800000004",
        )
        self.other_student = Student.objects.create(
            user=self.other_student_user,
            teacher_user=self.teacher,
            display_name="别的学生",
            grade="四年级",
            campus="虹桥校区",
            primary_course_name="C++",
            primary_track_name="GESP",
            primary_level_name="GESP4",
        )

        self.cpp_course, _ = Course.objects.get_or_create(
            slug="cpp",
            defaults={"title": "C++", "summary": "算法与竞赛"},
        )
        self.gesp_category, _ = CourseCategory.objects.get_or_create(
            course=self.cpp_course,
            slug="gesp",
            defaults={
                "title": "GESP",
                "summary": "GESP 课程",
                "sort_order": 1,
                "is_active": True,
            },
        )
        self.gesp4_level, _ = CourseLevel.objects.get_or_create(
            category=self.gesp_category,
            code="GESP4",
            defaults={
                "title": "GESP4",
                "summary": "GESP4 级别",
                "sort_order": 4,
                "is_active": True,
            },
        )
        self.gesp2_level, _ = CourseLevel.objects.get_or_create(
            category=self.gesp_category,
            code="GESP2",
            defaults={
                "title": "GESP2",
                "summary": "GESP2 级别",
                "sort_order": 2,
                "is_active": True,
            },
        )
        self.uav_course, _ = Course.objects.get_or_create(
            slug="uav",
            defaults={"title": "无人机", "summary": "无人机课程"},
        )
        self.uav_category, _ = CourseCategory.objects.get_or_create(
            course=self.uav_course,
            slug="flight",
            defaults={
                "title": "飞行控制",
                "summary": "无人机飞行课程",
                "sort_order": 1,
                "is_active": True,
            },
        )
        self.uav_level, _ = CourseLevel.objects.get_or_create(
            category=self.uav_category,
            code="S1",
            defaults={
                "title": "S1",
                "summary": "无人机 S1 级别",
                "sort_order": 1,
                "is_active": True,
            },
        )
        self.contents = {}
        for sort_order, topic in enumerate(GESP4_TOPIC_DEFINITIONS, start=1):
            content, _ = CourseContent.objects.update_or_create(
                slug=topic["slug"],
                defaults={
                    "course": self.cpp_course,
                    "level": self.gesp4_level,
                    "content_type": "topic",
                    "title": topic["title"],
                    "phase": "GESP4",
                    "sort_order": sort_order,
                    "route_path": topic["route_path"],
                    "summary": topic["summary"],
                    "has_real_content": topic["content_mode"] == "real",
                    "is_active": True,
                },
            )
            self.contents[content.slug] = content
        self.lower_level_content, _ = CourseContent.objects.update_or_create(
            slug="test-gesp2-homework-scope",
            defaults={
                "course": self.cpp_course,
                "level": self.gesp2_level,
                "content_type": "topic",
                "title": "越权 GESP2 知识点",
                "phase": "GESP2",
                "sort_order": 999,
                "route_path": "/student/cpp/gesp/gesp2/test-gesp2-homework-scope",
                "summary": "仅用于越权测试",
                "has_real_content": False,
                "is_active": True,
            },
        )
        self.gesp2_ascii_content, _ = CourseContent.objects.update_or_create(
            slug=ASCII_CHAR_ENCODING_CONTENT_SLUG,
            defaults={
                "course": self.cpp_course,
                "level": self.gesp2_level,
                "content_type": "topic",
                "title": "ASCII 编码",
                "phase": "GESP2",
                "permission_code": "C1",
                "sort_order": 1001,
                "route_path": "/student/cpp/gesp/gesp2/ascii-char-encoding",
                "summary": "GESP2 ASCII 编码知识点页。",
                "has_real_content": True,
                "is_active": True,
            },
        )
        self.out_of_scope_content, _ = CourseContent.objects.update_or_create(
            slug="test-uav-homework-scope",
            defaults={
                "course": self.uav_course,
                "level": self.uav_level,
                "content_type": "topic",
                "title": "无人机越权知识点",
                "phase": "S1",
                "sort_order": 1000,
                "route_path": "/student/uav/flight/s1/test-uav-homework-scope",
                "summary": "仅用于跨课程越权测试",
                "has_real_content": False,
                "is_active": True,
            },
        )

        self.array_content = self.contents[ARRAY_2D_CONTENT_SLUG]
        self.binary_search_content = self.contents[BINARY_SEARCH_CONTENT_SLUG]

        TeacherStudentAssignment.objects.get_or_create(
            teacher=self.teacher,
            student=self.student,
            course=self.cpp_course,
            level_code="C4",
            defaults={"is_active": True},
        )
        TeacherStudentAssignment.objects.get_or_create(
            teacher=self.peer_teacher,
            student=self.student,
            course=self.cpp_course,
            level_code="C4",
            defaults={"is_active": True},
        )
        TeacherStudentAssignment.objects.get_or_create(
            teacher=self.teacher,
            student=self.other_student,
            course=self.cpp_course,
            level_code="C4",
            defaults={"is_active": True},
        )
        TeacherStudentAssignment.objects.get_or_create(
            teacher=self.import_admin,
            student=self.student,
            course=self.cpp_course,
            level_code="C4",
            defaults={"is_active": True},
        )

    def sign_in(self, user: PortalUser) -> None:
        self.client.cookies[AUTH_COOKIE_NAME] = signing.dumps(
            {"username": user.username, "role": user.role},
            salt=AUTH_COOKIE_SALT,
        )

    def build_student_import_xlsx(self, rows: list[list[str]]) -> bytes:
        workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
        workbook_rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""

        row_xml_parts = []
        for row_index, row in enumerate(rows, start=1):
            cell_xml_parts = []
            for col_index, value in enumerate(row, start=1):
                column_ref = ""
                current = col_index
                while current:
                    current, remainder = divmod(current - 1, 26)
                    column_ref = chr(65 + remainder) + column_ref
                cell_ref = f"{column_ref}{row_index}"
                escaped_value = (
                    str(value)
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                cell_xml_parts.append(
                    f'<c r="{cell_ref}" t="inlineStr"><is><t>{escaped_value}</t></is></c>'
                )
            row_xml_parts.append(f'<row r="{row_index}">{"".join(cell_xml_parts)}</row>')
        sheet_xml = (
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>"""
            + "".join(row_xml_parts)
            + """</sheetData>
</worksheet>
"""
        )

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/workbook.xml", workbook_xml)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
            archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        return buffer.getvalue()

    def create_homework(
        self,
        *,
        student: Student | None = None,
        content: CourseContent | None = None,
        title: str = "二维数组作业",
        description: str = "完成本节知识点与练习。",
        due_date=None,
        status: str = HomeworkAssignment.STATUS_ASSIGNED,
        teacher_comment: str = "",
        assigned_at=None,
        highlights: str = "",
        areas_for_growth: str = "",
    ) -> HomeworkAssignment:
        assignment = HomeworkAssignment.objects.create(
            teacher=self.teacher,
            student=student or self.student,
            content=content or self.array_content,
            title=title,
            description=description,
            due_date=due_date or timezone.localdate(),
            status=status,
            teacher_comment=teacher_comment,
            assigned_at=assigned_at or timezone.now(),
            highlights=highlights,
            areas_for_growth=areas_for_growth,
        )
        return assignment

    def create_homework_summary(
        self,
        *assignments: HomeworkAssignment,
        title: str = "本周总结",
        summary_html: str = "<p>本周课堂与作业节奏正常。</p>",
    ) -> HomeworkSummary:
        summary = HomeworkSummary.objects.create(
            title=title,
            summary_html=summary_html,
            created_by=self.teacher,
        )
        if assignments:
            HomeworkAssignment.objects.filter(id__in=[assignment.id for assignment in assignments]).update(
                summary=summary,
                updated_at=timezone.now(),
            )
        return summary

    def save_content_restrictions(self, student: Student, *, restricted_contents: list[CourseContent]) -> None:
        response = self.client.post(
            reverse("teacher-student-detail", args=[student.id]),
            {
                "form_action": "save_content_restrictions",
                "restricted_content_ids": [str(content.id) for content in restricted_contents],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("content_restriction_saved=1", response["Location"])

    def create_student_account(
        self,
        *,
        username: str,
        full_name: str,
        display_name: str,
        primary_course_name: str = "C++",
        primary_level_name: str = "GESP4",
        assignment_level_code: str = "C4",
    ) -> tuple[PortalUser, Student]:
        portal_user = PortalUser.objects.create(
            username=username,
            role=PortalUser.ROLE_STUDENT,
            full_name=full_name,
            phone="13900000000",
        )
        student = Student.objects.create(
            user=portal_user,
            teacher_user=self.teacher,
            display_name=display_name,
            grade="四年级",
            campus="虹桥校区",
            primary_course_name=primary_course_name,
            primary_track_name="GESP",
            primary_level_name=primary_level_name,
        )
        TeacherStudentAssignment.objects.get_or_create(
            teacher=self.teacher,
            student=student,
            course=self.cpp_course,
            level_code=assignment_level_code,
            defaults={"is_active": True},
        )
        return portal_user, student

    def test_teacher_can_create_homework_and_keep_content_access_effective(self) -> None:
        self.sign_in(self.teacher)
        due_date = timezone.localdate() + timedelta(days=3)

        response = self.client.post(
            reverse("teacher-student-detail", args=[self.student.id]),
            {
                "form_action": "create_homework",
                "content_id": str(self.array_content.id),
                "title": "二维数组周练",
                "description": "先完成二维数组专题中的主例题，再整理错因。",
                "due_date": due_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("homework_op=created", response["Location"])

        assignment = HomeworkAssignment.objects.get(student=self.student, title="二维数组周练")
        self.assertEqual(assignment.content, self.array_content)
        self.assertEqual(assignment.status, HomeworkAssignment.STATUS_ASSIGNED)
        self.assertTrue(student_has_content_access(self.student, self.array_content.slug))
        self.assertFalse(StudentContentAccess.objects.filter(student=self.student, content=self.array_content).exists())

    def test_teacher_student_detail_renders_homework_modal_for_current_student(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-student-detail", args=[self.student.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="homework-panel"', html=False)
        self.assertContains(response, 'id="open-homework-create-modal"', html=False)
        self.assertContains(response, 'id="homework-create-modal"', html=False)
        self.assertContains(response, self.student.display_name)
        self.assertContains(response, 'name="content_id"', html=False)
        self.assertContains(response, 'name="due_date"', html=False)
        self.assertContains(response, 'name="title"', html=False)
        self.assertContains(response, 'name="description"', html=False)
        self.assertNotContains(response, 'name="student_id"', html=False)
        self.assertContains(response, self.array_content.title)
        self.assertContains(response, self.lower_level_content.title)
        self.assertNotContains(response, self.out_of_scope_content.title)
        self.assertContains(response, "还没有布置作业")
        self.assertContains(response, "可以先点上方“布置作业”")

    def test_c4_assignment_can_see_lower_level_homework_contents(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-student-detail", args=[self.student.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.lower_level_content.title)
        self.assertContains(response, "C++ / GESP2 / 越权 GESP2 知识点")

    def test_teacher_student_detail_renders_review_modal_with_minimal_fields(self) -> None:
        self.create_homework(title="待写评语作业")
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-student-detail", args=[self.student.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="homework-review-modal"', html=False)
        self.assertContains(response, 'data-open-homework-review-modal', html=False)
        self.assertContains(response, 'name="teacher_comment"', html=False)
        self.assertContains(response, 'name="homework_id"', html=False)
        self.assertContains(response, "当前学生")
        self.assertContains(response, "作业标题")
        self.assertContains(response, "对应知识点")
        self.assertContains(response, "当前状态")

    def test_teacher_student_detail_guides_teacher_to_batch_homework_summary_flow(self) -> None:
        self.create_homework(title="本周总结作业")
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-student-detail", args=[self.student.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "课后总结统一改到批量布置作业页面处理")
        self.assertContains(response, reverse("teacher-homework-batch-create"))
        self.assertContains(response, "单学生上传入口已隐藏")
        self.assertNotContains(response, 'id="open-homework-summary-modal"', html=False)
        self.assertNotContains(response, 'id="homework-summary-modal"', html=False)
        self.assertNotContains(response, 'name="summary_html_file"', html=False)
        self.assertNotContains(response, 'name="summary_html"', html=False)

    def test_teacher_student_detail_shows_c1_scope_contents_under_new_permission_model(self) -> None:
        c1_student_user = PortalUser.objects.create(
            username="student_c1_homework",
            role=PortalUser.ROLE_STUDENT,
            full_name="C1 学生",
            phone="13800000007",
        )
        c1_student = Student.objects.create(
            user=c1_student_user,
            teacher_user=self.teacher,
            display_name="C1 学生",
            grade="三年级",
            campus="虹桥校区",
            primary_course_name="C++",
            primary_track_name="GESP",
            primary_level_name="GESP1",
        )
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=c1_student,
            course=self.cpp_course,
            level_code="C1",
            is_active=True,
        )
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-student-detail", args=[c1_student.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "二维数组专题")
        self.assertContains(response, "二分查找专题")
        self.assertNotContains(response, "暂无可布置内容")
        self.assertNotContains(response, 'disabled title="', html=False)

    def test_teacher_datagrid_pages_render_shared_grid_structure(self) -> None:
        self.sign_in(self.teacher)

        urls = [
            reverse("teacher-student-assignments", args=[self.student.id]),
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            reverse("teacher-course-category-detail", args=[self.cpp_course.slug, self.gesp_category.slug]),
            reverse("teacher-course-level-detail", args=[self.cpp_course.slug, self.gesp_category.slug, self.gesp4_level.code]),
            reverse(
                "teacher-course-knowledge-point-permissions",
                args=[self.cpp_course.slug, self.gesp_category.slug, self.gesp4_level.code, self.array_content.slug],
            ),
            reverse("teacher-course-student-pool", args=[self.cpp_course.slug]),
            reverse("teacher-assignment-new"),
            reverse("teacher-course-knowledge-point-new", args=[self.cpp_course.slug, self.gesp_category.slug, self.gesp4_level.code]),
        ]

        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, 'teacher-tabulator-shell', html=False)
            self.assertContains(response, 'teacher-tabulator-container', html=False)

        permissions_response = self.client.get(
            reverse(
                "teacher-course-knowledge-point-permissions",
                args=[self.cpp_course.slug, self.gesp_category.slug, self.gesp4_level.code, self.array_content.slug],
            )
        )
        self.assertContains(permissions_response, 'teacher-tabulator-toolbar--secondary', html=False)
        self.assertContains(permissions_response, 'cm-tabulator-btn', html=False)

        student_pool_response = self.client.get(reverse("teacher-course-student-pool", args=[self.cpp_course.slug]))
        self.assertContains(student_pool_response, 'teacher-tabulator-filter-field', html=False)
        self.assertContains(student_pool_response, 'teacher-tabulator-filter-control', html=False)
        self.assertContains(student_pool_response, "添加单个学生")

    def test_teacher_workbench_pages_render_shared_portal_structure(self) -> None:
        self.sign_in(self.teacher)

        teacher_home_response = self.client.get(reverse("teacher-students"))
        self.assertEqual(teacher_home_response.status_code, 200)
        self.assertContains(teacher_home_response, 'teacher-workspace-shell', html=False)
        self.assertContains(teacher_home_response, 'teacher-tab-bar', html=False)
        self.assertContains(teacher_home_response, 'portal-workbench-panel portal-workbench-panel--recent portal-tab-panel teacher-tab-panel workbench-panel', html=False)
        self.assertContains(teacher_home_response, 'portal-action-row portal-action-row--leading', html=False)

        student_detail_response = self.client.get(reverse("teacher-student-detail", args=[self.student.id]))
        self.assertEqual(student_detail_response.status_code, 200)
        self.assertContains(student_detail_response, 'teacher-overview-card', html=False)
        self.assertContains(student_detail_response, 'teacher-overview-card__actions portal-action-row', html=False)
        self.assertContains(student_detail_response, 'teacher-workbench-grid', html=False)
        self.assertContains(student_detail_response, 'portal-block-offset-md', html=False)

        course_detail_response = self.client.get(reverse("teacher-course-detail", args=[self.cpp_course.slug]))
        self.assertEqual(course_detail_response.status_code, 200)
        self.assertContains(course_detail_response, 'workbench-panel workbench-panel--topics', html=False)
        self.assertContains(course_detail_response, 'topic-work-grid', html=False)
        self.assertContains(course_detail_response, 'portal-action-row portal-action-row--leading', html=False)

    def test_teacher_cannot_create_homework_for_out_of_scope_student(self) -> None:
        self.sign_in(self.outsider_teacher)

        response = self.client.post(
            reverse("teacher-student-detail", args=[self.student.id]),
            {
                "form_action": "create_homework",
                "content_id": str(self.array_content.id),
                "title": "无权限学生作业",
                "description": "不应该成功",
                "due_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(HomeworkAssignment.objects.filter(title="无权限学生作业").exists())

    def test_teacher_cannot_create_homework_for_out_of_scope_content(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-student-detail", args=[self.student.id]),
            {
                "form_action": "create_homework",
                "content_id": str(self.out_of_scope_content.id),
                "title": "越权内容作业",
                "description": "不应该成功",
                "due_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请选择当前教师负责范围内的知识点作为作业目标。")
        self.assertFalse(HomeworkAssignment.objects.filter(title="越权内容作业").exists())

    def test_teacher_can_create_homework_for_lower_level_content_when_assignment_is_c4(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-student-detail", args=[self.student.id]),
            {
                "form_action": "create_homework",
                "content_id": str(self.lower_level_content.id),
                "title": "GESP2 向下兼容作业",
                "description": "C4 老师范围应可覆盖低级别内容。",
                "due_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("homework_op=created", response["Location"])
        assignment = HomeworkAssignment.objects.get(student=self.student, title="GESP2 向下兼容作业")
        self.assertEqual(assignment.content, self.lower_level_content)

    def test_teacher_can_add_single_student_from_course_student_pool(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-course-student-pool", args=[self.cpp_course.slug]),
            {
                "form_action": "create_single_student",
                "new_student_name": "单个新增学生",
                "new_parent_phone": "13800000999",
                "new_course_id": str(self.cpp_course.id),
                "new_permission_level_code": "C2",
                "new_primary_level_name": "GESP6",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "单个学生添加成功，已加入当前课程。")

        student = Student.objects.select_related("user", "parent_user", "teacher_user").get(display_name="单个新增学生")
        self.assertEqual(student.parent_user.phone, "13800000999")
        self.assertEqual(student.parent_user.username, "parent_13800000999")
        self.assertEqual(student.teacher_user, self.teacher)
        self.assertEqual(student.primary_course_name, self.cpp_course.title)
        self.assertEqual(student.primary_track_name, "GESP")
        self.assertEqual(student.primary_level_name, "GESP6")
        self.assertTrue(student.user.check_password("123456"))
        self.assertTrue(student.parent_user.check_password("123456"))
        self.assertNotEqual(student.user.password, "123456")
        self.assertNotEqual(student.parent_user.password, "123456")
        self.assertNotIn(student.id, response.context["pool_student_ids"])

        assignment = TeacherStudentAssignment.objects.get(
            teacher=self.teacher,
            student=student,
            course=self.cpp_course,
            level_code="C2",
        )
        self.assertTrue(assignment.is_active)
        self.assertEqual(assignment.student_id, student.id)
        self.assertEqual(assignment.course, self.cpp_course)

    def test_teacher_cannot_add_duplicate_single_student_by_name_and_parent_phone(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-course-student-pool", args=[self.cpp_course.slug]),
            {
                "form_action": "create_single_student",
                "new_student_name": self.student.display_name,
                "new_parent_phone": self.parent.phone,
                "new_course_id": str(self.cpp_course.id),
                "new_permission_level_code": "C1",
                "new_primary_level_name": "GESP1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "该学生已经在数据库中，添加失败")
        self.assertEqual(
            Student.objects.filter(
                display_name=self.student.display_name,
                parent_user__phone=self.parent.phone,
            ).count(),
            1,
        )
        self.assertTrue(response.context["single_student_modal_should_open"])

    def test_teacher_add_single_student_requires_all_fields(self) -> None:
        self.sign_in(self.teacher)
        before_student_count = Student.objects.count()

        response = self.client.post(
            reverse("teacher-course-student-pool", args=[self.cpp_course.slug]),
            {
                "form_action": "create_single_student",
                "new_student_name": "",
                "new_parent_phone": "",
                "new_course_id": "",
                "new_permission_level_code": "",
                "new_primary_level_name": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请完整填写学生姓名、家长电话、当前课程、权限等级和等级名称。")
        self.assertEqual(Student.objects.count(), before_student_count)
        self.assertTrue(response.context["single_student_modal_should_open"])

    def test_teacher_add_single_student_reuses_existing_parent_account(self) -> None:
        existing_parent = PortalUser(
            username="legacy_parent_user",
            role=PortalUser.ROLE_PARENT,
            full_name="已有家长",
            phone="13800000888",
            is_active=True,
        )
        existing_parent.set_password("legacy-pass")
        existing_parent.save()
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-course-student-pool", args=[self.cpp_course.slug]),
            {
                "form_action": "create_single_student",
                "new_student_name": "复用家长学生",
                "new_parent_phone": "13800000888",
                "new_course_id": str(self.cpp_course.id),
                "new_permission_level_code": "C4",
                "new_primary_level_name": "CSP-S",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        student = Student.objects.select_related("parent_user").get(display_name="复用家长学生")
        self.assertEqual(student.parent_user_id, existing_parent.id)
        self.assertEqual(PortalUser.objects.filter(role=PortalUser.ROLE_PARENT, phone="13800000888").count(), 1)
        existing_parent.refresh_from_db()
        self.assertTrue(existing_parent.check_password("legacy-pass"))

    def test_teacher001_can_see_student_import_button_on_course_students_page(self) -> None:
        self.sign_in(self.import_admin)

        response = self.client.get(reverse("teacher-course-students-detail", args=[self.cpp_course.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "导入学生")

    def test_teacher001_can_see_student_import_button_on_teacher_workbench(self) -> None:
        self.sign_in(self.import_admin)

        response = self.client.get(reverse("teacher-students"), {"tab": "students"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "C++ · 添加新学生")
        self.assertContains(response, "导入学生")
        self.assertContains(
            response,
            f'{reverse("teacher-course-students-detail", args=[self.cpp_course.slug])}?open_import=1',
            html=False,
        )

    def test_teacher001_workbench_still_shows_bootstrap_links_without_assignments(self) -> None:
        TeacherStudentAssignment.objects.filter(teacher=self.import_admin).delete()
        self.sign_in(self.import_admin)

        response = self.client.get(reverse("teacher-students"), {"tab": "students"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "C++ · 添加新学生")
        self.assertContains(response, "导入学生")

    def test_non_teacher001_cannot_see_student_import_button_on_course_students_page(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-course-students-detail", args=[self.cpp_course.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "导入学生")

    def test_non_teacher001_cannot_see_student_import_button_on_teacher_workbench(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-students"), {"tab": "students"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "C++ · 添加新学生")
        self.assertNotContains(response, "导入学生")

    def test_non_teacher001_cannot_post_student_csv_import(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            {
                "form_action": "import_students_csv",
                "student_csv_file": SimpleUploadedFile(
                    "students.csv",
                    "学生姓名,家长手机号,当前学习内容,当前级别\n张三,13800001001,二维数组,GESP4\n".encode("utf-8"),
                    content_type="text/csv",
                ),
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Student.objects.filter(display_name="张三").exists())

    def test_teacher001_can_import_student_csv(self) -> None:
        self.sign_in(self.import_admin)

        response = self.client.post(
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            {
                "form_action": "import_students_csv",
                "student_csv_file": SimpleUploadedFile(
                    "students.csv",
                    "学生姓名,家长手机号,当前学习内容,当前级别\n张三,13800001002,二维数组,GESP4\n".encode("utf-8-sig"),
                    content_type="text/csv",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["student_import_result"]["success_count"], 1)
        self.assertEqual(response.context["student_import_result"]["failure_count"], 0)

        student = Student.objects.select_related("user", "parent_user", "teacher_user").get(display_name="张三")
        self.assertEqual(student.parent_user.username, "parent_13800001002")
        self.assertEqual(student.parent_user.phone, "13800001002")
        self.assertRegex(student.user.username, r"^student_13800001002_\d{4}$")
        self.assertEqual(student.user.phone, "")
        self.assertEqual(student.teacher_user, self.import_admin)
        self.assertEqual(student.primary_course_name, self.cpp_course.title)
        self.assertEqual(student.primary_track_name, "二维数组")
        self.assertEqual(student.primary_level_name, "GESP4")
        self.assertTrue(student.user.check_password("123456"))
        self.assertTrue(student.parent_user.check_password("123456"))
        self.assertNotEqual(student.user.password, "123456")
        self.assertNotEqual(student.parent_user.password, "123456")

        assignments = TeacherStudentAssignment.objects.filter(
            teacher=self.import_admin,
            student=student,
            course=self.cpp_course,
        )
        self.assertEqual(assignments.count(), 1)
        self.assertEqual(assignments.get().level_code, "C1")
        self.assertTrue(assignments.get().is_active)

    def test_teacher001_can_import_student_csv_with_c_permission_level(self) -> None:
        self.sign_in(self.import_admin)

        response = self.client.post(
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            {
                "form_action": "import_students_csv",
                "student_import_file": SimpleUploadedFile(
                    "students.csv",
                    "学生姓名,家长手机号,当前学习内容,当前级别\n权限级学生,13800001022,二维数组,C1\n".encode("utf-8"),
                    content_type="text/csv",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["student_import_result"]["success_count"], 1)
        student = Student.objects.select_related("parent_user").get(display_name="权限级学生")
        self.assertEqual(student.parent_user.phone, "13800001022")
        self.assertEqual(student.primary_track_name, "二维数组")
        self.assertEqual(student.primary_level_name, "C1")
        assignment = TeacherStudentAssignment.objects.get(
            teacher=self.import_admin,
            student=student,
            course=self.cpp_course,
        )
        self.assertEqual(assignment.level_code, "C1")

    def test_teacher001_can_import_student_xlsx(self) -> None:
        self.sign_in(self.import_admin)

        response = self.client.post(
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            {
                "form_action": "import_students_csv",
                "student_import_file": SimpleUploadedFile(
                    "students.xlsx",
                    self.build_student_import_xlsx(
                        [
                            ["学生姓名", "家长手机号", "当前学习内容", "当前级别"],
                            ["王五", "13800001012", "二分查找", "GESP5"],
                        ]
                    ),
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["student_import_result"]["success_count"], 1)
        self.assertEqual(response.context["student_import_result"]["failure_count"], 0)

        student = Student.objects.select_related("user", "parent_user", "teacher_user").get(display_name="王五")
        self.assertEqual(student.parent_user.username, "parent_13800001012")
        self.assertRegex(student.user.username, r"^student_13800001012_\d{4}$")
        self.assertEqual(student.primary_track_name, "二分查找")
        self.assertEqual(student.primary_level_name, "GESP5")
        assignment = TeacherStudentAssignment.objects.get(
            teacher=self.import_admin,
            student=student,
            course=self.cpp_course,
        )
        self.assertEqual(assignment.level_code, "C2")

    def test_teacher001_can_import_student_xlsx_with_c_permission_level(self) -> None:
        self.sign_in(self.import_admin)

        response = self.client.post(
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            {
                "form_action": "import_students_csv",
                "student_import_file": SimpleUploadedFile(
                    "students.xlsx",
                    self.build_student_import_xlsx(
                        [
                            ["学生姓名", "家长手机号", "当前学习内容", "当前级别"],
                            ["权限级王五", "13800001023", "二分查找", "C2"],
                        ]
                    ),
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["student_import_result"]["success_count"], 1)
        student = Student.objects.get(display_name="权限级王五")
        self.assertEqual(student.primary_track_name, "二分查找")
        self.assertEqual(student.primary_level_name, "C2")
        assignment = TeacherStudentAssignment.objects.get(
            teacher=self.import_admin,
            student=student,
            course=self.cpp_course,
        )
        self.assertEqual(assignment.level_code, "C2")

    def test_open_import_query_opens_teacher_course_students_modal(self) -> None:
        self.sign_in(self.import_admin)

        response = self.client.get(
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            {"open_import": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["student_import_modal_should_open"])

    def test_teacher001_can_import_student_csv_without_existing_assignments(self) -> None:
        TeacherStudentAssignment.objects.filter(teacher=self.import_admin).delete()
        self.sign_in(self.import_admin)

        response = self.client.post(
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            {
                "form_action": "import_students_csv",
                "student_csv_file": SimpleUploadedFile(
                    "students.csv",
                    "学生姓名,家长手机号,当前学习内容,当前级别\n无关系导入,13800001006,二维数组,GESP4\n".encode("utf-8"),
                    content_type="text/csv",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["student_import_result"]["success_count"], 1)
        student = Student.objects.get(display_name="无关系导入", parent_user__phone="13800001006")
        assignment = TeacherStudentAssignment.objects.get(
            teacher=self.import_admin,
            student=student,
            course=self.cpp_course,
        )
        self.assertEqual(assignment.level_code, "C1")
        self.assertTrue(assignment.is_active)

    def test_reimport_same_student_does_not_duplicate_student_and_updates_assignment(self) -> None:
        self.sign_in(self.import_admin)
        url = reverse("teacher-course-students-detail", args=[self.cpp_course.slug])

        first_response = self.client.post(
            url,
            {
                "form_action": "import_students_csv",
                "student_csv_file": SimpleUploadedFile(
                    "students.csv",
                    "学生姓名,家长手机号,当前学习内容,当前级别\n李四,13800001003,枚举法,GESP2\n".encode("utf-8"),
                    content_type="text/csv",
                ),
            },
        )
        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.post(
            url,
            {
                "form_action": "import_students_csv",
                "student_csv_file": SimpleUploadedFile(
                    "students.csv",
                    "学生姓名,家长手机号,当前学习内容,当前级别\n李四,13800001003,冲刺复习,CSP-S\n".encode("utf-8"),
                    content_type="text/csv",
                ),
            },
        )

        self.assertEqual(second_response.status_code, 200)
        student = Student.objects.select_related("user", "parent_user").get(display_name="李四", parent_user__phone="13800001003")
        self.assertEqual(
            Student.objects.filter(display_name="李四", parent_user__phone="13800001003").count(),
            1,
        )
        self.assertEqual(student.primary_track_name, "冲刺复习")
        self.assertEqual(student.primary_level_name, "CSP-S")
        self.assertEqual(
            TeacherStudentAssignment.objects.filter(
                teacher=self.import_admin,
                student=student,
                course=self.cpp_course,
            ).count(),
            1,
        )
        self.assertEqual(
            TeacherStudentAssignment.objects.get(
                teacher=self.import_admin,
                student=student,
                course=self.cpp_course,
            ).level_code,
            "C4",
        )

    def test_student_csv_import_allows_partial_success_with_row_errors(self) -> None:
        self.sign_in(self.import_admin)

        response = self.client.post(
            reverse("teacher-course-students-detail", args=[self.cpp_course.slug]),
            {
                "form_action": "import_students_csv",
                "student_csv_file": SimpleUploadedFile(
                    "students.csv",
                    (
                        "学生姓名,家长手机号,当前学习内容,当前级别\n"
                        "成功学生,13800001004,二维数组,GESP5\n"
                        "失败学生,13800001005,枚举法,\n"
                    ).encode("utf-8"),
                    content_type="text/csv",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["student_import_result"]["success_count"], 1)
        self.assertEqual(response.context["student_import_result"]["failure_count"], 1)
        self.assertEqual(len(response.context["student_import_result"]["failure_items"]), 1)
        self.assertIn("当前级别不能为空", response.context["student_import_result"]["failure_items"][0]["reason"])
        self.assertTrue(Student.objects.filter(display_name="成功学生", parent_user__phone="13800001004").exists())
        self.assertFalse(Student.objects.filter(display_name="失败学生", parent_user__phone="13800001005").exists())

    def test_teacher_can_batch_restrict_current_student_visible_contents(self) -> None:
        self.sign_in(self.teacher)

        self.save_content_restrictions(
            self.student,
            restricted_contents=[self.array_content, self.gesp2_ascii_content],
        )

        restriction_records = StudentContentAccess.objects.filter(student=self.student, is_open=False)
        self.assertCountEqual(
            restriction_records.values_list("content_id", flat=True),
            [self.array_content.id, self.gesp2_ascii_content.id],
        )
        self.assertFalse(student_has_content_access(self.student, self.array_content.slug))
        self.assertFalse(student_has_content_access(self.student, self.gesp2_ascii_content.slug))

        self.sign_in(self.student_user)
        gesp4_response = self.client.get(reverse("student-cpp-gesp4"))
        self.assertEqual(gesp4_response.status_code, 200)
        self.assertNotContains(gesp4_response, self.array_content.title)

        gesp2_response = self.client.get(reverse("student-cpp-gesp2"))
        self.assertEqual(gesp2_response.status_code, 200)
        self.assertNotContains(gesp2_response, self.gesp2_ascii_content.title)

    def test_teacher_can_restore_batch_restricted_contents_to_default_visible(self) -> None:
        self.sign_in(self.teacher)
        self.save_content_restrictions(
            self.student,
            restricted_contents=[self.array_content, self.gesp2_ascii_content],
        )
        self.save_content_restrictions(self.student, restricted_contents=[])

        self.assertFalse(StudentContentAccess.objects.filter(student=self.student, content=self.array_content).exists())
        self.assertFalse(StudentContentAccess.objects.filter(student=self.student, content=self.gesp2_ascii_content).exists())
        self.assertTrue(student_has_content_access(self.student, self.array_content.slug))
        self.assertTrue(student_has_content_access(self.student, self.gesp2_ascii_content.slug))

        self.sign_in(self.student_user)
        gesp4_response = self.client.get(reverse("student-cpp-gesp4"))
        self.assertContains(gesp4_response, self.array_content.title)
        gesp2_response = self.client.get(reverse("student-cpp-gesp2"))
        self.assertContains(gesp2_response, self.gesp2_ascii_content.title)

    def test_batch_restriction_does_not_affect_other_students(self) -> None:
        self.sign_in(self.teacher)
        self.save_content_restrictions(self.student, restricted_contents=[self.array_content])

        self.assertFalse(student_has_content_access(self.student, self.array_content.slug))
        self.assertTrue(student_has_content_access(self.other_student, self.array_content.slug))

        self.sign_in(self.other_student_user)
        response = self.client.get(reverse("student-cpp-gesp4"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.array_content.title)

    def test_batch_restriction_ignores_content_that_is_not_default_visible(self) -> None:
        c1_student_user = PortalUser.objects.create(
            username="student_c1_restriction",
            role=PortalUser.ROLE_STUDENT,
            full_name="C1 限制学生",
            phone="13800000017",
        )
        c1_student = Student.objects.create(
            user=c1_student_user,
            teacher_user=self.teacher,
            display_name="C1 限制学生",
            grade="三年级",
            campus="虹桥校区",
            primary_course_name="C++",
            primary_track_name="GESP",
            primary_level_name="GESP1",
        )
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=c1_student,
            course=self.cpp_course,
            level_code="C1",
            is_active=True,
        )
        csp_category, _ = CourseCategory.objects.get_or_create(
            course=self.cpp_course,
            slug="csp",
            defaults={
                "title": "CSP",
                "summary": "CSP 课程",
                "sort_order": 2,
                "is_active": True,
            },
        )
        csp_s_level, _ = CourseLevel.objects.get_or_create(
            category=csp_category,
            code="CSP-S",
            defaults={
                "title": "CSP-S",
                "summary": "CSP-S 级别",
                "sort_order": 2,
                "is_active": True,
            },
        )
        c4_only_content = CourseContent.objects.create(
            course=self.cpp_course,
            level=csp_s_level,
            content_type="topic",
            slug="test-c4-only-restriction",
            title="仅 C4 可见内容",
            phase="CSP-S",
            permission_code="C4",
            sort_order=2000,
            route_path="/student/cpp/csp/csp-s/test-c4-only-restriction",
            summary="仅用于不可见内容限制测试",
            has_real_content=False,
            is_active=True,
        )

        self.sign_in(self.teacher)
        self.save_content_restrictions(c1_student, restricted_contents=[c4_only_content])

        self.assertFalse(StudentContentAccess.objects.filter(student=c1_student, content=c4_only_content).exists())
        self.assertFalse(student_has_content_access(c1_student, c4_only_content.slug))

    def test_student_homework_list_is_descending_by_assigned_at_and_detail_can_mark_completed(self) -> None:
        now = timezone.now()
        older = self.create_homework(
            title="二维数组复盘",
            due_date=timezone.localdate() + timedelta(days=1),
            assigned_at=now - timedelta(days=2),
        )
        newer = self.create_homework(
            content=self.binary_search_content,
            title="二分查找预习",
            due_date=timezone.localdate() + timedelta(days=5),
            assigned_at=now - timedelta(hours=1),
        )
        self.sign_in(self.student_user)

        list_response = self.client.get(reverse("student-homework-list"))
        self.assertEqual(list_response.status_code, 200)
        html = list_response.content.decode("utf-8")
        self.assertLess(html.find("二分查找预习"), html.find("二维数组复盘"))

        detail_response = self.client.get(reverse("student-homework-detail", args=[older.id]))
        self.assertContains(detail_response, older.content.route_path)
        self.assertContains(detail_response, 'target="_blank"', html=False)

        complete_response = self.client.post(
            reverse("student-homework-detail", args=[older.id]),
            {"form_action": "mark_completed"},
        )
        self.assertEqual(complete_response.status_code, 302)

        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, HomeworkAssignment.STATUS_COMPLETED)
        self.assertIsNotNone(older.completed_at)
        self.assertEqual(newer.status, HomeworkAssignment.STATUS_ASSIGNED)

    def test_student_homework_detail_shows_assignment_feedback_with_safe_formatting(self) -> None:
        assignment = self.create_homework(
            title="反馈展示作业",
            highlights="第一行亮点\n<script>alert(1)</script>\n    缩进亮点",
            areas_for_growth="第一行不足\n    继续加强边界条件",
        )
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-detail", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "老师本周反馈")
        self.assertContains(response, "student-homework-feedback-card")
        self.assertContains(response, "student-homework-feedback-text")
        self.assertContains(response, "第一行亮点")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)
        self.assertNotContains(response, "<script>alert(1)</script>", html=False)
        self.assertContains(response, "第一行不足")
        self.assertContains(response, "继续加强边界条件")

    def test_student_homework_detail_shows_feedback_placeholders_when_empty(self) -> None:
        assignment = self.create_homework(title="空反馈作业")
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-detail", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "老师暂未填写本周反馈")
        self.assertContains(response, "暂无填写", count=2)

    def test_student_homework_list_shows_summary_entry_when_summary_exists(self) -> None:
        assignment = self.create_homework(title="二维数组周总结入口")
        self.create_homework_summary(
            assignment,
            title="二维数组第 1 周总结",
            summary_html="<p>本周重点完成二维数组专题。</p>",
        )
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '"has_summary": true', html=False)
        self.assertContains(response, reverse("student-homework-summary", args=[assignment.id]), html=False)

    def test_single_summary_can_bind_multiple_assignments_and_all_rows_show_entry(self) -> None:
        first_assignment = self.create_homework(title="二维数组周练 A")
        second_assignment = self.create_homework(
            title="二维数组周练 B",
            content=self.binary_search_content,
        )
        summary = self.create_homework_summary(
            first_assignment,
            second_assignment,
            title="同周统一总结",
            summary_html="<p>本周两条作业共用一篇总结。</p>",
        )
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-list"))

        first_assignment.refresh_from_db()
        second_assignment.refresh_from_db()
        self.assertEqual(first_assignment.summary_id, summary.id)
        self.assertEqual(second_assignment.summary_id, summary.id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("student-homework-summary", args=[first_assignment.id]), html=False)
        self.assertContains(response, reverse("student-homework-summary", args=[second_assignment.id]), html=False)

    def test_student_homework_list_shows_summary_empty_state_when_missing(self) -> None:
        self.create_homework(title="还没有总结的作业")
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '"has_summary": false', html=False)
        self.assertContains(response, '"summary_href": ""', html=False)

    def test_student_homework_summary_detail_renders_sanitized_html_and_print_entry(self) -> None:
        assignment = self.create_homework(title="二维数组本周总结")
        self.create_homework_summary(
            assignment,
            title="二维数组阶段总结",
            summary_html=(
                "<h2>本周课堂聚焦</h2>"
                "<p><strong>数组下标</strong> 和循环配合已经完成。</p>"
                '<div class="diagram">  *\n **\n***</div>'
                "<script>alert('x')</script>"
            ),
        )
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-summary", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "二维数组阶段总结")
        self.assertContains(response, "<h2>本周课堂聚焦</h2>", html=False)
        self.assertContains(response, "<strong>数组下标</strong>", html=False)
        self.assertContains(response, '<div class="diagram">  *\n **\n***</div>', html=False)
        self.assertContains(response, "window.print()", html=False)
        self.assertNotContains(response, "<script", html=False)

    def test_parent_homework_summary_detail_reuses_same_summary_page(self) -> None:
        assignment = self.create_homework(title="家长查看总结")
        self.create_homework_summary(
            assignment,
            title="家长周总结",
            summary_html="<p>家长端也应能查看这份总结。</p>",
        )
        self.sign_in(self.parent)

        response = self.client.get(reverse("parent-homework-summary", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "家长查看")
        self.assertContains(response, "家长周总结")

    def test_student_cannot_complete_cancelled_or_reviewed_homework(self) -> None:
        cancelled = self.create_homework(
            title="已取消作业",
            status=HomeworkAssignment.STATUS_CANCELLED,
        )
        reviewed = self.create_homework(
            title="已评阅作业",
            status=HomeworkAssignment.STATUS_REVIEWED,
            teacher_comment="老师已评语",
        )
        reviewed.completed_at = timezone.now() - timedelta(hours=2)
        reviewed.reviewed_at = timezone.now() - timedelta(hours=1)
        reviewed.save(update_fields=["completed_at", "reviewed_at", "updated_at"])
        self.sign_in(self.student_user)

        self.client.post(
            reverse("student-homework-detail", args=[cancelled.id]),
            {"form_action": "mark_completed"},
        )
        self.client.post(
            reverse("student-homework-detail", args=[reviewed.id]),
            {"form_action": "mark_completed"},
        )

        cancelled.refresh_from_db()
        reviewed.refresh_from_db()
        self.assertEqual(cancelled.status, HomeworkAssignment.STATUS_CANCELLED)
        self.assertIsNone(cancelled.completed_at)
        self.assertEqual(reviewed.status, HomeworkAssignment.STATUS_REVIEWED)
        self.assertEqual(reviewed.teacher_comment, "老师已评语")
        self.assertIsNotNone(reviewed.completed_at)
        self.assertIsNotNone(reviewed.reviewed_at)

    def test_student_repeated_complete_does_not_refresh_completed_at(self) -> None:
        assignment = self.create_homework(
            title="已完成作业",
            status=HomeworkAssignment.STATUS_COMPLETED,
        )
        original_completed_at = timezone.now() - timedelta(hours=3)
        assignment.completed_at = original_completed_at
        assignment.save(update_fields=["completed_at", "updated_at"])
        self.sign_in(self.student_user)

        self.client.post(
            reverse("student-homework-detail", args=[assignment.id]),
            {"form_action": "mark_completed"},
        )

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, HomeworkAssignment.STATUS_COMPLETED)
        self.assertEqual(assignment.completed_at, original_completed_at)

    def test_teacher_can_review_completed_homework(self) -> None:
        assignment = self.create_homework(
            title="二维数组讲后练",
            due_date=timezone.localdate() + timedelta(days=2),
        )
        assignment.mark_completed()
        assignment.save(update_fields=["status", "completed_at", "updated_at"])

        self.sign_in(self.teacher)
        response = self.client.post(
            reverse("teacher-student-detail", args=[self.student.id]),
            {
                "form_action": "review_homework",
                "homework_id": str(assignment.id),
                "teacher_comment": "完成得不错，下一次把边界条件写得更稳一些。",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "评语已保存，学生端会同步显示最新内容。")
        self.assertContains(response, "完成得不错，下一次把边界条件写得更稳一些。")
        self.assertContains(response, "修改评语")

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, HomeworkAssignment.STATUS_REVIEWED)
        self.assertEqual(assignment.teacher_comment, "完成得不错，下一次把边界条件写得更稳一些。")
        self.assertIsNotNone(assignment.reviewed_at)

        self.sign_in(self.student_user)
        student_response = self.client.get(reverse("student-homework-detail", args=[assignment.id]))
        self.assertEqual(student_response.status_code, 200)
        self.assertContains(student_response, "完成得不错，下一次把边界条件写得更稳一些。")

    def test_cancelled_homework_card_keeps_visible_cancelled_state(self) -> None:
        assignment = self.create_homework(title="已取消展示作业")
        assignment.cancel()
        assignment.save(update_fields=["status", "updated_at"])
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-student-detail", args=[self.student.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "已取消")
        self.assertContains(response, "作业已取消，但记录会保留在当前列表里。")
        self.assertContains(response, "暂无评语")

    def test_peer_teacher_cannot_review_or_cancel_other_teachers_homework(self) -> None:
        assignment = self.create_homework(title="别人的作业记录")
        self.sign_in(self.peer_teacher)

        review_response = self.client.post(
            reverse("teacher-student-detail", args=[self.student.id]),
            {
                "form_action": "review_homework",
                "homework_id": str(assignment.id),
                "teacher_comment": "不允许评语",
            },
        )
        cancel_response = self.client.post(
            reverse("teacher-student-detail", args=[self.student.id]),
            {
                "form_action": "cancel_homework",
                "homework_id": str(assignment.id),
            },
        )

        self.assertEqual(review_response.status_code, 404)
        self.assertEqual(cancel_response.status_code, 404)
        assignment.refresh_from_db()
        self.assertEqual(assignment.teacher, self.teacher)
        self.assertEqual(assignment.status, HomeworkAssignment.STATUS_ASSIGNED)
        self.assertEqual(assignment.teacher_comment, "")

    def test_parent_page_shows_weekly_homework_summary(self) -> None:
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        self.create_homework(
            title="本周待完成作业",
            due_date=week_start,
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )
        self.create_homework(
            title="本周已完成作业",
            due_date=week_start + timedelta(days=1),
            status=HomeworkAssignment.STATUS_COMPLETED,
        )
        reviewed = self.create_homework(
            title="本周已评语作业",
            due_date=week_start + timedelta(days=2),
            status=HomeworkAssignment.STATUS_REVIEWED,
            teacher_comment="这周整体完成度不错，继续保持。",
        )
        reviewed.reviewed_at = timezone.now()
        reviewed.save(update_fields=["reviewed_at", "updated_at"])
        self.create_homework(
            title="本周已取消作业",
            due_date=week_start + timedelta(days=3),
            status=HomeworkAssignment.STATUS_CANCELLED,
        )
        self.create_homework(
            title="下周才截止的作业",
            due_date=week_start + timedelta(days=8),
            status=HomeworkAssignment.STATUS_REVIEWED,
            teacher_comment="这条不该进本周摘要。",
        )

        self.sign_in(self.parent)
        response = self.client.get(reverse("parent-student-profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "本周作业摘要")
        self.assertContains(response, "这周整体完成度不错，继续保持。")
        self.assertContains(response, "本周待完成作业")
        self.assertContains(response, "本周已完成作业")
        self.assertContains(response, "本周已评语作业")
        self.assertContains(response, "仅学生账号可进入")
        self.assertNotContains(response, ">进入内容<", html=False)
        self.assertNotContains(response, "本周已取消作业")
        self.assertNotContains(response, "下周才截止的作业")
        self.assertNotContains(response, "这条不该进本周摘要。")

    def test_student_cannot_open_other_students_homework(self) -> None:
        other_assignment = self.create_homework(student=self.other_student, title="别人的作业")
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-detail", args=[other_assignment.id]))

        self.assertEqual(response.status_code, 404)

    def test_student_courses_page_hides_unrelated_course_entries_for_regular_students(self) -> None:
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "C++")
        self.assertContains(response, "练习")
        self.assertNotContains(response, "Python")
        self.assertNotContains(response, 'id="entry-ai"', html=False)
        self.assertNotContains(response, "无人机")

    def test_student_courses_page_keeps_exception_account_entries(self) -> None:
        exception_user, _ = self.create_student_account(
            username="student_portal_exception",
            full_name="林一诺",
            display_name="林一诺",
        )
        self.sign_in(exception_user)

        response = self.client.get(reverse("student-courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "C++")
        self.assertContains(response, "练习")
        self.assertContains(response, "Python")
        self.assertContains(response, 'id="entry-ai"', html=False)

    def assert_cpp_portal_category_slugs(self, *, level_code: str, primary_level_name: str, expected_slugs: list[str]) -> None:
        portal_user, _ = self.create_student_account(
            username=f"student_cpp_portal_{level_code.lower()}",
            full_name=f"{level_code} 分类学生",
            display_name=f"{level_code} 分类学生",
            primary_level_name=primary_level_name,
            assignment_level_code=level_code,
        )
        self.sign_in(portal_user)

        response = self.client.get(reverse("student-cpp"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [card["slug"] for card in response.context["page_shell"]["portal_cards"]],
            expected_slugs,
        )

    def test_c1_student_cpp_portal_hides_csp_and_robotics_categories(self) -> None:
        self.assert_cpp_portal_category_slugs(
            level_code="C1",
            primary_level_name="GESP1",
            expected_slugs=["gesp"],
        )

    def test_c2_student_cpp_portal_hides_csp_and_robotics_categories(self) -> None:
        self.assert_cpp_portal_category_slugs(
            level_code="C2",
            primary_level_name="GESP6",
            expected_slugs=["gesp"],
        )

    def test_c3_student_cpp_portal_shows_csp_but_hides_robotics_category(self) -> None:
        self.assert_cpp_portal_category_slugs(
            level_code="C3",
            primary_level_name="CSP-J",
            expected_slugs=["gesp", "csp"],
        )

    def test_c4_student_cpp_portal_shows_csp_but_hides_robotics_category(self) -> None:
        self.assert_cpp_portal_category_slugs(
            level_code="C4",
            primary_level_name="CSP-S",
            expected_slugs=["gesp", "csp"],
        )
