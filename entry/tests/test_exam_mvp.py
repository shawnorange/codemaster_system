from __future__ import annotations

import html
import hashlib
import json
import re
import shutil
import threading
import time
import tempfile
import zipfile
from io import BytesIO
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from entry.auth import AUTH_COOKIE_NAME, AUTH_COOKIE_SALT
from entry.exam_paper_import import (
    format_exam_markdown_for_teacher_edit,
    restore_exam_markdown_code_fences_from_original,
    split_ocr_markdown_into_question_blocks,
)
from entry.models import (
    Course,
    ExamPaper,
    ExamProctorEvent,
    ExamQuestion,
    ExamQuestionBankAsset,
    ExamQuestionBankImportJob,
    ExamQuestionBankItem,
    ExamQuestionBankOption,
    ExamQuestionBankPaper,
    ExamQuestionBankQuestion,
    ExamSession,
    ExamSubmissionAnswer,
    PortalUser,
    Student,
    TeacherStudentAssignment,
)
from entry.portal_context import normalize_exam_question_no_for_sort, render_exam_markdown_for_display


class ExamMVPTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="codemaster-exam-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self) -> None:
        super().setUp()
        self.teacher = PortalUser.objects.create(
            username="exam_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="考试老师",
            phone="13810000001",
        )
        self.parent = PortalUser.objects.create(
            username="exam_parent",
            role=PortalUser.ROLE_PARENT,
            full_name="考试家长",
            phone="13810000002",
        )
        self.student_user = PortalUser.objects.create(
            username="exam_student",
            role=PortalUser.ROLE_STUDENT,
            full_name="考试学生",
            phone="13810000003",
        )
        self.student = Student.objects.create(
            user=self.student_user,
            parent_user=self.parent,
            teacher_user=self.teacher,
            display_name="考试学生",
            grade="五年级",
            campus="虹桥校区",
            primary_course_name="Python",
            primary_track_name="算法",
            primary_level_name="P1",
        )
        self.course = Course.objects.create(slug="python-exam", title="Python", summary="考试测试课程")
        self.cpp_course, _ = Course.objects.update_or_create(
            slug="cpp",
            defaults={"title": "C++", "summary": "算法与竞赛方向"},
        )
        self.drone_course = Course.objects.create(slug="drone-exam", title="无人机", summary="无人机测试课程")
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.student,
            course=self.course,
            level_code="P1",
            is_active=True,
        )
        self.bank_item = ExamQuestionBankItem.objects.create(
            course=self.course,
            level_code="P1",
            knowledge_point="加法基础",
            source=ExamQuestionBankItem.SOURCE_IMPORT,
            source_label="本地题库",
            question_type=ExamQuestionBankItem.QUESTION_TYPE_SINGLE_CHOICE,
            stem="1 + 1 = ?",
            options_json={"A": "2", "B": "3", "C": "4", "D": "5"},
            correct_answer="A",
            analysis="1 加 1 等于 2。",
            score="2",
            created_by=self.teacher,
            is_active=True,
        )

    def sign_in(self, user: PortalUser) -> None:
        self.client.cookies[AUTH_COOKIE_NAME] = signing.dumps(
            {"username": user.username, "role": user.role},
            salt=AUTH_COOKIE_SALT,
        )

    def format_datetime_local(self, value) -> str:
        return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M")

    def build_pdf_bytes(self, *, page_count: int = 1) -> bytes:
        from pypdf import PdfWriter  # type: ignore

        output = tempfile.SpooledTemporaryFile()
        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=595, height=842)
        writer.write(output)
        output.seek(0)
        return output.read()

    def build_docx_bytes(self, text: str) -> bytes:
        buffer = BytesIO()
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>"
            + html.escape(text)
            + "</w:t></w:r></w:p></w:body></w:document>"
        )
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

    def create_exam_via_teacher_view(
        self,
        *,
        mode: str = ExamPaper.MODE_DEADLINE,
        title: str = "第一场考试",
        start_at=None,
        end_at=None,
    ) -> ExamSession:
        self.sign_in(self.teacher)
        now = timezone.now()
        start_at = start_at or now + timedelta(minutes=5)
        end_at = end_at or now + timedelta(days=1)
        response = self.client.post(
            reverse("teacher-exams"),
            {
                "exam_course_id": str(self.course.id),
                "student_ids": [str(self.student.id)],
                "question_bank_item_ids": [str(self.bank_item.id)],
                "exam_mode": mode,
                "exam_title": title,
                "exam_duration_minutes": "45",
                "exam_start_at": self.format_datetime_local(start_at),
                "exam_end_at": self.format_datetime_local(end_at),
                "exam_description": "基础单选题",
                "exam_proctoring_enabled": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("teacher-exams"), response["Location"])
        return ExamSession.objects.select_related("paper").get(student=self.student)

    def test_teacher_workbench_exposes_single_exam_management_entry(self) -> None:
        self.sign_in(self.teacher)
        response = self.client.get(reverse("teacher-students"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "考试管理")
        self.assertNotContains(response, "#exam-panel")

        course_response = self.client.get(reverse("teacher-course-students-detail", args=[self.course.slug]))
        self.assertEqual(course_response.status_code, 200)
        self.assertNotContains(course_response, "#exam-panel")

        exam_response = self.client.get(reverse("teacher-exams"))
        self.assertEqual(exam_response.status_code, 200)
        self.assertContains(exam_response, "发布考试")
        self.assertContains(exam_response, "可用试卷")
        self.assertContains(exam_response, "试卷名称")
        self.assertContains(exam_response, "新增试卷")
        self.assertContains(exam_response, reverse("teacher-exam-paper-new"))
        self.assertContains(exam_response, "考试管理列表")
        self.assertContains(exam_response, "搜索考试名称")
        self.assertContains(exam_response, 'id="available-paper-level-free-filter"', html=False)
        self.assertContains(exam_response, 'id="available-paper-level-select-filter"', html=False)
        self.assertContains(exam_response, 'id="teacher-exam-level-free-filter"', html=False)
        self.assertContains(exam_response, 'id="teacher-exam-level-select-filter"', html=False)
        self.assertNotContains(exam_response, 'id="available-paper-level-options"', html=False)
        self.assertNotContains(exam_response, 'id="teacher-exam-level-options"', html=False)
        self.assertNotContains(exam_response, "所选题目")
        self.assertNotContains(exam_response, 'name="exam_bank_json_file"', html=False)

    def test_teacher_new_exam_paper_page_creates_import_job(self) -> None:
        self.sign_in(self.teacher)
        response = self.client.get(reverse("teacher-exam-paper-new"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "新增试卷")
        self.assertContains(response, 'name="source_pdf"', html=False)
        self.assertContains(response, "识别链路检查")
        self.assertContains(response, "CSP-J")
        self.assertContains(response, "CSP-S")
        self.assertContains(response, 'id="exam-paper-level-options-by-course"', html=False)
        self.assertNotContains(response, '<option value="CSP-J">', html=False)
        self.assertNotContains(response, '<option value="CSP-S">', html=False)
        self.assertContains(response, "上传文件后自动生成")
        self.assertContains(response, ".md")
        self.assertContains(response, ".docx")
        self.assertContains(response, ".png")
        self.assertNotContains(response, 'type="number" name="year"', html=False)
        self.assertNotContains(response, 'type="number" name="month"', html=False)
        self.assertContains(response, "已入库试卷")
        self.assertContains(response, "已删除试卷")
        self.assertContains(response, 'id="exam-bank-paper-table"', html=False)
        self.assertContains(response, 'id="exam-deleted-bank-paper-table"', html=False)
        self.assertNotContains(response, "后续还需要补齐")

        match = re.search(
            r'<script id="exam-paper-level-options-by-course" type="application/json">(.*?)</script>',
            response.content.decode("utf-8"),
            re.S,
        )
        self.assertIsNotNone(match)
        level_options_by_course = json.loads(html.unescape(match.group(1)))
        cpp_values = {item["value"] for item in level_options_by_course[str(self.cpp_course.id)]}
        drone_values = {item["value"] for item in level_options_by_course[str(self.drone_course.id)]}
        self.assertIn("CSP-J", cpp_values)
        self.assertIn("CSP-S", cpp_values)
        self.assertNotIn("CSP-J", drone_values)
        self.assertNotIn("CSP-S", drone_values)

        pdf_bytes = self.build_pdf_bytes(page_count=1)
        upload_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "course_id": str(self.cpp_course.id),
                "level_code": "GESP1",
                "title": "GESP1 2026年3月C++1级真题",
                "source_pdf": SimpleUploadedFile(
                    "2026年3月C++1级试题.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
            },
        )
        self.assertEqual(upload_response.status_code, 302)
        self.assertIn("op=queued", upload_response["Location"])
        self.assertIn("#recent-import-jobs", upload_response["Location"])
        import_job = ExamQuestionBankImportJob.objects.get(source_pdf_id="2026_3_c_1")
        self.assertEqual(import_job.status, ExamQuestionBankImportJob.STATUS_UPLOADED)
        self.assertEqual(import_job.course, self.cpp_course)
        self.assertEqual(import_job.level_code, "GESP1")

        redirected_response = self.client.get(upload_response["Location"])
        self.assertEqual(redirected_response.status_code, 200)
        self.assertContains(redirected_response, "已创建考试文件导入任务")
        self.assertContains(redirected_response, "2026_3_c_1")
        self.assertContains(redirected_response, reverse("teacher-exam-paper-import-job-detail", args=[import_job.id]))

        duplicate_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "course_id": str(self.cpp_course.id),
                "level_code": "GESP1",
                "title": "GESP1 2026年3月C++1级真题",
                "source_pdf": SimpleUploadedFile(
                    "2026年3月C++1级试题.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
            },
        )
        self.assertEqual(duplicate_response.status_code, 302)
        self.assertIn("op=existing", duplicate_response["Location"])
        self.assertEqual(ExamQuestionBankImportJob.objects.filter(source_pdf_id="2026_3_c_1").count(), 1)

    def test_teacher_new_exam_paper_page_shows_confirmed_bank_paper_grid(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2024,
            month=3,
            source_pdf_id="confirmed_new_page_paper",
            source_file="2024年3月C++二级真题.pdf",
            title="2024年3月C++二级真题",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP2",
            title="2024年3月C++二级真题",
            year=2024,
            month=3,
            source_pdf_id="confirmed_new_page_paper",
            source_pdf=SimpleUploadedFile("confirmed.pdf", b"fake", content_type="application/pdf"),
            source_filename="confirmed.pdf",
            source_sha256=hashlib.sha256(b"fake").hexdigest(),
            status=ExamQuestionBankImportJob.STATUS_IMPORTED,
            is_active=True,
        )
        for question_no in [1, 2]:
            ExamQuestionBankQuestion.objects.create(
                paper=bank_paper,
                question_uid=f"confirmed_new_page_paper_q{question_no}",
                question_no=question_no,
                question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                stem_md=f"第 {question_no} 题题干",
                answer_json={"correct_answer": "A"},
                analysis_md="解析",
            )

        response = self.client.get(reverse("teacher-exam-paper-new"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "已入库试卷")
        self.assertContains(response, "2024年3月C++二级真题")
        self.assertContains(response, "confirmed_new_page_paper")
        self.assertContains(response, "2 题")
        self.assertContains(response, reverse("teacher-exam-bank-paper-preview", args=[bank_paper.id]))
        self.assertContains(response, reverse("teacher-exam-bank-paper-edit", args=[bank_paper.id]))
        self.assertContains(response, "exam-bank-paper-delete-form")
        self.assertContains(response, 'name="form_action" value="delete_bank_paper"', html=False)
        self.assertContains(response, "硬删除")
        self.assertContains(response, "去发布")
        self.assertNotContains(response, "后续还需要补齐")

        delete_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "form_action": "delete_bank_paper",
                "bank_paper_id": str(bank_paper.id),
            },
        )

        self.assertEqual(delete_response.status_code, 302)
        self.assertIn("op=bank_paper_deleted", delete_response["Location"])
        self.assertIn("mode=hard", delete_response["Location"])
        self.assertIn("#confirmed-bank-papers", delete_response["Location"])
        self.assertFalse(ExamQuestionBankPaper.objects.filter(id=bank_paper.id).exists())
        import_job.refresh_from_db()
        self.assertFalse(import_job.is_active)

        updated_response = self.client.get(reverse("teacher-exam-paper-new"))
        self.assertNotContains(updated_response, "2024年3月C++二级真题")

    def test_teacher_new_exam_paper_page_soft_deletes_published_bank_paper_for_restore(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2024,
            month=3,
            source_pdf_id="published_restore_paper",
            source_file="published.pdf",
            title="已发布后删除的试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        ExamPaper.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            title="已发布后删除的试卷",
            description=f"来源题库试卷\nexam_question_bank_paper_id={bank_paper.id}",
            status=ExamPaper.STATUS_DRAFT,
            is_active=True,
        )

        response = self.client.get(reverse("teacher-exam-paper-new"))
        self.assertContains(response, "已发布后删除的试卷")
        self.assertContains(response, "这张试卷已经发布过")
        self.assertNotContains(response, ">硬删除<", html=False)

        delete_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "form_action": "delete_bank_paper",
                "bank_paper_id": str(bank_paper.id),
            },
        )

        self.assertEqual(delete_response.status_code, 302)
        self.assertIn("op=bank_paper_deleted", delete_response["Location"])
        self.assertIn("mode=soft", delete_response["Location"])
        self.assertIn("#confirmed-bank-papers", delete_response["Location"])
        bank_paper.refresh_from_db()
        self.assertFalse(bank_paper.is_active)

        updated_response = self.client.get(reverse("teacher-exam-paper-new"))
        self.assertContains(updated_response, "已删除试卷")
        self.assertContains(updated_response, "已发布后删除的试卷")
        self.assertContains(updated_response, 'name="form_action" value="restore_bank_paper"', html=False)

        restore_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "form_action": "restore_bank_paper",
                "bank_paper_id": str(bank_paper.id),
            },
        )

        self.assertEqual(restore_response.status_code, 302)
        self.assertIn("op=bank_paper_restored", restore_response["Location"])
        self.assertIn("#confirmed-bank-papers", restore_response["Location"])
        bank_paper.refresh_from_db()
        self.assertTrue(bank_paper.is_active)

    def test_teacher_reupload_deleted_bank_paper_points_to_restore_grid(self) -> None:
        self.sign_in(self.teacher)
        pdf_bytes = self.build_pdf_bytes(page_count=1)
        source_pdf_id = "2026_3_c_1"
        source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2026,
            month=3,
            source_pdf_id=source_pdf_id,
            source_file="2026年3月C++1级试题.pdf",
            title="已删除的 2026 年 3 月卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=False,
        )
        ExamPaper.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            title="已删除的 2026 年 3 月卷",
            description=f"来源题库试卷\nexam_question_bank_paper_id={bank_paper.id}",
            status=ExamPaper.STATUS_DRAFT,
            is_active=True,
        )
        ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="已删除的 2026 年 3 月卷",
            year=2026,
            month=3,
            source_pdf_id=source_pdf_id,
            source_pdf=SimpleUploadedFile("2026年3月C++1级试题.pdf", pdf_bytes, content_type="application/pdf"),
            source_filename="2026年3月C++1级试题.pdf",
            source_sha256=source_sha256,
            status=ExamQuestionBankImportJob.STATUS_IMPORTED,
            is_active=True,
        )

        response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "course_id": str(self.cpp_course.id),
                "level_code": "GESP1",
                "title": "GESP1 2026年3月C++1级真题",
                "source_pdf": SimpleUploadedFile(
                    "2026年3月C++1级试题.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=restore_available", response["Location"])
        self.assertIn("#deleted-bank-papers", response["Location"])
        redirected_response = self.client.get(response["Location"])
        self.assertContains(redirected_response, "已入库但当前处于已删除状态")
        self.assertContains(redirected_response, "已删除的 2026 年 3 月卷")
        self.assertContains(redirected_response, 'name="form_action" value="restore_bank_paper"', html=False)
        bank_paper.refresh_from_db()
        self.assertFalse(bank_paper.is_active)

    def test_teacher_new_exam_paper_page_accepts_markdown_without_date_in_filename(self) -> None:
        self.sign_in(self.teacher)
        response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "course_id": str(self.cpp_course.id),
                "level_code": "GESP2",
                "title": "Markdown 快速导入",
                "source_pdf": SimpleUploadedFile(
                    "markdown-fast-import.md",
                    b"# Markdown Fast Import\n\nA. option\n",
                    content_type="text/markdown",
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("op=queued", response["Location"])
        import_job = ExamQuestionBankImportJob.objects.get(title="Markdown 快速导入")
        self.assertEqual(import_job.year, timezone.localdate().year)
        self.assertEqual(import_job.month, timezone.localdate().month)
        self.assertIn("markdown-fast-import", import_job.source_pdf_id)
        self.assertIn("文本类文件将直接本地转换为 Markdown", import_job.status_notes)

    def test_teacher_exam_paper_import_job_preview_shows_cleaned_ocr_markdown(self) -> None:
        self.sign_in(self.teacher)
        workspace_dir = Path(self._media_root) / "exam_paper_import_workspace" / "1_2026_3_c_1"
        page_image_dir = workspace_dir / "page_images"
        raw_ocr_dir = workspace_dir / "raw_ocr"
        page_image_dir.mkdir(parents=True, exist_ok=True)
        raw_ocr_dir.mkdir(parents=True, exist_ok=True)
        (page_image_dir / "page_001.png").write_bytes(b"fake-png")
        (raw_ocr_dir / "page_001.md").write_text(
            "第 1 页 OCR Markdown\n"
            "你应该恰好输出 $N$ 行。\n"
            "```cpp\n"
            "1 | #include <iostream>\n"
            "2 |   return 0;\n"
            "```\n",
            encoding="utf-8",
        )
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="GESP1 2026年3月C++1级真题",
            year=2026,
            month=3,
            source_pdf_id="2026_3_c_1",
            source_pdf=SimpleUploadedFile(
                "2026年3月C++1级试题.pdf",
                self.build_pdf_bytes(page_count=1),
                content_type="application/pdf",
            ),
            source_filename="2026年3月C++1级试题.pdf",
            status=ExamQuestionBankImportJob.STATUS_OCR_DONE,
            status_notes="Qwen OCR 已完成，等待结构化解析和老师复核。",
            page_count=1,
            rendered_page_count=1,
            ocr_page_count=1,
            workspace_relative_path="exam_paper_import_workspace/1_2026_3_c_1",
            rendered_pages_json=[
                {
                    "page_no": 1,
                    "relative_path": "exam_paper_import_workspace/1_2026_3_c_1/page_images/page_001.png",
                    "width": 595,
                    "height": 842,
                }
            ],
            raw_ocr_json=[
                {
                    "page_no": 1,
                    "markdown_relative_path": "exam_paper_import_workspace/1_2026_3_c_1/raw_ocr/page_001.md",
                    "char_count": 17,
                    "provider": "qwen",
                }
            ],
        )

        response = self.client.get(reverse("teacher-exam-paper-import-job-detail", args=[import_job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "识别预览")
        self.assertContains(response, "第 1 页 OCR Markdown")
        self.assertContains(response, "你应该恰好输出 N 行。")
        self.assertContains(response, "#include &lt;iostream&gt;")
        self.assertContains(response, "  return 0;")
        self.assertNotContains(response, "$N$")
        self.assertNotContains(response, "1 | #include")
        self.assertContains(response, "2026_3_c_1")

    def test_teacher_confirms_exam_paper_import_job_into_available_paper(self) -> None:
        self.sign_in(self.teacher)
        workspace_dir = Path(self._media_root) / "exam_paper_import_workspace" / "7_2026_3_c_1"
        page_image_dir = workspace_dir / "page_images"
        raw_ocr_dir = workspace_dir / "raw_ocr"
        page_image_dir.mkdir(parents=True)
        raw_ocr_dir.mkdir(parents=True)
        (page_image_dir / "page_001.png").write_bytes(b"fake-png")
        markdown_text = "\n".join(
            [
                "你应该恰好输出 $N$ 行。",
                "",
                "```cpp",
                "1 | #include <iostream>",
                "2 | int main() {",
                "3 |   return 0;",
                "4 | }",
                "```",
                "",
                "一行一个整数 $N$（$5 \\le N \\le 49$，保证 $N$ 为奇数）。",
            ]
        )
        (raw_ocr_dir / "page_001.md").write_text(markdown_text, encoding="utf-8")
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="GESP1 2026年3月C++1级真题",
            year=2026,
            month=3,
            source_pdf_id="2026_3_c_1",
            source_pdf=SimpleUploadedFile(
                "2026年3月C++1级试题.pdf",
                self.build_pdf_bytes(page_count=1),
                content_type="application/pdf",
            ),
            source_filename="2026年3月C++1级试题.pdf",
            status=ExamQuestionBankImportJob.STATUS_OCR_DONE,
            page_count=1,
            rendered_page_count=1,
            ocr_page_count=1,
            workspace_relative_path="exam_paper_import_workspace/7_2026_3_c_1",
            rendered_pages_json=[
                {
                    "page_no": 1,
                    "relative_path": "exam_paper_import_workspace/7_2026_3_c_1/page_images/page_001.png",
                    "width": 595,
                    "height": 842,
                }
            ],
            raw_ocr_json=[
                {
                    "page_no": 1,
                    "markdown_relative_path": "exam_paper_import_workspace/7_2026_3_c_1/raw_ocr/page_001.md",
                    "char_count": len(markdown_text),
                    "provider": "qwen",
                }
            ],
        )

        response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "form_action": "confirm_import_job",
                "import_job_id": str(import_job.id),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("teacher-exams"), response["Location"])
        self.assertIn("op=paper_confirmed", response["Location"])
        self.assertIn("#available-exam-papers", response["Location"])

        import_job.refresh_from_db()
        self.assertEqual(import_job.status, ExamQuestionBankImportJob.STATUS_IMPORTED)
        paper = ExamQuestionBankPaper.objects.get(
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            source_pdf_id="2026_3_c_1",
        )
        self.assertEqual(paper.title, "GESP1 2026年3月C++1级真题")
        self.assertEqual(paper.level, "GESP1")
        question = ExamQuestionBankQuestion.objects.get(paper=paper, question_no=1)
        self.assertEqual(question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN)
        self.assertIn("你应该恰好输出 N 行。", question.stem_md)
        self.assertIn("5 ≤ N ≤ 49", question.stem_md)
        self.assertNotIn("1 | #include", question.stem_md)
        self.assertNotIn("2 | int main", question.stem_md)
        self.assertIn("#include <iostream>", question.stem_md)
        self.assertIn("  return 0;", question.stem_md)
        self.assertEqual(ExamQuestionBankAsset.objects.filter(question=question, asset_role="content").count(), 0)

        exam_response = self.client.get(reverse("teacher-exams"))
        self.assertEqual(exam_response.status_code, 200)
        self.assertContains(exam_response, "GESP1 2026年3月C++1级真题")

    def test_teacher_confirms_pdf_ocr_markdown_as_split_choice_questions(self) -> None:
        self.sign_in(self.teacher)
        workspace_dir = Path(self._media_root) / "exam_paper_import_workspace" / "9_2024_3_c_2"
        page_image_dir = workspace_dir / "page_images"
        raw_ocr_dir = workspace_dir / "raw_ocr"
        page_image_dir.mkdir(parents=True)
        raw_ocr_dir.mkdir(parents=True)
        (page_image_dir / "page_001.png").write_bytes(b"fake-png")
        markdown_text = "\n".join(
            [
                "CCF GESP CCF 编程能力等级认证",
                "Grade Examination of Software Programming",
                "C++ 二级",
                "2024 年 03 月",
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 1 | 2 |",
                "|:---:|:---:|:---:|",
                "| 答案 | B | C |",
                "第 1 题 下列流程图的输出结果是？（）",
                "[流程图见图]",
                "- [ ] A. 优秀",
                "- [ ] B. 良好",
                "- [ ] C. 不及格",
                "- [ ] D. 没有输出",
                "第 2 题 以下变量命名正确的是？（）",
                "- [ ] A. 2_from",
                "- [ ] B. class",
                "- [ ] C. score_2",
                "- [ ] D. return",
            ]
        )
        (raw_ocr_dir / "page_001.md").write_text(markdown_text, encoding="utf-8")
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP2",
            title="2024年3月C++二级真题",
            year=2024,
            month=3,
            source_pdf_id="2024_3_c_2_split",
            source_pdf=SimpleUploadedFile("2024年3月C++二级真题.pdf", self.build_pdf_bytes(page_count=1), content_type="application/pdf"),
            source_filename="2024年3月C++二级真题.pdf",
            status=ExamQuestionBankImportJob.STATUS_OCR_DONE,
            page_count=1,
            rendered_page_count=1,
            ocr_page_count=1,
            rendered_pages_json=[
                {
                    "page_no": 1,
                    "relative_path": "exam_paper_import_workspace/9_2024_3_c_2/page_images/page_001.png",
                    "width": 595,
                    "height": 842,
                }
            ],
            raw_ocr_json=[
                {
                    "page_no": 1,
                    "markdown_relative_path": "exam_paper_import_workspace/9_2024_3_c_2/raw_ocr/page_001.md",
                    "char_count": len(markdown_text),
                    "provider": "qwen",
                }
            ],
        )

        response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "form_action": "confirm_import_job",
                "import_job_id": str(import_job.id),
            },
        )
        self.assertEqual(response.status_code, 302)
        paper = ExamQuestionBankPaper.objects.get(source_pdf_id="2024_3_c_2_split")
        questions = list(paper.questions.order_by("question_no"))
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0].question_type, ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE)
        self.assertEqual(questions[0].answer_json["correct_answer"], "B")
        self.assertEqual(questions[0].analysis_md, "")
        self.assertNotIn("CCF GESP", questions[0].stem_md)
        self.assertNotIn("题号", questions[0].stem_md)
        self.assertIn("第 1 题 下列流程图的输出结果是？", questions[0].stem_md)
        self.assertEqual(
            list(questions[0].options.order_by("sort_order").values_list("option_key", "option_text_md")),
            [("A", "优秀"), ("B", "良好"), ("C", "不及格"), ("D", "没有输出")],
        )
        self.assertEqual(ExamQuestionBankAsset.objects.filter(question__paper=paper).count(), 0)

    def test_teacher_confirms_image_import_job_with_manual_choice_review(self) -> None:
        self.sign_in(self.teacher)
        scratch_course = Course.objects.create(slug="scratch-exam", title="SCRATCH", summary="Scratch 测试课程")
        workspace_dir = Path(self._media_root) / "exam_paper_import_workspace" / "8_scratch_image"
        page_image_dir = workspace_dir / "page_images"
        raw_ocr_dir = workspace_dir / "raw_ocr"
        page_image_dir.mkdir(parents=True)
        raw_ocr_dir.mkdir(parents=True)
        (page_image_dir / "page_001.png").write_bytes(b"fake-png")
        (raw_ocr_dir / "page_001.md").write_text("Scratch 图片题，请看截图作答。", encoding="utf-8")
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=scratch_course,
            level_code="SCRATCH-L1",
            title="Scratch 图片题导入",
            year=2026,
            month=6,
            source_pdf_id="scratch_image",
            source_pdf=SimpleUploadedFile("scratch-question.png", b"fake-png", content_type="image/png"),
            source_filename="scratch-question.png",
            status=ExamQuestionBankImportJob.STATUS_OCR_DONE,
            page_count=1,
            rendered_page_count=1,
            ocr_page_count=1,
            rendered_pages_json=[
                {
                    "page_no": 1,
                    "relative_path": "exam_paper_import_workspace/8_scratch_image/page_images/page_001.png",
                    "width": 640,
                    "height": 480,
                }
            ],
            raw_ocr_json=[
                {
                    "page_no": 1,
                    "markdown_relative_path": "exam_paper_import_workspace/8_scratch_image/raw_ocr/page_001.md",
                    "char_count": 15,
                    "provider": "qwen",
                }
            ],
        )

        preview_response = self.client.get(reverse("teacher-exam-paper-import-job-detail", args=[import_job.id]))
        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "图片题会按每一页生成一条单选题")
        self.assertContains(preview_response, 'name="manual_choice_correct_answer_1"', html=False)

        response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "form_action": "confirm_import_job",
                "import_job_id": str(import_job.id),
                "manual_choice_page_no": ["1"],
                "manual_choice_option_1_a": "向左转 15 度",
                "manual_choice_option_1_b": "重复执行 10 次",
                "manual_choice_option_1_c": "等待 1 秒",
                "manual_choice_option_1_d": "播放声音",
                "manual_choice_correct_answer_1": "B",
                "manual_choice_analysis_1": "截图中积木的循环次数为 10。",
            },
        )

        self.assertEqual(response.status_code, 302)
        paper = ExamQuestionBankPaper.objects.get(
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            source_pdf_id="scratch_image",
        )
        question = ExamQuestionBankQuestion.objects.get(paper=paper, question_no=1)
        self.assertEqual(question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE)
        self.assertEqual(question.answer_json["correct_answer"], "B")
        self.assertEqual(question.analysis_md, "截图中积木的循环次数为 10。")
        self.assertEqual(
            list(question.options.order_by("sort_order").values_list("option_key", "option_text_md")),
            [
                ("A", "向左转 15 度"),
                ("B", "重复执行 10 次"),
                ("C", "等待 1 秒"),
                ("D", "播放声音"),
            ],
        )
        asset = ExamQuestionBankAsset.objects.get(question=question, asset_role="content")
        self.assertEqual(asset.relative_path, "exam_paper_import_workspace/8_scratch_image/page_images/page_001.png")
        self.assertEqual(ExamQuestionBankOption.objects.filter(question=question).count(), 4)

    def test_teacher_can_preview_and_edit_available_exam_bank_paper(self) -> None:
        self.sign_in(self.teacher)
        paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2026,
            month=6,
            source_pdf_id="editable_bank_paper",
            source_file="editable.pdf",
            title="可编辑试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        question = ExamQuestionBankQuestion.objects.create(
            paper=paper,
            question_uid="editable-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="原始题干 $N$\n\n```cpp\n1 int main() {\n2     return 0;\n3 }\n```",
            answer_json={"correct_answer": "A"},
            analysis_md="原始解析",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankOption.objects.create(question=question, option_key="A", option_text_md="原 A", sort_order=1)
        ExamQuestionBankOption.objects.create(question=question, option_key="B", option_text_md="原 B", sort_order=2)
        ExamQuestionBankAsset.objects.create(
            question=question,
            asset_role="content",
            asset_type="image/png",
            relative_path="exam_paper_import_workspace/editable/page_images/page_001.png",
            alt="题图",
        )

        exam_page_response = self.client.get(reverse("teacher-exams"))
        self.assertEqual(exam_page_response.status_code, 200)
        self.assertContains(exam_page_response, reverse("teacher-exam-bank-paper-preview", args=[paper.id]))
        self.assertContains(exam_page_response, reverse("teacher-exam-bank-paper-edit", args=[paper.id]))

        preview_response = self.client.get(reverse("teacher-exam-bank-paper-preview", args=[paper.id]))
        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "试卷内容预览")
        self.assertContains(preview_response, "原始题干 N")
        self.assertContains(preview_response, "原 A")

        edit_response = self.client.get(reverse("teacher-exam-bank-paper-edit", args=[paper.id]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, "手动修改试卷内容")
        self.assertContains(edit_response, 'name="question_ids"', html=False)
        self.assertContains(edit_response, 'data-add-new-question', html=False)
        self.assertContains(edit_response, f'name="question_{question.id}_option_d"', html=False)
        self.assertContains(edit_response, "int main()")
        self.assertNotContains(edit_response, "```cpp")

        save_response = self.client.post(
            reverse("teacher-exam-bank-paper-edit", args=[paper.id]),
            {
                "paper_title": "修改后的试卷",
                "paper_level": "GESP2",
                "question_ids": [str(question.id)],
                "new_question_keys": ["1"],
                f"question_{question.id}_type": ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                f"question_{question.id}_stem_md": "修改后题干 $N$\nint main() {\n  return 0;\n}",
                f"question_{question.id}_answer": "B",
                f"question_{question.id}_option_a": "修改 A",
                f"question_{question.id}_option_b": "修改 B",
                f"question_{question.id}_option_c": "新增 C",
                f"question_{question.id}_option_d": "",
                f"question_{question.id}_analysis": "修改解析",
                "new_question_1_type": ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                "new_question_1_stem_md": "新增题干：下列说法正确的是？",
                "new_question_1_answer": "C",
                "new_question_1_option_a": "新增 A",
                "new_question_1_option_b": "新增 B",
                "new_question_1_option_c": "新增 C",
                "new_question_1_option_d": "",
                "new_question_1_analysis": "",
            },
        )
        self.assertEqual(save_response.status_code, 302)
        self.assertIn(reverse("teacher-exam-bank-paper-preview", args=[paper.id]), save_response["Location"])

        paper.refresh_from_db()
        question.refresh_from_db()
        added_question = ExamQuestionBankQuestion.objects.get(paper=paper, question_no=2)
        self.assertEqual(paper.title, "修改后的试卷")
        self.assertEqual(paper.level, "GESP2")
        self.assertIn("修改后题干", question.stem_md)
        self.assertIn("```cpp", question.stem_md)
        self.assertEqual(question.answer_json["correct_answer"], "B")
        self.assertEqual(question.analysis_md, "修改解析")
        self.assertEqual(
            list(question.options.order_by("sort_order").values_list("option_key", "option_text_md")),
            [("A", "修改 A"), ("B", "修改 B"), ("C", "新增 C")],
        )
        self.assertEqual(added_question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE)
        self.assertEqual(added_question.answer_json["correct_answer"], "C")
        self.assertIn("新增题干", added_question.stem_md)
        self.assertIn("正确答案：C", added_question.analysis_md)
        self.assertEqual(
            list(added_question.options.order_by("sort_order").values_list("option_key", "option_text_md")),
            [("A", "新增 A"), ("B", "新增 B"), ("C", "新增 C")],
        )

        updated_preview_response = self.client.get(reverse("teacher-exam-bank-paper-preview", args=[paper.id]))
        self.assertContains(updated_preview_response, "修改后的试卷")
        self.assertContains(updated_preview_response, "修改后题干 N")
        self.assertContains(updated_preview_response, "修改 B")
        self.assertContains(updated_preview_response, "新增题干")

    def test_teacher_can_delete_available_bank_paper_from_publish_grid(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2026,
            month=6,
            source_pdf_id="deletable_bank_paper",
            source_file="deletable.pdf",
            title="待删除可用试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )

        exam_page_response = self.client.get(reverse("teacher-exams"))

        self.assertEqual(exam_page_response.status_code, 200)
        self.assertContains(exam_page_response, "待删除可用试卷")
        self.assertContains(exam_page_response, 'name="form_action" value="delete_bank_paper"', html=False)
        self.assertContains(exam_page_response, "available-paper-actions")
        self.assertContains(exam_page_response, "available-paper-delete-button")
        available_rows = list(exam_page_response.context["available_paper_rows"])
        bank_paper_row = next(row for row in available_rows if row["id"] == bank_paper.id)
        self.assertEqual(bank_paper_row["delete_label"], "硬删除")
        self.assertIn("硬删除题库快照", bank_paper_row["delete_confirm_message"])

        delete_response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "delete_bank_paper",
                "bank_paper_id": str(bank_paper.id),
            },
        )

        self.assertEqual(delete_response.status_code, 302)
        self.assertIn("op=bank_paper_deleted", delete_response["Location"])
        self.assertIn("mode=hard", delete_response["Location"])
        self.assertIn("#available-exam-papers", delete_response["Location"])
        self.assertFalse(ExamQuestionBankPaper.objects.filter(id=bank_paper.id).exists())
        updated_response = self.client.get(reverse("teacher-exams"))
        self.assertNotContains(updated_response, "待删除可用试卷")

    def test_teacher_publish_available_bank_paper_adds_draft_to_exam_management(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2024,
            month=3,
            source_pdf_id="publishable_2024_3_c_2",
            source_file="2024年3月C++二级试题.pdf",
            title="2024年3月C++二级真题",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        bank_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="publishable_2024_3_c_2-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列说法正确的是？",
            answer_json={"correct_answer": "A"},
            analysis_md="解析内容",
            programming_json={},
            full_json={},
        )
        for index, (key, text) in enumerate({"A": "正确", "B": "错误", "C": "不确定", "D": "以上都不对"}.items(), start=1):
            ExamQuestionBankOption.objects.create(
                question=bank_question,
                option_key=key,
                option_text_md=text,
                sort_order=index,
            )
        true_false_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="publishable_2024_3_c_2-q-016",
            question_no=16,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
            stem_md="第 1 题 C++ 是编程语言。",
            answer_json={"answer": True},
            analysis_md="判断题解析",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankOption.objects.create(question=true_false_question, option_key="A", option_text_md="正确", sort_order=1)
        ExamQuestionBankOption.objects.create(question=true_false_question, option_key="B", option_text_md="错误", sort_order=2)
        ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="publishable_2024_3_c_2-q-026",
            question_no=26,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
            stem_md="3.1 编程题 1\n\n问题描述",
            answer_json={},
            analysis_md="参考程序提交后显示。",
            programming_json={"title": "乘法问题"},
            full_json={},
        )

        exam_page_response = self.client.get(reverse("teacher-exams"))
        self.assertEqual(exam_page_response.status_code, 200)
        self.assertContains(exam_page_response, 'name="form_action" value="create_exam_from_bank_paper"', html=False)
        self.assertContains(exam_page_response, 'id="teacher-exam-management"', html=False)
        self.assertContains(exam_page_response, 'id="available-paper-publish-dialog"', html=False)
        self.assertContains(exam_page_response, "考试老师")
        self.assertNotContains(exam_page_response, "发布设置弹窗下一步接入")

        start_at = timezone.now() + timedelta(minutes=10)
        end_at = start_at + timedelta(minutes=45)
        response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "create_exam_from_bank_paper",
                "bank_paper_id": str(bank_paper.id),
                "exam_schedule_mode": "scheduled",
                "exam_schedule_start_at": self.format_datetime_local(start_at),
                "exam_schedule_end_at": self.format_datetime_local(end_at),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=bank_paper_added", response["Location"])
        self.assertIn("#teacher-exam-management", response["Location"])
        exam_paper = ExamPaper.objects.get(title="2024年3月C++二级真题")
        self.assertEqual(exam_paper.teacher, self.teacher)
        self.assertEqual(exam_paper.course, self.cpp_course)
        self.assertEqual(exam_paper.status, ExamPaper.STATUS_DRAFT)
        self.assertEqual(exam_paper.mode, ExamPaper.MODE_TIMED)
        self.assertEqual(exam_paper.duration_minutes, 45)
        self.assertEqual(self.format_datetime_local(exam_paper.start_at), self.format_datetime_local(start_at))
        self.assertEqual(self.format_datetime_local(exam_paper.end_at), self.format_datetime_local(end_at))
        self.assertIn(f"exam_question_bank_paper_id={bank_paper.id}", exam_paper.description)
        self.assertEqual(exam_paper.questions.count(), 3)
        exam_question = exam_paper.questions.get(question_no=1)
        self.assertEqual(exam_question.correct_answer, "A")
        self.assertEqual(exam_question.options_json["A"], "正确")
        true_false_exam_question = exam_paper.questions.get(question_no=16)
        self.assertEqual(true_false_exam_question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE)
        self.assertEqual(true_false_exam_question.correct_answer, "A")
        programming_exam_question = exam_paper.questions.get(question_no=26)
        self.assertEqual(programming_exam_question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING)
        self.assertEqual(programming_exam_question.score, 0)
        self.assertEqual(exam_paper.sessions.count(), 0)

        updated_end_at = start_at + timedelta(minutes=60)
        duplicate_response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "create_exam_from_bank_paper",
                "bank_paper_id": str(bank_paper.id),
                "exam_schedule_mode": "scheduled",
                "exam_schedule_start_at": self.format_datetime_local(start_at),
                "exam_schedule_end_at": self.format_datetime_local(updated_end_at),
            },
        )

        self.assertEqual(duplicate_response.status_code, 302)
        self.assertIn("op=bank_paper_exists", duplicate_response["Location"])
        self.assertEqual(ExamPaper.objects.filter(title="2024年3月C++二级真题").count(), 1)
        exam_paper.refresh_from_db()
        self.assertEqual(exam_paper.duration_minutes, 60)

        updated_exam_page_response = self.client.get(reverse("teacher-exams"))
        self.assertContains(updated_exam_page_response, "2024年3月C++二级真题")
        self.assertContains(updated_exam_page_response, "GESP2")
        self.assertContains(updated_exam_page_response, "未开始")

    def test_teacher_can_publish_bank_paper_as_immediate_countdown_exam(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2026,
            month=3,
            source_pdf_id="countdown_2026_3_c_1",
            source_file="2026年3月C++一级试题.pdf",
            title="2026年3月C++一级真题",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        bank_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="countdown_2026_3_c_1-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
            stem_md="第 1 题 C++ 是编程语言。",
            answer_json={"answer": True},
            analysis_md="C++ 是一门通用编程语言。",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankOption.objects.create(question=bank_question, option_key="A", option_text_md="正确", sort_order=1)
        ExamQuestionBankOption.objects.create(question=bank_question, option_key="B", option_text_md="错误", sort_order=2)
        response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "create_exam_from_bank_paper",
                "bank_paper_id": str(bank_paper.id),
                "exam_schedule_mode": "countdown",
                "exam_schedule_duration_minutes": "30",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=bank_paper_started", response["Location"])
        exam_paper = ExamPaper.objects.get(title="2026年3月C++一级真题")
        self.assertEqual(exam_paper.status, ExamPaper.STATUS_PUBLISHED)
        self.assertEqual(exam_paper.mode, ExamPaper.MODE_DEADLINE)
        self.assertEqual(exam_paper.duration_minutes, 30)
        self.assertRegex(exam_paper.access_code, r"^\d{6}$")
        self.assertGreater(exam_paper.end_at, timezone.now())
        self.assertEqual(exam_paper.questions.count(), 1)
        self.assertEqual(exam_paper.questions.get().correct_answer, "A")

        updated_exam_page_response = self.client.get(reverse("teacher-exams"))
        self.assertContains(updated_exam_page_response, "进行中")

    def test_teacher_can_update_exam_schedule_from_management_grid(self) -> None:
        self.sign_in(self.teacher)
        paper = ExamPaper.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            title="待调整考试",
            description="",
            mode=ExamPaper.MODE_DEADLINE,
            duration_minutes=45,
            end_at=timezone.now() + timedelta(days=1),
            status=ExamPaper.STATUS_DRAFT,
            is_active=True,
        )
        start_at = timezone.now() + timedelta(minutes=20)
        end_at = start_at + timedelta(minutes=70)
        response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "update_exam_schedule",
                "paper_id": str(paper.id),
                "exam_schedule_mode": "scheduled",
                "exam_schedule_start_at": self.format_datetime_local(start_at),
                "exam_schedule_end_at": self.format_datetime_local(end_at),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=exam_schedule_updated", response["Location"])
        paper.refresh_from_db()
        self.assertEqual(paper.mode, ExamPaper.MODE_TIMED)
        self.assertEqual(paper.duration_minutes, 70)
        self.assertEqual(self.format_datetime_local(paper.start_at), self.format_datetime_local(start_at))
        self.assertEqual(self.format_datetime_local(paper.end_at), self.format_datetime_local(end_at))

    def test_teacher_preview_splits_legacy_raw_pdf_page_and_hides_full_page_asset(self) -> None:
        self.sign_in(self.teacher)
        paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2024,
            month=3,
            source_pdf_id="legacy_raw_2024_3_c_2",
            source_file="2024年3月C++二级试题解析.pdf",
            title="2024年3月C++二级真题",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        raw_markdown = "\n".join(
            [
                "OCR Markdown 页 · legacy_raw-page-001",
                "CCF GESP CCF 编程能力等级认证",
                "Grade Examination of Software Programming",
                "C++ 二级",
                "2024 年 03 月",
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 1 | 2 |",
                "|:---:|:---:|:---:|",
                "| 答案 | B | C |",
                "第 1 题 下列流程图的输出结果是？（）",
                "[流程图见图]",
                "- [ ] A. 优秀",
                "- [ ] B. 良好",
                "- [ ] C. 不及格",
                "- [ ] D. 没有输出",
                "第 2 题 以下变量命名正确的是？（）",
                "- [ ] A. 2_from",
                "- [ ] B. class",
                "- [ ] C. score_2",
                "- [ ] D. return",
            ]
        )
        question = ExamQuestionBankQuestion.objects.create(
            paper=paper,
            question_uid="legacy_raw-page-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN,
            stem_md=raw_markdown,
            answer_json={"source": "ocr_markdown_page", "needs_teacher_review": True},
            analysis_md="",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankAsset.objects.create(
            question=question,
            asset_role="content",
            asset_type="image/png",
            relative_path="exam_paper_import_workspace/legacy_raw/page_images/page_001.png",
            alt="第 1 页原始截图",
        )

        response = self.client.get(reverse("teacher-exam-bank-paper-preview", args=[paper.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "第 1 题 下列流程图的输出结果是？")
        self.assertContains(response, "第 2 题 以下变量命名正确的是？")
        self.assertContains(response, "答案 B")
        self.assertContains(response, "答案 C")
        self.assertContains(response, "优秀")
        self.assertNotContains(response, "OCR Markdown 页")
        self.assertNotContains(response, "CCF GESP")
        self.assertNotContains(response, "题号 |")
        self.assertNotContains(response, "exam_paper_import_workspace/legacy_raw/page_images/page_001.png")
        self.assertNotContains(response, '<img class="exam-bank-paper-question__asset', html=False)

    def test_teacher_preview_and_edit_split_legacy_raw_full_paper_sections(self) -> None:
        self.sign_in(self.teacher)
        paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2024,
            month=3,
            source_pdf_id="legacy_sections_2024_3_c_2",
            source_file="2024年3月C++二级试题解析.pdf",
            title="2024年3月C++二级真题",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        page_one_markdown = "\n".join(
            [
                "CCF GESP CCF 编程能力等级认证",
                "C++ 二级",
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
                "| 答案 | B | B | B | C | B | C | D | B | C | A | D | A | C | C | C |",
                "第8题 下面C++代码执行后的输出是？（）",
            ]
        )
        page_two_markdown = "\n".join(
            [
                "```cpp",
                "int s,t,ans;",
                "s = 2, t = 10;",
                "ans = 0;",
                "while (s != t){",
                "    if (t % 2 == 0 && t / 2 >= s)",
                "        t /= 2;",
                "    else",
                "        t -= 1;",
                "    ans += 1;",
                "}",
                "cout << ans;",
                "```",
                "- [ ] A. 2",
                "- [ ] B. 3",
                "- [ ] C. 4",
                "- [ ] D. 5",
                "2 判断题（每题 2 分，共 20 分）",
                "| 题号 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
                "| 答案 | × | √ | × | × | × | √ | × | × | √ | √ |",
                "**第1题** cout << t 的结果为 28.5。",
                "**3 编程题（每题 25 分，共 50 分）**",
                "3.1 编程题 1",
                "试题名称：日字矩阵",
                "样例输出",
                "```text",
                "|---|",
                "|xxx|",
                "|---|",
                "```",
                "3.1.1 参考程序",
                "```cpp",
                "#include <iostream>",
                "using namespace std;",
                "int main() {",
                "    return 0;",
                "}",
                "```",
            ]
        )
        first_raw_question = ExamQuestionBankQuestion.objects.create(
            paper=paper,
            question_uid="legacy_sections-page-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN,
            stem_md=page_one_markdown,
            answer_json={"source": "ocr_markdown_page", "needs_teacher_review": True},
            analysis_md="",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankQuestion.objects.create(
            paper=paper,
            question_uid="legacy_sections-page-002",
            question_no=2,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN,
            stem_md=page_two_markdown,
            answer_json={"source": "ocr_markdown_page", "needs_teacher_review": True},
            analysis_md="",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankAsset.objects.create(
            question=first_raw_question,
            asset_role="content",
            asset_type="image/png",
            relative_path="exam_paper_import_workspace/legacy_sections/page_images/page_001.png",
            alt="第 1 页原始截图",
        )

        preview_response = self.client.get(reverse("teacher-exam-bank-paper-preview", args=[paper.id]))

        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "第 8 题")
        self.assertContains(preview_response, "int s,t,ans;")
        self.assertContains(preview_response, "判断题")
        self.assertContains(preview_response, "第 16 题")
        self.assertContains(preview_response, "编程题")
        self.assertContains(preview_response, "第 26 题")
        self.assertContains(preview_response, "|---|")
        self.assertNotContains(preview_response, '"source": "ocr_question_split"')
        self.assertNotContains(preview_response, "CCF GESP")
        self.assertNotContains(preview_response, "题号 |")
        self.assertNotContains(preview_response, "exam_paper_import_workspace/legacy_sections/page_images/page_001.png")

        edit_response = self.client.get(reverse("teacher-exam-bank-paper-edit", args=[paper.id]))

        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(paper.questions.count(), 3)
        structured_question = ExamQuestionBankQuestion.objects.get(paper=paper, question_no=8)
        self.assertEqual(structured_question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE)
        self.assertIn("int s,t,ans;", structured_question.stem_md)
        self.assertNotIn("CCF GESP", structured_question.stem_md)
        self.assertNotIn("第1题** cout", structured_question.stem_md)
        programming_question = ExamQuestionBankQuestion.objects.get(paper=paper, question_no=26)
        self.assertEqual(programming_question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING)
        self.assertIn("样例输出", programming_question.stem_md)
        self.assertNotIn("参考程序", programming_question.stem_md)
        self.assertNotIn("#include <iostream>", programming_question.stem_md)
        self.assertIn("参考程序", programming_question.analysis_md)
        self.assertIn("#include <iostream>", programming_question.analysis_md)
        self.assertContains(edit_response, "int s,t,ans;")
        self.assertContains(edit_response, "日字矩阵")
        self.assertNotContains(edit_response, "CCF GESP")
        self.assertNotContains(edit_response, "| 题号 |")

    def test_ocr_question_split_stops_malformed_choice_code_at_next_section(self) -> None:
        markdown_text = "\n".join(
            [
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 15 |",
                "| :--- | :--- |",
                "| 答案 | B |",
                "第 15 题 N 是一个正整数。如果 N 的所有奇数位的数位和等于所有偶数位的数位和，则称它是一个“双螺旋数”。空白处应该填入的代码是（ ）。",
                "```cpp",
                "int i, N, N1=0, N2=0, N0;",
                "cin >> N;",
                "while (N){",
                "    ________________________",
                "    ________________________",
                "}",
                "```",
                "A.",
                "```cpp",
                "N1 += N%10, N /= 10;",
                "N2 += N%10, N /= 10;",
                "```",
                "B.",
                "```cpp",
                "N1 += N%10, N %= 10;",
                "N2 += N%10, N %= 10;",
                "```",
                "```",
                "C.",
                "```cpp",
                "```",
                "D.",
                "```cpp",
                "```",
                "2 判断题（每题 2 分，共 20 分）",
                "| 题号 | 1 |",
                "| :--- | :--- |",
                "| 答案 | √ |",
                "第 1 题 小明的电话手表中装有一款特定操作系统。",
                "3 编程题（每题 25 分，共 50 分）",
                "3.1 编程题 1",
                "试题名称：交朋友",
                "3.1.1 题目描述",
                "Alice 想要和身高最接近她的人交朋友。",
                "3.1.7 参考程序",
                "```cpp",
                "#include <iostream>",
                "int main() { return 0; }",
                "```",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)

        self.assertGreaterEqual(len(parsed_questions), 3)
        question_15 = next(item for item in parsed_questions if item["question_no"] == 15)
        true_false = next(item for item in parsed_questions if item["question_no"] == 16)
        programming = next(item for item in parsed_questions if item["question_no"] == 17)
        self.assertEqual(question_15["question_type"], ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE)
        self.assertEqual(question_15["answer_json"]["correct_answer"], "B")
        self.assertEqual(question_15["analysis_md"], "")
        self.assertNotIn("2 判断题", question_15["stem_md"])
        self.assertNotIn("3 编程题", question_15["stem_md"])
        self.assertIn("N1 += N%10", question_15["options"]["B"])
        self.assertEqual(true_false["question_type"], ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE)
        self.assertIn("电话手表", true_false["stem_md"])
        self.assertEqual(programming["question_type"], ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING)
        self.assertIn("交朋友", programming["stem_md"])
        self.assertIn("参考程序", programming["analysis_md"])
        self.assertIn("#include <iostream>", programming["analysis_md"])

    def test_ocr_markdown_cleaning_drops_page_footer_and_code_line_numbers(self) -> None:
        markdown_text = "\n".join(
            [
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 1 |",
                "| 答案 | A |",
                "第 1 题 阅读代码，输出是？（ ）",
                "```cpp",
                "1 int a = 1;",
                "2 | cout << a;",
                "3 return 0;",
                "```",
                "A. 1",
                "B. 2",
                "第 1 页 / 共 10 页",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)

        question = parsed_questions[0]
        self.assertNotIn("第 1 页 / 共 10 页", question["stem_md"])
        self.assertIn("int a = 1;", question["stem_md"])
        self.assertIn("cout << a;", question["stem_md"])
        self.assertIn("return 0;", question["stem_md"])
        self.assertNotIn("1 int a", question["stem_md"])
        self.assertNotIn("2 | cout", question["stem_md"])

    def test_teacher_edit_display_hides_code_fence_but_save_restores_cpp_marker(self) -> None:
        original = "第 1 题 阅读代码。\n\n```cpp\n1 int main() {\n2     return 0;\n3 }\n```"

        display_text = format_exam_markdown_for_teacher_edit(original)
        saved_text = restore_exam_markdown_code_fences_from_original(
            "第 1 题 修改后阅读代码。\nint main() {\n    return 0;\n}",
            original,
        )

        self.assertNotIn("```cpp", display_text)
        self.assertIn("int main()", display_text)
        self.assertIn("```cpp", saved_text)
        self.assertIn("return 0;", saved_text)

    def test_ocr_question_split_closes_malformed_code_before_next_choice_question(self) -> None:
        markdown_text = "\n".join(
            [
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 10 | 11 | 12 | 13 | 14 | 15 |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
                "| 答案 | A | B | C | D | A | B |",
                "第 10 题 下面 C++ 代码执行后输出是？（ ）",
                "A.",
                "```cpp",
                "cout << 10;",
                "B. cout << 11;",
                "第 11 题 变量命名正确的是？（ ）",
                "A. 2name",
                "B. name2",
                "C. int",
                "D. return",
                "第 12 题 下列说法正确的是？（ ）",
                "A. 错误项",
                "B. 错误项",
                "C. 正确项",
                "D. 错误项",
                "第 13 题 下列选项是循环语句的是？（ ）",
                "A. if",
                "B. else",
                "C. return",
                "D. while",
                "第 14 题 下列哪个是输入语句？（ ）",
                "A. cin",
                "B. cout",
                "C. return",
                "D. break",
                "第 15 题 空白处应填入的代码是？（ ）",
                "A.",
                "```cpp",
                "N1 += N%10;",
                "```",
                "B.",
                "```cpp",
                "N2 += N%10;",
                "```",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)

        parsed_by_no = {item["question_no"]: item for item in parsed_questions}
        self.assertTrue({10, 11, 12, 13, 14, 15}.issubset(parsed_by_no))
        self.assertIn("cout << 10", parsed_by_no[10]["options"]["A"])
        self.assertNotIn("第 11 题", parsed_by_no[10]["options"]["A"])
        self.assertEqual(parsed_by_no[11]["answer_json"]["correct_answer"], "B")
        self.assertEqual(parsed_by_no[12]["answer_json"]["correct_answer"], "C")
        self.assertEqual(parsed_by_no[15]["answer_json"]["correct_answer"], "B")
        self.assertEqual(parsed_by_no[15]["analysis_md"], "")

    def test_ocr_question_split_detects_programming_title_without_section_header(self) -> None:
        markdown_text = "\n".join(
            [
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 15 |",
                "| 答案 | B |",
                "第 15 题 空白处应该填入的代码是？（ ）",
                "A. 代码 A",
                "B. 代码 B",
                "3.1 编程题 1",
                "试题名称：交朋友",
                "3.1.1 题目描述",
                "Alice 想要和身高最接近她的人交朋友。",
                "3.2 编程题 2",
                "试题名称：数字替换",
                "3.2.1 题目描述",
                "把数字 4 替换成 8。",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)

        self.assertEqual(parsed_questions[0]["question_no"], 15)
        programming_questions = [
            item for item in parsed_questions if item["question_type"] == ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING
        ]
        self.assertEqual(len(programming_questions), 2)
        self.assertIn("交朋友", programming_questions[0]["stem_md"])
        self.assertIn("数字替换", programming_questions[1]["stem_md"])

    def test_teacher_exam_paper_import_jobs_status_endpoint(self) -> None:
        self.sign_in(self.teacher)
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="进度测试试卷",
            year=2026,
            month=3,
            source_pdf_id="progress_2026_3_c_1",
            source_pdf=SimpleUploadedFile(
                "progress.pdf",
                self.build_pdf_bytes(page_count=1),
                content_type="application/pdf",
            ),
            source_filename="progress.pdf",
            status=ExamQuestionBankImportJob.STATUS_OCR_RUNNING,
            page_count=17,
            rendered_page_count=17,
            ocr_page_count=10,
            status_notes="Qwen OCR 已完成 10/17 页。",
        )

        response = self.client.get(reverse("teacher-exam-paper-import-jobs-status"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("jobs", payload)
        row = next(item for item in payload["jobs"] if item["id"] == import_job.id)
        self.assertEqual(row["ocr_page_count"], 10)
        self.assertFalse(row["can_confirm"])
        self.assertEqual(row["confirm_label"], "确认")

    def test_teacher_exam_paper_import_jobs_can_be_soft_deleted(self) -> None:
        self.sign_in(self.teacher)
        other_teacher = PortalUser.objects.create(
            username="other_exam_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="其他考试老师",
            phone="13810000009",
        )
        job_one = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="待删除试卷一",
            year=2026,
            month=3,
            source_pdf_id="2026_3_c_1",
            source_pdf=SimpleUploadedFile("job-one.pdf", self.build_pdf_bytes(page_count=1), content_type="application/pdf"),
            source_filename="job-one.pdf",
            status=ExamQuestionBankImportJob.STATUS_UPLOADED,
        )
        job_two = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="待删除试卷二",
            year=2026,
            month=4,
            source_pdf_id="2026_4_c_1",
            source_pdf=SimpleUploadedFile("job-two.pdf", self.build_pdf_bytes(page_count=1), content_type="application/pdf"),
            source_filename="job-two.pdf",
            status=ExamQuestionBankImportJob.STATUS_OCR_DONE,
        )
        other_job = ExamQuestionBankImportJob.objects.create(
            teacher=other_teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="其他老师试卷",
            year=2026,
            month=5,
            source_pdf_id="2026_5_c_1",
            source_pdf=SimpleUploadedFile("other-job.pdf", self.build_pdf_bytes(page_count=1), content_type="application/pdf"),
            source_filename="other-job.pdf",
            status=ExamQuestionBankImportJob.STATUS_OCR_DONE,
        )

        page_response = self.client.get(reverse("teacher-exam-paper-new"))
        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, "一键删除")
        self.assertContains(page_response, "bulk_delete_import_jobs")
        self.assertContains(page_response, "delete_import_job")

        delete_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "form_action": "delete_import_job",
                "import_job_id": str(job_one.id),
            },
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assertIn("op=deleted", delete_response["Location"])
        self.assertIn("count=1", delete_response["Location"])
        job_one.refresh_from_db()
        job_two.refresh_from_db()
        self.assertFalse(job_one.is_active)
        self.assertTrue(job_two.is_active)

        bulk_delete_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "form_action": "bulk_delete_import_jobs",
                "import_job_ids": [str(job_two.id), str(other_job.id)],
            },
        )
        self.assertEqual(bulk_delete_response.status_code, 302)
        self.assertIn("op=deleted", bulk_delete_response["Location"])
        self.assertIn("count=1", bulk_delete_response["Location"])
        job_two.refresh_from_db()
        other_job.refresh_from_db()
        self.assertFalse(job_two.is_active)
        self.assertTrue(other_job.is_active)

    @override_settings(
        QWEN_API_KEY="test-qwen-key",
        QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
        QWEN_OCR_MODEL="qwen-test-ocr",
        QWEN_MAX_RETRIES=1,
    )
    def test_exam_paper_import_worker_renders_pdf_and_saves_raw_ocr(self) -> None:
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="GESP1 2026年3月C++1级真题",
            year=2026,
            month=3,
            source_pdf_id="2026_3_c_1",
            source_pdf=SimpleUploadedFile(
                "2026年3月C++1级试题.pdf",
                self.build_pdf_bytes(page_count=1),
                content_type="application/pdf",
            ),
            source_filename="2026年3月C++1级试题.pdf",
            status=ExamQuestionBankImportJob.STATUS_UPLOADED,
        )

        with patch(
            "entry.exam_paper_import.qwen_ocr_image",
            return_value=("第 1 页 OCR Markdown", {"choices": [{"message": {"content": "第 1 页 OCR Markdown"}}]}),
        ) as mock_qwen:
            call_command("process_exam_paper_import_jobs", "--once")

        import_job.refresh_from_db()
        self.assertEqual(import_job.status, ExamQuestionBankImportJob.STATUS_OCR_DONE)
        self.assertEqual(import_job.page_count, 1)
        self.assertEqual(import_job.rendered_page_count, 1)
        self.assertEqual(import_job.ocr_page_count, 1)
        self.assertEqual(mock_qwen.call_count, 1)
        page_path = self._media_root_path(import_job.rendered_pages_json[0]["relative_path"])
        markdown_path = self._media_root_path(import_job.raw_ocr_json[0]["markdown_relative_path"])
        self.assertTrue(page_path.exists())
        self.assertEqual(markdown_path.read_text(encoding="utf-8"), "第 1 页 OCR Markdown")

    def test_exam_paper_import_worker_converts_markdown_without_qwen(self) -> None:
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP2",
            title="Markdown 快速导入",
            year=2026,
            month=6,
            source_pdf_id="2026_6_markdown_fast_import",
            source_pdf=SimpleUploadedFile(
                "markdown-fast-import.md",
                b"# Markdown Fast Import\n\n```cpp\n1 #include <iostream>\n2 int main() {\n3   return 0;\n4 }\n```\n",
                content_type="text/markdown",
            ),
            source_filename="markdown-fast-import.md",
            status=ExamQuestionBankImportJob.STATUS_UPLOADED,
        )

        with patch("entry.exam_paper_import.qwen_ocr_image") as mock_qwen:
            call_command("process_exam_paper_import_jobs", "--once")

        import_job.refresh_from_db()
        self.assertEqual(import_job.status, ExamQuestionBankImportJob.STATUS_OCR_DONE)
        self.assertEqual(import_job.page_count, 1)
        self.assertEqual(import_job.rendered_page_count, 0)
        self.assertEqual(import_job.ocr_page_count, 1)
        self.assertEqual(mock_qwen.call_count, 0)
        markdown_path = self._media_root_path(import_job.raw_ocr_json[0]["markdown_relative_path"])
        self.assertIn("Markdown Fast Import", markdown_path.read_text(encoding="utf-8"))

    def test_exam_paper_import_worker_converts_text_like_files_without_qwen(self) -> None:
        files = [
            ("plain.txt", b"Plain text import", "text/plain"),
            (
                "paper.json",
                json.dumps({"questions": [{"question_no": 1, "stem": "JSON stem", "answer": "A"}]}, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
            ("page.html", b"<html><body><h1>HTML Import</h1><p>Question text</p></body></html>", "text/html"),
            ("document.docx", self.build_docx_bytes("DOCX Import"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ]
        import_jobs = []
        for index, (filename, content, content_type) in enumerate(files, start=1):
            import_jobs.append(
                ExamQuestionBankImportJob.objects.create(
                    teacher=self.teacher,
                    course=self.cpp_course,
                    level_code="GESP1",
                    title=f"文本类导入 {index}",
                    year=2026,
                    month=6,
                    source_pdf_id=f"2026_6_text_like_{index}",
                    source_pdf=SimpleUploadedFile(filename, content, content_type=content_type),
                    source_filename=filename,
                    status=ExamQuestionBankImportJob.STATUS_UPLOADED,
                )
            )

        with patch("entry.exam_paper_import.qwen_ocr_image") as mock_qwen:
            call_command("process_exam_paper_import_jobs", "--limit", str(len(import_jobs)), "--poll-interval", "0.2")

        self.assertEqual(mock_qwen.call_count, 0)
        expected_snippets = ["Plain text import", "JSON stem", "HTML Import", "DOCX Import"]
        for import_job, expected_snippet in zip(import_jobs, expected_snippets, strict=True):
            import_job.refresh_from_db()
            self.assertEqual(import_job.status, ExamQuestionBankImportJob.STATUS_OCR_DONE)
            self.assertEqual(import_job.page_count, 1)
            self.assertEqual(import_job.rendered_page_count, 0)
            self.assertEqual(import_job.ocr_page_count, 1)
            markdown_path = self._media_root_path(import_job.raw_ocr_json[0]["markdown_relative_path"])
            self.assertIn(expected_snippet, markdown_path.read_text(encoding="utf-8"))

    @override_settings(
        QWEN_API_KEY="test-qwen-key",
        QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
        QWEN_OCR_MODEL="qwen-test-ocr",
        QWEN_MAX_RETRIES=1,
        EXAM_QWEN_OCR_CONCURRENCY=2,
    )
    def test_exam_paper_import_worker_ocr_uses_configured_page_concurrency(self) -> None:
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="并发 OCR 试卷",
            year=2026,
            month=6,
            source_pdf_id="2026_6_parallel_ocr",
            source_pdf=SimpleUploadedFile(
                "parallel-ocr.pdf",
                self.build_pdf_bytes(page_count=3),
                content_type="application/pdf",
            ),
            source_filename="parallel-ocr.pdf",
            status=ExamQuestionBankImportJob.STATUS_UPLOADED,
        )
        thread_ids: set[int] = set()
        thread_lock = threading.Lock()

        def fake_qwen_ocr_image(*, image_path, prompt=None):
            with thread_lock:
                thread_ids.add(threading.get_ident())
            time.sleep(0.02)
            page_no = int(Path(image_path).stem.rsplit("_", 1)[-1])
            text = f"第 {page_no} 页 OCR Markdown"
            return text, {"choices": [{"message": {"content": text}}]}

        with patch("entry.exam_paper_import.qwen_ocr_image", side_effect=fake_qwen_ocr_image) as mock_qwen:
            call_command("process_exam_paper_import_jobs", "--once")

        import_job.refresh_from_db()
        self.assertEqual(import_job.status, ExamQuestionBankImportJob.STATUS_OCR_DONE)
        self.assertEqual(import_job.page_count, 3)
        self.assertEqual(import_job.ocr_page_count, 3)
        self.assertEqual(mock_qwen.call_count, 3)
        self.assertEqual([item["page_no"] for item in import_job.raw_ocr_json], [1, 2, 3])
        self.assertGreaterEqual(len(thread_ids), 2)

    @override_settings(
        QWEN_API_KEY="test-qwen-key",
        QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
        QWEN_OCR_MODEL="qwen-test-ocr",
        QWEN_MAX_RETRIES=1,
        EXAM_QWEN_OCR_CONCURRENCY=1,
    )
    def test_exam_paper_import_worker_continues_after_failed_job(self) -> None:
        failed_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP1",
            title="失败 PDF",
            year=2026,
            month=6,
            source_pdf_id="2026_6_failed_pdf",
            source_pdf=SimpleUploadedFile(
                "failed.pdf",
                self.build_pdf_bytes(page_count=1),
                content_type="application/pdf",
            ),
            source_filename="failed.pdf",
            status=ExamQuestionBankImportJob.STATUS_UPLOADED,
        )
        next_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            level_code="GESP2",
            title="失败后继续处理的 Markdown",
            year=2026,
            month=6,
            source_pdf_id="2026_6_after_failure",
            source_pdf=SimpleUploadedFile(
                "after-failure.md",
                b"# After Failure\n\nThis job should still run.",
                content_type="text/markdown",
            ),
            source_filename="after-failure.md",
            status=ExamQuestionBankImportJob.STATUS_UPLOADED,
        )

        with patch("entry.exam_paper_import.qwen_ocr_image", side_effect=RuntimeError("mock qwen failure")):
            call_command("process_exam_paper_import_jobs", "--limit", "2", "--poll-interval", "0.2")

        failed_job.refresh_from_db()
        next_job.refresh_from_db()
        self.assertEqual(failed_job.status, ExamQuestionBankImportJob.STATUS_FAILED)
        self.assertIn("mock qwen failure", failed_job.error_message)
        self.assertEqual(next_job.status, ExamQuestionBankImportJob.STATUS_OCR_DONE)
        self.assertEqual(next_job.ocr_page_count, 1)

    def test_exam_question_markdown_renderer_preserves_code_and_math_display(self) -> None:
        rendered = str(
            render_exam_markdown_for_display(
                "你应该恰好输出 $N$ 行。\n"
                "一行一个整数 $N$（$5 \\le N \\le 49$，保证 $N$ 为奇数）。\n\n"
                "```cpp\n"
                "#include <iostream>\n"
                "int main() {\n"
                "  return 0;\n"
                "}\n"
                "```"
            )
        )

        self.assertIn("你应该恰好输出 N 行。", rendered)
        self.assertIn("5 ≤ N ≤ 49", rendered)
        self.assertIn("<pre", rendered)
        self.assertIn("#include &lt;iostream&gt;", rendered)
        self.assertIn("  return 0;", rendered)
        self.assertNotIn("$N$", rendered)

    def _media_root_path(self, relative_path: str):
        return Path(self._media_root) / relative_path

    def test_teacher_imports_exam_question_bank_json(self) -> None:
        self.sign_in(self.teacher)
        payload = {
            "questions": [
                {
                    "level_code": "P1",
                    "knowledge_point": "变量基础",
                    "stem": "Python 变量赋值正确的是？",
                    "options": {
                        "A": "x = 1",
                        "B": "1 = x",
                        "C": "int x = 1",
                        "D": "var x := 1",
                    },
                    "correct_answer": "A",
                    "analysis": "Python 使用等号进行变量赋值。",
                    "score": 2,
                }
            ]
        }

        response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "import_question_bank",
                "exam_bank_course_id": str(self.course.id),
                "exam_bank_json_file": SimpleUploadedFile(
                    "exam-bank.json",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    content_type="application/json",
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=bank_imported", response["Location"])
        imported_item = ExamQuestionBankItem.objects.get(stem="Python 变量赋值正确的是？")
        self.assertEqual(imported_item.course_id, self.course.id)
        self.assertEqual(imported_item.knowledge_point, "变量基础")
        self.assertEqual(imported_item.options_json["A"], "x = 1")
        self.assertEqual(imported_item.correct_answer, "A")

        page_response = self.client.get(reverse("teacher-exams"))
        self.assertEqual(page_response.status_code, 200)
        question_ids = {item["id"] for item in page_response.context["question_bank_rows"]}
        self.assertIn(imported_item.id, question_ids)

    def test_teacher_exam_question_bank_import_rejects_non_single_choice(self) -> None:
        self.sign_in(self.teacher)
        payload = {
            "questions": [
                {
                    "question_type": "programming",
                    "knowledge_point": "循环",
                    "stem": "写一个循环。",
                    "options": {"A": "A", "B": "B", "C": "C", "D": "D"},
                    "correct_answer": "A",
                }
            ]
        }

        response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "import_question_bank",
                "exam_bank_course_id": str(self.course.id),
                "exam_bank_json_file": SimpleUploadedFile(
                    "exam-bank.json",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    content_type="application/json",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "暂只支持 single_choice")

    def test_teacher_creates_exam_and_student_submits_for_score(self) -> None:
        session = self.create_exam_via_teacher_view()
        self.assertEqual(session.paper.status, ExamPaper.STATUS_PUBLISHED)
        self.assertEqual(session.paper.mode, ExamPaper.MODE_DEADLINE)
        self.assertTrue(session.paper.proctoring_enabled)
        self.assertEqual(session.paper.questions.count(), 1)
        question = session.paper.questions.get()
        self.assertEqual(question.source_snapshot_json["question_bank_item_id"], self.bank_item.id)

        self.sign_in(self.student_user)
        list_response = self.client.get(reverse("student-exam-list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "第一场考试")

        detail_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "开始考试")
        session.refresh_from_db()
        self.assertEqual(session.status, ExamSession.STATUS_ASSIGNED)

        start_response = self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "start_exam"},
        )
        self.assertEqual(start_response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.status, ExamSession.STATUS_IN_PROGRESS)

        submit_response = self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "submit_exam", f"question_{question.id}": "A"},
        )
        self.assertEqual(submit_response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.status, ExamSession.STATUS_AUTO_CHECKED)
        self.assertEqual(session.correct_count, 1)
        self.assertEqual(str(session.earned_score), "2.00")
        self.assertEqual(ExamSubmissionAnswer.objects.filter(session=session, is_correct=True).count(), 1)
        result_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertEqual(result_response.status_code, 200)
        self.assertNotContains(result_response, "不能重新开始")
        self.assertContains(result_response, "考试结果")
        self.assertContains(result_response, "整张卷子重新练习")
        self.assertContains(result_response, "打印带结果的整张卷子")
        self.assertContains(result_response, 'data-practice-start-form', html=False)
        self.assertContains(result_response, "点开后将直接记时并增加一条练习记录，请确定你有完整的练习时间。")
        self.assertContains(result_response, "data-practice-confirm-cancel", html=False)
        self.assertContains(result_response, "data-practice-confirm-submit", html=False)

    def test_student_exam_result_renders_analysis_code_and_print_page(self) -> None:
        session = self.create_exam_via_teacher_view()
        question = session.paper.questions.get()
        question.analysis = "参考程序：\n\n```cpp\nint main() {\n    return 0;\n}\n```"
        question.save(update_fields=["analysis"])

        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "submit_exam", f"question_{question.id}": "B"},
        )

        result_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertEqual(result_response.status_code, 200)
        self.assertContains(result_response, '<pre class="exam-markdown-body__code"><code>', html=False)
        self.assertContains(result_response, "    return 0;", html=False)
        self.assertContains(result_response, "打印空白错题卷")

        print_response = self.client.get(reverse("student-exam-print", args=[session.id]) + "?variant=result_wrong")
        self.assertEqual(print_response.status_code, 200)
        self.assertContains(print_response, "完整错题卷")
        self.assertContains(print_response, "你的答案：B；正确答案：A")
        self.assertContains(print_response, "    return 0;", html=False)

    def test_teacher_can_mark_exam_question_important_for_correction_views(self) -> None:
        session = self.create_exam_via_teacher_view()
        question = session.paper.questions.get()
        self.sign_in(self.teacher)
        detail_response = self.client.get(reverse("teacher-exam-detail", args=[session.paper_id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "标记重点")

        mark_response = self.client.post(
            reverse("teacher-exam-detail", args=[session.paper_id]),
            {
                "form_action": "mark_exam_question_important",
                "question_id": str(question.id),
                "important_note": "订正时重点看循环边界。",
            },
        )
        self.assertEqual(mark_response.status_code, 302)
        question.refresh_from_db()
        self.assertTrue(question.is_important)
        self.assertEqual(question.important_note, "订正时重点看循环边界。")
        self.assertEqual(question.important_marked_by, self.teacher)

        marked_detail_response = self.client.get(reverse("teacher-exam-detail", args=[session.paper_id]))
        self.assertContains(marked_detail_response, "已标重点")
        self.assertContains(marked_detail_response, "订正时重点看循环边界。")

        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        in_progress_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertEqual(in_progress_response.status_code, 200)
        self.assertNotContains(in_progress_response, "老师标记重点")
        self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "submit_exam", f"question_{question.id}": "B"},
        )
        result_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertContains(result_response, "老师标记重点")
        self.assertContains(result_response, "订正时重点看循环边界。")
        self.assertNotContains(result_response, "data-clear-important-marks", html=False)

        print_response = self.client.get(reverse("student-exam-print", args=[session.id]) + "?variant=result_wrong")
        self.assertContains(print_response, "老师标记重点")
        self.assertContains(print_response, "订正时重点看循环边界。")
        self.assertNotContains(print_response, "data-clear-print-important", html=False)

        blank_print_response = self.client.get(reverse("student-exam-print", args=[session.id]) + "?variant=blank_wrong")
        self.assertContains(blank_print_response, "老师标记重点")
        self.assertNotContains(blank_print_response, "订正时重点看循环边界。")
        self.assertContains(blank_print_response, "data-clear-print-important", html=False)

        hidden_mark_print_response = self.client.get(
            reverse("student-exam-print", args=[session.id]) + "?variant=blank_wrong&hide_important=1"
        )
        self.assertNotContains(hidden_mark_print_response, "老师标记重点")
        self.assertNotContains(hidden_mark_print_response, "订正时重点看循环边界。")
        self.assertNotContains(hidden_mark_print_response, "data-clear-print-important", html=False)

        full_practice_response = self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "start_full_practice"},
        )
        self.assertEqual(full_practice_response.status_code, 302)
        full_practice = ExamSession.objects.get(paper=session.paper, student=self.student, attempt_no=2)
        practice_detail_response = self.client.get(reverse("student-exam-detail", args=[full_practice.id]))
        self.assertContains(practice_detail_response, "老师标记重点")
        self.assertNotContains(practice_detail_response, "订正时重点看循环边界。")

        self.sign_in(self.teacher)
        teacher_submission_response = self.client.get(reverse("teacher-student-exam-detail", args=[self.student.id, session.id]))
        self.assertContains(teacher_submission_response, "老师标记重点")
        self.assertContains(teacher_submission_response, "订正时重点看循环边界。")

    def test_student_can_create_practice_attempts_and_wrong_practice_requires_explanation(self) -> None:
        session = self.create_exam_via_teacher_view()
        question = session.paper.questions.get()
        correct_original_question = ExamQuestion.objects.create(
            paper=session.paper,
            question_no=2,
            question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem="2 + 2 = ?",
            options_json={"A": "4", "B": "3", "C": "2", "D": "1"},
            correct_answer="A",
            analysis="2 加 2 等于 4。",
            score="2",
            wrong_point_label="加法基础",
            is_active=True,
        )

        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {
                "form_action": "submit_exam",
                f"question_{question.id}": "B",
                f"question_{correct_original_question.id}": "A",
            },
        )

        full_practice_response = self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "start_full_practice"},
        )
        self.assertEqual(full_practice_response.status_code, 302)
        full_practice = ExamSession.objects.get(paper=session.paper, student=self.student, attempt_no=2)
        self.assertEqual(full_practice.session_type, ExamSession.SESSION_TYPE_FULL_PRACTICE)
        self.assertEqual(full_practice.status, ExamSession.STATUS_IN_PROGRESS)
        self.client.post(
            reverse("student-exam-detail", args=[full_practice.id]),
            {
                "form_action": "submit_exam",
                f"question_{question.id}": "A",
                f"question_{correct_original_question.id}": "B",
            },
        )

        wrong_practice_response = self.client.post(
            reverse("student-exam-detail", args=[full_practice.id]),
            {"form_action": "start_wrong_practice"},
        )
        self.assertEqual(wrong_practice_response.status_code, 302)
        wrong_practice = ExamSession.objects.get(paper=session.paper, student=self.student, attempt_no=3)
        self.assertEqual(wrong_practice.session_type, ExamSession.SESSION_TYPE_WRONG_PRACTICE)
        self.assertEqual(wrong_practice.question_scope_json["question_ids"], [question.id])

        wrong_detail_response = self.client.get(reverse("student-exam-detail", args=[wrong_practice.id]))
        self.assertEqual(wrong_detail_response.status_code, 200)
        self.assertContains(wrong_detail_response, "写出本题解析")
        self.assertContains(wrong_detail_response, "1 + 1 = ?")
        self.assertNotContains(wrong_detail_response, "2 + 2 = ?")

        short_explanation_response = self.client.post(
            reverse("student-exam-detail", args=[wrong_practice.id]),
            {
                "form_action": "submit_exam",
                f"question_{question.id}": "A",
                f"explanation_{question.id}": "太短",
            },
        )
        self.assertEqual(short_explanation_response.status_code, 200)
        self.assertContains(short_explanation_response, "不少于 10 字")
        self.assertContains(short_explanation_response, f'const focusQuestionId = "{question.id}"', html=False)
        self.assertContains(short_explanation_response, "太短")

        submit_response = self.client.post(
            reverse("student-exam-detail", args=[wrong_practice.id]),
            {
                "form_action": "submit_exam",
                f"question_{question.id}": "A",
                f"explanation_{question.id}": "这里重新计算后确认答案应该选择A",
            },
        )
        self.assertEqual(submit_response.status_code, 302)
        wrong_practice.refresh_from_db()
        self.assertEqual(wrong_practice.status, ExamSession.STATUS_AUTO_CHECKED)
        answer = ExamSubmissionAnswer.objects.get(session=wrong_practice, question=question)
        self.assertEqual(answer.explanation_text, "这里重新计算后确认答案应该选择A")

        record_response = self.client.get(reverse("student-exam-record-detail", args=[session.paper_id]))
        self.assertEqual(record_response.status_code, 200)
        self.assertContains(record_response, "第 3 次")
        self.assertContains(record_response, "错题练习")

    def test_teacher_generates_access_code_and_student_enters_exam(self) -> None:
        session = self.create_exam_via_teacher_view()
        paper = session.paper

        self.sign_in(self.teacher)
        response = self.client.post(
            reverse("teacher-exams"),
            {"form_action": "start_exam", "paper_id": str(paper.id)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("op=access_code_generated", response["Location"])
        paper.refresh_from_db()
        self.assertRegex(paper.access_code, r"^\d{6}$")

        self.sign_in(self.student_user)
        list_response = self.client.get(reverse("student-exam-list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "输入口令开启考试")
        self.assertContains(list_response, "考试记录")

        enter_response = self.client.post(
            reverse("student-exam-list"),
            {"form_action": "enter_exam_access_code", "exam_access_code": paper.access_code},
        )
        self.assertEqual(enter_response.status_code, 302)
        self.assertIn(reverse("student-exam-detail", args=[session.id]), enter_response["Location"])

        record_response = self.client.get(reverse("student-exam-record-detail", args=[paper.id]))
        self.assertEqual(record_response.status_code, 200)
        self.assertContains(record_response, "本场考试记录")
        self.assertContains(record_response, "第 1 次")

    def test_teacher_cannot_regenerate_access_code_while_exam_in_progress(self) -> None:
        session = self.create_exam_via_teacher_view()
        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        session.refresh_from_db()
        self.assertEqual(session.status, ExamSession.STATUS_IN_PROGRESS)

        self.sign_in(self.teacher)
        response = self.client.post(
            reverse("teacher-exams"),
            {"form_action": "start_exam", "paper_id": str(session.paper_id)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前正在考试中，不允许重新生成口令")

    def test_teacher_exam_detail_shows_leaderboard_and_question_stats(self) -> None:
        session = self.create_exam_via_teacher_view()
        question = session.paper.questions.get()
        question.analysis = "参考程序：\n\n```cpp\nint main() {\n    return 0;\n}\n```"
        question.save(update_fields=["analysis"])
        second_question = ExamQuestion.objects.create(
            paper=session.paper,
            question_no=2,
            question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem="2 + 2 = ?",
            options_json={"A": "4", "B": "3", "C": "2", "D": "1"},
            correct_answer="A",
            analysis="2 加 2 等于 4。",
            score="2",
            wrong_point_label="加法基础",
            is_active=True,
        )
        third_question = ExamQuestion.objects.create(
            paper=session.paper,
            question_no=3,
            question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem="3 + 3 = ?",
            options_json={"A": "6", "B": "5", "C": "4", "D": "3"},
            correct_answer="A",
            analysis="3 加 3 等于 6。",
            score="2",
            wrong_point_label="加法基础",
            is_active=True,
        )
        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {
                "form_action": "submit_exam",
                f"question_{question.id}": "A",
                f"question_{second_question.id}": "B",
                f"question_{third_question.id}": "C",
            },
        )
        late_submitted_at = session.paper.end_at + timedelta(minutes=5)
        practice_session = ExamSession.objects.create(
            paper=session.paper,
            student=self.student,
            assigned_by=self.teacher,
            attempt_no=2,
            session_type=ExamSession.SESSION_TYPE_FULL_PRACTICE,
            status=ExamSession.STATUS_AUTO_CHECKED,
            total_count=3,
            correct_count=0,
            wrong_count=3,
            total_score="6.00",
            earned_score="0.00",
            started_at=late_submitted_at - timedelta(minutes=20),
            submitted_at=late_submitted_at,
            checked_at=late_submitted_at,
        )
        late_exam_session = ExamSession.objects.create(
            paper=session.paper,
            student=self.student,
            assigned_by=self.teacher,
            attempt_no=3,
            session_type=ExamSession.SESSION_TYPE_EXAM,
            status=ExamSession.STATUS_AUTO_CHECKED,
            total_count=3,
            correct_count=0,
            wrong_count=3,
            total_score="6.00",
            earned_score="0.00",
            started_at=late_submitted_at - timedelta(minutes=15),
            submitted_at=late_submitted_at,
            checked_at=late_submitted_at,
        )
        for scoped_session in [practice_session, late_exam_session]:
            for scoped_question, selected_answer in [
                (question, "B"),
                (second_question, "B"),
                (third_question, "C"),
            ]:
                ExamSubmissionAnswer.objects.create(
                    session=scoped_session,
                    question=scoped_question,
                    selected_answer=selected_answer,
                    is_correct=False,
                    score="0.00",
                    correct_answer_snapshot=scoped_question.correct_answer,
                    analysis_snapshot=scoped_question.analysis,
                )

        self.sign_in(self.teacher)
        response = self.client.get(reverse("teacher-exam-detail", args=[session.paper_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "分数排行榜 Top10")
        self.assertContains(response, "考试学生")
        self.assertContains(response, "对 1")
        self.assertContains(response, "错 1；错误答案还有：C")
        self.assertContains(response, "错误率 100%")
        self.assertContains(response, 'class="topic-access-option teacher-exam-question-option"', html=False)
        self.assertContains(response, "独立练习情况")
        self.assertContains(response, "全卷练习")
        self.assertContains(response, '<pre class="exam-markdown-body__code"><code>', html=False)
        self.assertContains(response, "答案解析")
        self.assertEqual(len(response.context["leaderboard_rows"]), 1)
        self.assertEqual(len(response.context["practice_session_rows"]), 1)
        self.assertEqual(response.context["practice_session_rows"][0]["score_summary"], "0 / 3")
        rows_by_question_no = {row["question_no"]: row for row in response.context["question_rows"]}
        self.assertEqual(rows_by_question_no[1]["correct_count"], 1)
        self.assertEqual(rows_by_question_no[1]["wrong_count"], 0)
        self.assertEqual(rows_by_question_no[2]["wrong_count"], 1)
        self.assertEqual(rows_by_question_no[3]["wrong_count"], 1)
        content = response.content.decode("utf-8")
        third_index = content.find('data-question-no="3"')
        second_index = content.find('data-question-no="2"')
        first_index = content.find('data-question-no="1"')
        self.assertGreaterEqual(second_index, 0)
        self.assertLess(second_index, third_index)
        self.assertLess(third_index, first_index)

        practice_detail_response = self.client.get(response.context["practice_session_rows"][0]["detail_href"])
        self.assertEqual(practice_detail_response.status_code, 200)
        self.assertContains(practice_detail_response, "提交详情")
        self.assertContains(practice_detail_response, "学生答案：B；正确答案：A")
        self.assertContains(practice_detail_response, "teacher-exam-submission-option--wrong")
        self.assertContains(practice_detail_response, '<pre class="exam-markdown-body__code"><code>', html=False)

    def test_teacher_exam_detail_question_no_sort_handles_natural_labels(self) -> None:
        self.assertEqual(normalize_exam_question_no_for_sort("1."), 1)
        self.assertEqual(normalize_exam_question_no_for_sort("第 2 题"), 2)
        self.assertEqual(normalize_exam_question_no_for_sort("3、"), 3)
        self.assertEqual(normalize_exam_question_no_for_sort("第二题"), 2)
        self.assertEqual(normalize_exam_question_no_for_sort("第十一题"), 11)
        self.assertEqual(normalize_exam_question_no_for_sort("第二十题"), 20)

    def test_proctor_event_records_switch_count(self) -> None:
        session = self.create_exam_via_teacher_view()
        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        response = self.client.post(
            reverse("api-student-exam-proctor-event", args=[session.id]),
            data=json.dumps({"event_type": ExamProctorEvent.EVENT_VISIBILITY_HIDDEN}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.switch_count, 1)
        self.assertEqual(session.proctor_events.filter(event_type=ExamProctorEvent.EVENT_VISIBILITY_HIDDEN).count(), 1)

    def test_timed_exam_cannot_start_before_scheduled_time(self) -> None:
        session = self.create_exam_via_teacher_view(
            mode=ExamPaper.MODE_TIMED,
            title="定时考试",
            start_at=timezone.now() + timedelta(days=1),
        )
        self.sign_in(self.student_user)
        response = self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "start_exam"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前考试还未到开始时间")
        session.refresh_from_db()
        self.assertEqual(session.status, ExamSession.STATUS_ASSIGNED)
