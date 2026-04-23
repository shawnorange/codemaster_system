from __future__ import annotations

from django.test import TestCase

from entry.course_identity import normalize_assignment_level
from entry.models import (
    Course,
    CourseCategory,
    CourseContent,
    CourseLevel,
    PortalUser,
    Student,
    StudentContentAccess,
    TeacherStudentAssignment,
)
from entry.portal_context import get_teacher_student_homework_contents, student_has_content_access


class ContentVisibilityTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teacher = PortalUser.objects.create(
            username="teacher_content_visibility",
            role=PortalUser.ROLE_TEACHER,
            full_name="权限老师",
            phone="13800001001",
        )
        self.cpp_course, _ = Course.objects.get_or_create(
            slug="cpp",
            defaults={
                "title": "C++",
                "summary": "算法与竞赛",
            },
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
        self.csp_category, _ = CourseCategory.objects.get_or_create(
            course=self.cpp_course,
            slug="csp",
            defaults={
                "title": "CSP",
                "summary": "CSP 课程",
                "sort_order": 2,
                "is_active": True,
            },
        )
        self.levels = {
            "GESP2": CourseLevel.objects.update_or_create(
                category=self.gesp_category,
                code="GESP2",
                defaults={
                    "title": "GESP2",
                    "summary": "GESP2",
                    "sort_order": 2,
                    "is_active": True,
                },
            )[0],
            "GESP6": CourseLevel.objects.update_or_create(
                category=self.gesp_category,
                code="GESP6",
                defaults={
                    "title": "GESP6",
                    "summary": "GESP6",
                    "sort_order": 6,
                    "is_active": True,
                },
            )[0],
            "CSP-J": CourseLevel.objects.update_or_create(
                category=self.csp_category,
                code="CSP-J",
                defaults={
                    "title": "CSP-J",
                    "summary": "CSP-J",
                    "sort_order": 1,
                    "is_active": True,
                },
            )[0],
            "CSP-S": CourseLevel.objects.update_or_create(
                category=self.csp_category,
                code="CSP-S",
                defaults={
                    "title": "CSP-S",
                    "summary": "CSP-S",
                    "sort_order": 2,
                    "is_active": True,
                },
            )[0],
        }
        self.contents = {
            "C1": CourseContent.objects.create(
                course=self.cpp_course,
                level=self.levels["GESP2"],
                content_type="topic",
                slug="content-c1",
                title="C1 内容",
                phase="GESP2",
                permission_code="C1",
                sort_order=1,
                route_path="/student/cpp/gesp/gesp2/content-c1",
                summary="C1 内容",
                has_real_content=False,
                is_active=True,
            ),
            "C2": CourseContent.objects.create(
                course=self.cpp_course,
                level=self.levels["GESP6"],
                content_type="topic",
                slug="content-c2",
                title="C2 内容",
                phase="GESP6",
                permission_code="C2",
                sort_order=2,
                route_path="/student/cpp/gesp/gesp6/content-c2",
                summary="C2 内容",
                has_real_content=False,
                is_active=True,
            ),
            "C3": CourseContent.objects.create(
                course=self.cpp_course,
                level=self.levels["CSP-J"],
                content_type="topic",
                slug="content-c3",
                title="C3 内容",
                phase="CSP-J",
                permission_code="C3",
                sort_order=3,
                route_path="/student/cpp/csp/csp-j/content-c3",
                summary="C3 内容",
                has_real_content=False,
                is_active=True,
            ),
            "C4": CourseContent.objects.create(
                course=self.cpp_course,
                level=self.levels["CSP-S"],
                content_type="topic",
                slug="content-c4",
                title="C4 内容",
                phase="CSP-S",
                permission_code="C4",
                sort_order=4,
                route_path="/student/cpp/csp/csp-s/content-c4",
                summary="C4 内容",
                has_real_content=False,
                is_active=True,
            ),
        }
        self.students = {
            level_code: self._create_student(level_code)
            for level_code in ("C1", "C2", "C3", "C4")
        }

    def _create_student(self, level_code: str) -> Student:
        student_user = PortalUser.objects.create(
            username=f"student_{level_code.lower()}_content_visibility",
            role=PortalUser.ROLE_STUDENT,
            full_name=f"{level_code} 学生",
            phone=f"13800001{len(level_code)}{ord(level_code[-1])}",
        )
        student = Student.objects.create(
            user=student_user,
            teacher_user=self.teacher,
            display_name=f"{level_code} 学生",
            grade="四年级",
            campus="虹桥校区",
            primary_course_name="C++",
            primary_track_name="竞赛",
            primary_level_name=level_code,
        )
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=student,
            course=self.cpp_course,
            level_code=level_code,
            is_active=True,
        )
        return student

    def assert_visible_content_codes(self, level_code: str, expected_codes: list[str]) -> None:
        student = self.students[level_code]
        visible_contents = get_teacher_student_homework_contents(self.teacher, student)
        self.assertEqual({content.permission_code for content in visible_contents}, set(expected_codes))
        for content_code, content in self.contents.items():
            self.assertEqual(
                student_has_content_access(student, content.slug),
                content_code in expected_codes,
            )

    def test_cpp_stage_codes_normalize_to_new_permission_groups(self) -> None:
        self.assertEqual(normalize_assignment_level("cpp", "GESP4"), "C1")
        self.assertEqual(normalize_assignment_level("cpp", "GESP8"), "C2")
        self.assertEqual(normalize_assignment_level("cpp", "CSP-J"), "C3")
        self.assertEqual(normalize_assignment_level("cpp", "CSP-S"), "C4")

    def test_c1_student_can_only_see_c1_content(self) -> None:
        self.assert_visible_content_codes("C1", ["C1"])

    def test_c2_student_can_see_c1_and_c2_content(self) -> None:
        self.assert_visible_content_codes("C2", ["C1", "C2"])

    def test_c3_student_can_see_c1_c2_and_c3_content(self) -> None:
        self.assert_visible_content_codes("C3", ["C1", "C2", "C3"])

    def test_c4_student_can_see_all_permission_scopes(self) -> None:
        self.assert_visible_content_codes("C4", ["C1", "C2", "C3", "C4"])

    def test_student_content_access_override_is_applied(self) -> None:
        student = self.students["C1"]
        StudentContentAccess.objects.create(
            student=student,
            content=self.contents["C1"],
            is_open=False,
            granted_by=self.teacher,
        )
        StudentContentAccess.objects.create(
            student=student,
            content=self.contents["C3"],
            is_open=True,
            granted_by=self.teacher,
        )

        self.assertFalse(student_has_content_access(student, self.contents["C1"].slug))
        self.assertTrue(student_has_content_access(student, self.contents["C3"].slug))
        visible_contents = get_teacher_student_homework_contents(self.teacher, student)
        self.assertEqual({content.permission_code for content in visible_contents}, {"C1"})
