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
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from entry.auth import AUTH_COOKIE_NAME, AUTH_COOKIE_SALT
from entry.exam_paper_import import (
    SOURCE_TYPE_DOCX,
    confirm_exam_question_bank_import_job,
    format_exam_markdown_for_teacher_edit,
    process_text_exam_import_job,
    restore_exam_markdown_code_fences_from_original,
    split_ocr_markdown_into_question_blocks,
)
from entry.models import (
    Course,
    CourseCategory,
    CourseLevel,
    ExamPaper,
    ExamProctorEvent,
    ExamQuestion,
    ExamQuestionAnalysisBlock,
    ExamQuestionAnalysisSuggestion,
    ExamQuestionBankAsset,
    ExamQuestionBankImportJob,
    ExamQuestionBankItem,
    ExamKnowledgePointMap,
    ExamQuestionBankOption,
    ExamQuestionBankPaper,
    ExamQuestionBankQuestion,
    ExamRun,
    ExamSession,
    ExamSubmissionAnswer,
    PortalUser,
    Student,
    StudentSiteMessage,
    Teacher,
    TeacherStudentAssignment,
)
from entry.portal_context import (
    exclude_markdown_embedded_image_paths,
    infer_exam_bank_paper_subject,
    normalize_exam_question_no_for_sort,
    render_exam_markdown_for_display,
    teacher_can_operate_exam_subject,
)


class ExamMVPTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="codemaster-exam-media-")
        cls._media_override = override_settings(
            MEDIA_ROOT=cls._media_root,
            QWEN_API_KEY="",
            DASHSCOPE_API_KEY="",
            QWEN_BASE_URL="",
            QWEN_OCR_MODEL="",
        )
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

    def build_demo_text_layer_pdf_bytes(self) -> bytes:
        import fitz  # type: ignore

        document = fitz.open()
        page = document.new_page(width=595, height=842)
        font_candidates = [
            Path("/System/Library/Fonts/STHeiti Medium.ttc"),
            Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        ]
        font_path = next((path for path in font_candidates if path.exists()), None)
        font_name = "helv"
        if font_path:
            page.insert_font(fontname="cjk", fontfile=str(font_path))
            font_name = "cjk"
        page.insert_text((50, 45), "1 单选题（每题 2 分，共 30 分）", fontsize=11, fontname=font_name)
        page.insert_text((50, 65), "题号 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15", fontsize=9, fontname=font_name)
        page.insert_text((50, 80), "答案 A B C D A B C D A B C D A B C", fontsize=9, fontname=font_name)
        y = 110
        for question_no in range(1, 16):
            page.insert_text((50, y), f"第 {question_no} 题 自动切题测试题干（ ）", fontsize=10, fontname=font_name)
            page.insert_text((70, y + 13), "A. 选项 A   B. 选项 B   C. 选项 C   D. 选项 D", fontsize=8, fontname=font_name)
            y += 45
        page.insert_text((260, 820), "第 1 页 / 共 1 页", fontsize=8, fontname=font_name)
        page = document.new_page(width=595, height=842)
        if font_path:
            page.insert_font(fontname="cjk", fontfile=str(font_path))
        page.insert_text((50, 45), "2 判断题（每题 2 分，共 20 分）", fontsize=11, fontname=font_name)
        page.insert_text((50, 65), "题号 1 2 3 4 5 6 7 8 9 10", fontsize=9, fontname=font_name)
        page.insert_text((50, 80), "答案 √ × √ × √ × √ × √ ×", fontsize=9, fontname=font_name)
        y = 110
        for local_no in range(1, 11):
            page.insert_text((50, y), f"第 {local_no} 题 自动切题判断题题干。", fontsize=10, fontname=font_name)
            y += 35
        page.insert_text((50, 500), "3 编程题（每题 25 分，共 50 分）", fontsize=11, fontname=font_name)
        page.insert_text((50, 530), "3.1 编程题 1", fontsize=11, fontname=font_name)
        page.insert_text((60, 555), "试题名称：交朋友", fontsize=10, fontname=font_name)
        page.insert_text((60, 580), "3.1.1 题目描述", fontsize=10, fontname=font_name)
        page.insert_text((60, 605), "Alice 想要找身高最接近的人。", fontsize=9, fontname=font_name)
        page.insert_text((60, 650), "3.1.7 参考程序", fontsize=10, fontname=font_name)
        page.insert_text((60, 665), "#include <iostream>", fontsize=8, fontname=font_name)
        page.insert_text((50, 700), "3．2 编程题 2", fontsize=11, fontname=font_name)
        page.insert_text((60, 725), "试题名称：数字替换", fontsize=10, fontname=font_name)
        page.insert_text((60, 750), "3.2.1 题目描述", fontsize=10, fontname=font_name)
        page.insert_text((60, 775), "把数字 4 替换成数字 8。", fontsize=9, fontname=font_name)
        page.insert_text((260, 820), "第 2 页 / 共 2 页", fontsize=8, fontname=font_name)
        pdf_bytes = document.tobytes()
        document.close()
        return pdf_bytes

    def build_demo_csp_j_round1_pdf_bytes(self) -> bytes:
        import fitz  # type: ignore

        document = fitz.open()
        page = document.new_page(width=595, height=842)
        font_candidates = [
            Path("/System/Library/Fonts/STHeiti Medium.ttc"),
            Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        ]
        font_path = next((path for path in font_candidates if path.exists()), None)
        font_name = "helv"
        if font_path:
            page.insert_font(fontname="cjk", fontfile=str(font_path))
            font_name = "cjk"
        page.insert_text((50, 45), "2023 CCF CSP-J 第一轮 C++ 语言试题", fontsize=10, fontname=font_name)
        page.insert_text((50, 70), "一、单项选择题（共15题，每题2分）", fontsize=11, fontname=font_name)
        y = 95
        for question_no in range(1, 16):
            page.insert_text((50, y), f"{question_no}. 单项选择题测试（ ）", fontsize=9, fontname=font_name)
            page.insert_text((70, y + 12), "A. A  B. B  C. C  D. D", fontsize=8, fontname=font_name)
            y += 35
        page.insert_text((50, 650), "二、阅读程序（判断题正确填√，错误填×）", fontsize=11, fontname=font_name)
        page.insert_text((50, 675), "(1)", fontsize=10, fontname=font_name)
        page.insert_text((70, 700), "01 #include <iostream>", fontsize=8, fontname=font_name)
        page.insert_text((70, 715), "02 int main(){ return 0; }", fontsize=8, fontname=font_name)
        page.insert_text((50, 740), "16. 该程序可以正常编译。（ ）", fontsize=9, fontname=font_name)
        page.insert_text((50, 765), "17. 该程序输出为（ ）", fontsize=9, fontname=font_name)
        page.insert_text((70, 780), "A. 0  B. 1  C. 2  D. 3", fontsize=8, fontname=font_name)
        page.insert_text((50, 815), "第1页，共1页", fontsize=8, fontname=font_name)
        pdf_bytes = document.tobytes()
        document.close()
        return pdf_bytes

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

    def build_docx_with_image_bytes(self, lines: list[str]) -> bytes:
        buffer = BytesIO()
        paragraphs = []
        for line in lines:
            if line == "[image]":
                paragraphs.append(
                    '<w:p><w:r><w:drawing>'
                    '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
                    '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    "<a:graphicData>"
                    '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                    "<pic:blipFill>"
                    '<a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="rId1"/>'
                    "</pic:blipFill>"
                    "</pic:pic>"
                    "</a:graphicData>"
                    "</a:graphic>"
                    "</wp:inline>"
                    "</w:drawing></w:r></w:p>"
                )
                continue
            paragraphs.append(f"<w:p><w:r><w:t>{html.escape(line)}</w:t></w:r></w:p>")
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            + "".join(paragraphs)
            + "</w:body></w:document>"
        )
        rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/image1.png"/>'
            "</Relationships>"
        )
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01"
            b"\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
            archive.writestr("word/_rels/document.xml.rels", rels_xml)
            archive.writestr("word/media/image1.png", png_bytes)
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
        self.assertContains(response, "添加新学生")
        self.assertNotContains(response, "C++ · 添加新学生")
        self.assertNotContains(response, "导入考题")
        self.assertNotContains(response, "当前页面上下文")
        self.assertNotContains(response, "教学提醒")
        self.assertNotContains(response, "#exam-panel")
        teacher_profile = Teacher.objects.get(user=self.teacher)
        self.assertTrue(teacher_profile.courses.filter(id=self.course.id).exists())

        course_response = self.client.get(reverse("teacher-course-students-detail", args=[self.course.slug]))
        self.assertEqual(course_response.status_code, 200)
        self.assertNotContains(course_response, "#exam-panel")

        exam_response = self.client.get(reverse("teacher-exams"))
        self.assertEqual(exam_response.status_code, 200)
        self.assertContains(exam_response, "试卷管理")
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
        self.assertNotContains(exam_response, "PDF 截图切题 Demo")
        self.assertNotContains(exam_response, reverse("teacher-exam-pdf-crop-demo-upload"))
        self.assertNotContains(exam_response, 'name="demo_pdf"', html=False)

    def test_teacher_pdf_crop_demo_renders_pdf_and_saves_relative_crop(self) -> None:
        self.sign_in(self.teacher)
        pdf_bytes = self.build_demo_text_layer_pdf_bytes()

        upload_response = self.client.post(
            reverse("teacher-exam-pdf-crop-demo-upload"),
            {
                "demo_subject": "cpp",
                "demo_exam_type": "gesp1",
                "demo_year": "2024",
                "demo_month": "5",
                "demo_scale": "2",
                "demo_pdf": SimpleUploadedFile("demo.pdf", pdf_bytes, content_type="application/pdf"),
            },
        )

        self.assertEqual(upload_response.status_code, 302)
        self.assertIn("/teacher/exams/pdf-crop-demo/", upload_response["Location"])
        detail_response = self.client.get(upload_response["Location"])
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "PDF 截图切题 Demo")
        self.assertContains(detail_response, "exam_assets/demo_pages/")
        self.assertNotContains(detail_response, "/Users/")
        self.assertNotContains(detail_response, "/opt/")

        session_id = upload_response["Location"].rstrip("/").split("/")[-1]
        with default_storage.open(f"exam_assets/demo_sessions/{session_id}.json", "r") as state_file:
            state = json.load(state_file)
        self.assertEqual(state["subject"], "cpp")
        self.assertEqual(state["exam_type"], "gesp1")
        self.assertEqual(state["month"], "05")
        self.assertEqual(len(state["pages"]), 2)
        self.assertTrue(state["pages"][0]["image_path"].startswith("exam_assets/demo_pages/"))
        self.assertEqual(state["auto_summary"]["anchor_count"], 27)
        self.assertEqual(state["auto_summary"]["answer_count"], 25)
        self.assertFalse(state["auto_summary"]["fallback_used"])
        self.assertGreaterEqual(len(state["crops"]), 27)
        crop = state["crops"][0]
        self.assertEqual(crop["answer"], "A")
        self.assertEqual(crop["question_type"], "single_choice")
        self.assertEqual(
            crop["image_path"],
            "exam_assets/demo_questions/cpp/gesp1/2024_05/cpp_gesp1_2024_05_s01_q001_p01.png",
        )
        self.assertTrue(default_storage.exists(crop["image_path"]))
        programming_crops = [item for item in state["crops"] if item["question_type"] == "programming"]
        self.assertEqual([item["question_no"] for item in programming_crops], [26, 27])
        self.assertTrue(programming_crops[0]["image_path"].endswith("cpp_gesp1_2024_05_s03_q026_p01.png"))
        self.assertTrue(programming_crops[1]["image_path"].endswith("cpp_gesp1_2024_05_s03_q027_p01.png"))
        self.assertLess(programming_crops[0]["crop_box"][3], 1300)
        self.assertContains(detail_response, "pdf-demo-preview-footer")
        self.assertContains(detail_response, "保存答案修改")
        self.assertContains(detail_response, "确认填入答案")
        self.assertContains(detail_response, 'name="quick_answer_1"', html=False)
        self.assertContains(detail_response, 'name="quick_answer_25"', html=False)
        self.assertContains(detail_response, "调整截图")
        self.assertContains(detail_response, "确认入库并查看已入库试卷")

        quick_answer_response = self.client.post(
            reverse("teacher-exam-pdf-crop-demo", args=[session_id]),
            {
                "form_action": "bulk_update_pdf_crop_demo_answers",
                "quick_answer_1": "d",
                "quick_answer_15": "a",
                "quick_answer_16": "对",
                "quick_answer_25": "错",
            },
        )
        self.assertEqual(quick_answer_response.status_code, 200)
        self.assertContains(quick_answer_response, "已快速填入 4 道客观题答案。")
        with default_storage.open(f"exam_assets/demo_sessions/{session_id}.json", "r") as state_file:
            quick_answer_state = json.load(state_file)
        first_question_records = [item for item in quick_answer_state["crops"] if item["question_no"] == 1]
        fifteenth_question_records = [item for item in quick_answer_state["crops"] if item["question_no"] == 15]
        first_judgment_records = [item for item in quick_answer_state["crops"] if item["question_no"] == 16]
        last_judgment_records = [item for item in quick_answer_state["crops"] if item["question_no"] == 25]
        self.assertTrue(first_question_records)
        self.assertTrue(fifteenth_question_records)
        self.assertTrue(first_judgment_records)
        self.assertTrue(last_judgment_records)
        self.assertTrue(all(item["answer"] == "D" for item in first_question_records))
        self.assertTrue(all(item["answer"] == "A" for item in fifteenth_question_records))
        self.assertTrue(all(item["answer"] == "√" for item in first_judgment_records))
        self.assertTrue(all(item["answer"] == "×" for item in last_judgment_records))

        state = quick_answer_state
        initial_crop_count = len(state["crops"])
        add_part_response = self.client.post(
            reverse("teacher-exam-pdf-crop-demo", args=[session_id]),
            {
                "form_action": "add_pdf_crop_demo_part",
                "page_no": "1",
                "section_no": "1",
                "question_no": "1",
                "part_no": "2",
                "question_type": "single_choice",
                "answer": "A",
                "score": "2",
                "x1": "10",
                "y1": "10",
                "x2": "120",
                "y2": "120",
            },
        )
        self.assertEqual(add_part_response.status_code, 200)
        with default_storage.open(f"exam_assets/demo_sessions/{session_id}.json", "r") as state_file:
            updated_state = json.load(state_file)
        self.assertEqual(len(updated_state["crops"]), initial_crop_count + 1)
        replace_part_response = self.client.post(
            reverse("teacher-exam-pdf-crop-demo", args=[session_id]),
            {
                "form_action": "add_pdf_crop_demo_part",
                "page_no": "1",
                "section_no": "1",
                "question_no": "1",
                "part_no": "2",
                "question_type": "single_choice",
                "answer": "B",
                "score": "3",
                "x1": "20",
                "y1": "20",
                "x2": "140",
                "y2": "140",
            },
        )
        self.assertEqual(replace_part_response.status_code, 200)
        self.assertContains(replace_part_response, "已替换第 1 大题第 1 题第 2 张截图。")
        with default_storage.open(f"exam_assets/demo_sessions/{session_id}.json", "r") as state_file:
            replaced_state = json.load(state_file)
        self.assertEqual(len(replaced_state["crops"]), initial_crop_count + 1)
        replacement = next(item for item in replaced_state["crops"] if item["question_no"] == 1 and item["part_no"] == 2)
        self.assertEqual(replacement["answer"], "B")
        self.assertEqual(replacement["score"], "3")
        self.assertEqual(replacement["crop_box"], [20, 20, 140, 140])

    def test_pdf_crop_demo_parses_gesp_answer_blocks_before_first_question(self) -> None:
        from entry.views import parse_pdf_demo_answers

        answers = parse_pdf_demo_answers(
            {
                "single_choice": [
                    {"text": "1 单选题（每题2分，共30分）"},
                    {"text": "| 题号 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |"},
                    {"text": "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"},
                    {"text": "| 答案 | B | B | B | C | B | C | D | B | C | A | D | A | C | C | C |"},
                    {"text": "第 1 题 下列 C++ 代码的输出是？"},
                ],
                "judgment": [
                    {"text": "2 判断题（每题2分，共20分）"},
                    {"text": "题号 1 2 3 4 5 6 7 8 9 10"},
                    {"text": "答案"},
                    {"text": "√ × × √ √ × √ × √ ×"},
                    {"text": "第 1 题 C++ 是编程语言。"},
                ],
            }
        )

        self.assertEqual([answers[index] for index in range(1, 16)], ["B", "B", "B", "C", "B", "C", "D", "B", "C", "A", "D", "A", "C", "C", "C"])
        self.assertEqual([answers[index] for index in range(16, 26)], ["√", "×", "×", "√", "√", "×", "√", "×", "√", "×"])

        split_cell_answers = parse_pdf_demo_answers(
            {
                "single_choice": [
                    {"text": "1 单选题（每题2分，共30分）"},
                    {"text": "题号"},
                    *[{"text": str(index)} for index in range(1, 16)],
                    {"text": "答案"},
                    *[{"text": token} for token in ["Ｂ", "Ｂ", "Ｂ", "Ｃ", "Ｂ", "Ｃ", "Ｄ", "Ｂ", "Ｃ", "Ａ", "Ｄ", "Ａ", "Ｃ", "Ｃ", "Ｃ"]],
                    {"text": "第 1 题 C++ 表达式的值是？"},
                ],
                "judgment": [
                    {"text": "2 判断题（每题2分，共20分）"},
                    {"text": "题号"},
                    *[{"text": str(index)} for index in range(1, 11)],
                    {"text": "答案"},
                    *[{"text": token} for token in ["✓", "X", "✓", "✕", "√", "×", "✓", "X", "√", "×"]],
                    {"text": "第 1 题 C++ 是编程语言。"},
                ],
            }
        )
        self.assertEqual([split_cell_answers[index] for index in range(1, 16)], ["B", "B", "B", "C", "B", "C", "D", "B", "C", "A", "D", "A", "C", "C", "C"])
        self.assertEqual([split_cell_answers[index] for index in range(16, 26)], ["√", "×", "√", "×", "√", "×", "√", "×", "√", "×"])

        noisy_judgment_answers = parse_pdf_demo_answers(
            {
                "judgment": [
                    {"text": "2 判断题"},
                    {"text": "题号 1 2 3 4 5 6 7 8 9 10"},
                    {"text": "答案 √ × √ × √ × √ × √ ×"},
                    {"text": "CCF GESP C++ 编程能力等级认证"},
                    {"text": "第 1 题 执行C++代码 cout<<(5&2)<<endl; 后将输出 1。"},
                ]
            }
        )
        self.assertEqual([noisy_judgment_answers[index] for index in range(16, 26)], ["√", "×", "√", "×", "√", "×", "√", "×", "√", "×"])

        missing_judgment_answers = parse_pdf_demo_answers(
            {
                "judgment": [
                    {"text": "2 判断题"},
                    {"text": "答案"},
                    {"text": "CCF GESP C++ 编程能力等级认证"},
                    {"text": "第 1 题 执行C++代码 cout<<(5&2)<<endl; 后将输出 1。"},
                ]
            }
        )
        self.assertNotIn(16, missing_judgment_answers)

        geometry_answers = parse_pdf_demo_answers(
            {
                "single_choice": [
                    {"text": "1 单选题（每题2分，共30分）", "x0": 10, "y0": 10, "y1": 20},
                    {"text": "题号", "x0": 10, "y0": 30, "y1": 40},
                    *[
                        {"text": str(index), "x0": 40 + index * 12, "y0": 30, "y1": 40}
                        for index in range(1, 16)
                    ],
                    {"text": "答案", "x0": 10, "y0": 45, "y1": 55},
                    *[
                        {"text": token, "x0": 40 + index * 12, "y0": 45, "y1": 55}
                        for index, token in enumerate(["B", "B", "B", "C", "B", "C", "D", "B", "C", "A", "D", "A", "C", "C", "C"], start=1)
                    ],
                    {"text": "CCF GESP CCF 编程能力等级认证", "x0": 10, "y0": 70, "y1": 80},
                    {"text": "第 1 题 C++ 表达式的值是？", "x0": 10, "y0": 100, "y1": 110},
                ],
                "judgment": [],
            }
        )
        self.assertEqual([geometry_answers[index] for index in range(1, 16)], ["B", "B", "B", "C", "B", "C", "D", "B", "C", "A", "D", "A", "C", "C", "C"])

    def test_pdf_crop_demo_detects_number_dot_question_anchors(self) -> None:
        from entry.views import detect_pdf_demo_anchors

        anchors, boundaries, _answers = detect_pdf_demo_anchors(
            [
                {"page_no": 1, "text": "1 单选题（每题2分，共30分）", "x0": 50, "y0": 40, "x1": 300, "y1": 60},
                {"page_no": 1, "text": "题号 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15", "x0": 50, "y0": 70, "x1": 400, "y1": 85},
                {"page_no": 1, "text": "答案 D D A D C A B B C D C A B D B", "x0": 50, "y0": 90, "x1": 500, "y1": 105},
                {"page_no": 1, "text": "1. 高级语言编写的程序需要经过以下（ ）操作。", "x0": 70, "y0": 150, "x1": 500, "y1": 170},
                {"page_no": 1, "text": "2．下列选项正确的是（ ）。", "x0": 70, "y0": 310, "x1": 500, "y1": 330},
                {"page_no": 2, "text": "2 判断题（每题2分，共20分）", "x0": 50, "y0": 40, "x1": 300, "y1": 60},
                {"page_no": 2, "text": "题号 1 2 3 4 5 6 7 8 9 10", "x0": 50, "y0": 70, "x1": 400, "y1": 85},
                {"page_no": 2, "text": "答案 √ × √ × √ × √ × √ ×", "x0": 50, "y0": 90, "x1": 500, "y1": 105},
                {"page_no": 2, "text": "1、执行 C++ 代码后将输出 1。（ ）", "x0": 70, "y0": 150, "x1": 500, "y1": 170},
                {"page_no": 2, "text": "2. C++ 程序执行后输出正确。（ ）", "x0": 70, "y0": 250, "x1": 500, "y1": 270},
            ]
        )

        self.assertEqual([int(anchor["question_no"]) for anchor in anchors], [1, 2, 16, 17])
        self.assertIn("single_choice", [boundary["boundary_type"] for boundary in boundaries])
        self.assertIn("judgment", [boundary["boundary_type"] for boundary in boundaries])

    def test_pdf_crop_demo_infers_visual_judgment_answers_from_red_marks(self) -> None:
        from PIL import Image, ImageDraw

        from entry.views import infer_pdf_demo_visual_judgment_answers

        image = Image.new("RGB", (1000, 1400), "white")
        draw = ImageDraw.Draw(image)
        expected_answers = ["√", "√", "√", "√", "×", "×", "√", "√", "×", "√"]
        for index, answer in enumerate(expected_answers):
            x = 210 + index * 55
            if answer == "√":
                draw.line([(x, 430), (x + 8, 446), (x + 24, 410)], fill=(238, 40, 40), width=4)
            else:
                draw.line([(x, 418), (x + 18, 436)], fill=(238, 40, 40), width=4)
                draw.line([(x + 18, 418), (x, 436)], fill=(238, 40, 40), width=4)

        image_buffer = BytesIO()
        image.save(image_buffer, format="PNG")
        image_path = default_storage.save("exam_assets/demo_pages/visual_judgment/page_001.png", ContentFile(image_buffer.getvalue()))
        answers = infer_pdf_demo_visual_judgment_answers(
            pages=[{"page_no": 1, "image_path": image_path, "width": 1000, "height": 1400}],
            page_metrics={
                1: {
                    "width_pt": 500,
                    "height_pt": 700,
                    "width_px": 1000,
                    "height_px": 1400,
                    "top_pt": 20,
                    "bottom_pt": 680,
                }
            },
            lines=[
                {"page_no": 1, "text": "2 判断题（每题2分，共20分）", "x0": 50, "y0": 180, "x1": 300, "y1": 200},
                {"page_no": 1, "text": "第 1 题 执行C++代码后将输出 1。", "x0": 50, "y0": 245, "x1": 450, "y1": 260},
            ],
        )

        self.assertEqual([answers[index] for index in range(16, 26)], expected_answers)

    def test_pdf_crop_demo_does_not_create_fake_crops_without_question_anchors(self) -> None:
        from entry.views import build_pdf_demo_auto_crops

        class NoQuestionAnchorDocument:
            page_count = 0

        state = {
            "subject": "cpp",
            "exam_type": "gesp3",
            "year": "2023",
            "month": "06",
            "pages": [{"page_no": 1, "image_path": "exam_assets/demo_pages/no_anchor/page_001.png", "width": 1000, "height": 1400}],
        }
        crops, summary = build_pdf_demo_auto_crops(
            state=state,
            document=NoQuestionAnchorDocument(),
            pages=state["pages"],
        )

        self.assertEqual(crops, [])
        self.assertTrue(summary["fallback_used"])
        self.assertTrue(summary["requires_manual_crop"])
        self.assertEqual(summary["crop_count"], 0)

    def test_pdf_crop_demo_section_fallback_stops_programming_before_reference_code(self) -> None:
        from entry.views import build_pdf_demo_section_fallback_regions

        regions = build_pdf_demo_section_fallback_regions(
            boundaries=[
                {"boundary_type": "single_choice", "page_no": 1, "x0": 50, "y0": 40, "y1": 60},
                {"boundary_type": "judgment", "page_no": 3, "x0": 50, "y0": 100, "y1": 120},
                {"boundary_type": "programming", "page_no": 5, "x0": 50, "y0": 100, "y1": 120},
                {"boundary_type": "programming_reference", "page_no": 6, "x0": 50, "y0": 300, "y1": 320},
            ],
            page_metrics={
                page_no: {
                    "width_pt": 500,
                    "height_pt": 700,
                    "width_px": 1000,
                    "height_px": 1400,
                    "top_pt": 20,
                    "bottom_pt": 680,
                }
                for page_no in range(1, 7)
            },
            answers={**{question_no: "A" for question_no in range(1, 16)}, **{question_no: "√" for question_no in range(16, 26)}},
        )

        self.assertIn(26, regions)
        self.assertIn(27, regions)
        reference_y_px = 300 * 2
        programming_regions_on_reference_page = [
            region for question_no in (26, 27) for region in regions[question_no] if region["page_no"] == 6
        ]
        self.assertTrue(programming_regions_on_reference_page)
        self.assertTrue(all(region["bbox"][3] <= reference_y_px - 12 for region in programming_regions_on_reference_page))

    def test_teacher_pdf_crop_demo_confirm_creates_publishable_snapshot_with_images(self) -> None:
        self.sign_in(self.teacher)
        pdf_bytes = self.build_demo_text_layer_pdf_bytes()

        upload_response = self.client.post(
            reverse("teacher-exam-pdf-crop-demo-upload"),
            {
                "demo_scale": "2",
                "demo_pdf": SimpleUploadedFile("2024年5月C++1级试题.pdf", pdf_bytes, content_type="application/pdf"),
            },
        )

        self.assertEqual(upload_response.status_code, 302)
        session_id = upload_response["Location"].rstrip("/").split("/")[-1]
        update_response = self.client.post(
            reverse("teacher-exam-pdf-crop-demo", args=[session_id]),
            {
                "form_action": "update_pdf_crop_demo_answers",
                "question_type_1": "single_choice",
                "answer_1": "D",
                "score_1": "3",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        confirm_response = self.client.post(
            reverse("teacher-exam-pdf-crop-demo", args=[session_id]),
            {"form_action": "confirm_pdf_crop_demo_to_bank"},
        )

        self.assertEqual(confirm_response.status_code, 302)
        self.assertIn("op=pdf_crop_demo_confirmed", confirm_response["Location"])
        bank_paper = ExamQuestionBankPaper.objects.get(source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR)
        self.assertEqual(bank_paper.level, "GESP1")
        self.assertEqual(bank_paper.year, 2024)
        self.assertEqual(bank_paper.month, 5)
        self.assertEqual(bank_paper.questions.count(), 27)
        self.assertEqual(bank_paper.questions.get(question_no=1).answer_json["correct_answer"], "D")
        self.assertEqual(bank_paper.questions.get(question_no=1).full_json["score"], "3")
        programming_question = bank_paper.questions.get(question_no=26)
        self.assertEqual(programming_question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING)
        self.assertEqual(programming_question.assets.filter(asset_role="content").count(), 1)
        self.assertTrue(programming_question.assets.get().relative_path.startswith("exam_assets/demo_questions/"))
        bank_paper.questions.filter(question_no=2).update(answer_json={})

        start_at = timezone.now() + timedelta(minutes=5)
        end_at = start_at + timedelta(minutes=45)
        publish_response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "create_exam_from_bank_paper",
                "bank_paper_id": str(bank_paper.id),
                "exam_schedule_mode": "scheduled",
                "exam_schedule_start_at": self.format_datetime_local(start_at),
                "exam_schedule_end_at": self.format_datetime_local(end_at),
            },
        )
        self.assertEqual(publish_response.status_code, 302)
        exam_paper = ExamPaper.objects.get(title=bank_paper.title)
        self.assertEqual(exam_paper.questions.count(), 27)
        first_question = exam_paper.questions.get(question_no=1)
        self.assertEqual(first_question.correct_answer, "D")
        self.assertEqual(first_question.score, 3)
        self.assertEqual(exam_paper.questions.get(question_no=2).correct_answer, "")
        programming_exam_question = exam_paper.questions.get(question_no=26)
        self.assertEqual(programming_exam_question.score, 25)
        image_paths = programming_exam_question.source_snapshot_json.get("image_paths")
        self.assertIsInstance(image_paths, list)
        self.assertEqual(image_paths, [programming_exam_question.image_path])

    def test_teacher_new_exam_paper_page_defaults_to_teacher_subject_course(self) -> None:
        scratch_teacher = PortalUser.objects.create(
            username="scratch_exam_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="Scratch 考试老师",
            phone="13810001001",
        )
        scratch_course = Course.objects.create(slug="scratch", title="Scratch", summary="图形化编程")
        scratch_category = CourseCategory.objects.create(
            course=scratch_course,
            slug="grade-exam",
            title="图形化等级考试",
            summary="Scratch 等级考试",
            sort_order=1,
            is_active=True,
        )
        CourseLevel.objects.create(
            category=scratch_category,
            code="S2",
            title="图形化编程二级",
            summary="图形化编程二级",
            sort_order=2,
            is_active=True,
        )
        teacher_profile = Teacher.objects.create(
            user=scratch_teacher,
            display_name=scratch_teacher.full_name,
            phone=scratch_teacher.phone,
            subject="Scratch",
        )
        teacher_profile.courses.add(scratch_course)
        self.sign_in(scratch_teacher)

        response = self.client.get(reverse("teacher-exam-paper-new"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_course_id"], scratch_course.id)
        self.assertContains(
            response,
            f'<option value="{scratch_course.id}" selected>Scratch</option>',
            html=False,
        )

    def test_teacher_new_exam_paper_page_creates_import_job(self) -> None:
        self.sign_in(self.teacher)
        response = self.client.get(reverse("teacher-exam-paper-new"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "新增试卷")
        self.assertContains(response, 'name="source_pdf"', html=False)
        self.assertContains(response, 'name="use_qwen_ocr"', html=False)
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
                "use_qwen_ocr": "on",
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
                "use_qwen_ocr": "on",
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

    def test_docx_import_extracts_embedded_images_and_scratch_programming_metadata(self) -> None:
        docx_bytes = self.build_docx_with_image_bytes(
            [
                "一、单选题",
                "1. 下列哪个区域显示 Scratch 角色运行效果？",
                "[image]",
                "A. 舞台区",
                "B. 代码区",
                "C. 角色列表",
                "D. 背景库",
                "标准答案：A",
                "试题解析：舞台区用于显示角色运行效果。",
                "二、操作题",
                "1. 操作题 请制作一个角色说你好并移动 10 步。",
            ]
        )
        scratch_course = Course.objects.create(slug="scratch-docx-import", title="Scratch", summary="Scratch DOCX 导入")
        import_job = ExamQuestionBankImportJob.objects.create(
            teacher=self.teacher,
            course=scratch_course,
            level_code="S2",
            title="Scratch DOCX 导入测试",
            year=2026,
            month=9,
            source_pdf_id="scratch_docx_import_test",
            source_filename="2026年9月图形化二级.docx",
            source_sha256=hashlib.sha256(docx_bytes).hexdigest(),
            status=ExamQuestionBankImportJob.STATUS_UPLOADED,
        )
        import_job.source_pdf.save("scratch_docx_import_test.docx", ContentFile(docx_bytes), save=True)
        workspace_dir = Path(self._media_root) / "exam_import_test_workspace"
        raw_ocr_dir = workspace_dir / "raw"
        response_dir = workspace_dir / "responses"
        raw_ocr_dir.mkdir(parents=True, exist_ok=True)
        response_dir.mkdir(parents=True, exist_ok=True)

        process_text_exam_import_job(
            import_job,
            workspace_dir=workspace_dir,
            raw_ocr_dir=raw_ocr_dir,
            response_dir=response_dir,
            source_type=SOURCE_TYPE_DOCX,
        )
        import_job.refresh_from_db()
        self.assertEqual(import_job.status, ExamQuestionBankImportJob.STATUS_OCR_DONE)
        raw_markdown_path = self._media_root_path(import_job.raw_ocr_json[0]["markdown_relative_path"])
        raw_markdown = raw_markdown_path.read_text(encoding="utf-8")
        self.assertIn("![DOCX 图片", raw_markdown)

        paper, stats = confirm_exam_question_bank_import_job(import_job)

        self.assertEqual(stats["questions_created"], 2)
        image_asset = ExamQuestionBankAsset.objects.get(question__paper=paper, relative_path__contains="docx_media/")
        self.assertEqual(image_asset.asset_type, "image/png")
        choice_question = paper.questions.get(question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE)
        self.assertEqual(choice_question.answer_json["correct_answer"], "A")
        self.assertEqual(choice_question.answer_json["source"], "docx_embedded_answer")
        self.assertIn("舞台区用于显示角色运行效果", choice_question.analysis_md)
        programming_question = paper.questions.get(question_type=ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING)
        self.assertEqual(programming_question.programming_json["submission_mode"], "scratch_project")
        self.assertEqual(programming_question.programming_json["grading_mode"], "manual")

    def test_scratch_docx_split_handles_solo_numbers_and_embedded_answer_analysis(self) -> None:
        markdown = "\n".join(
            [
                "1.",
                "小猫初始位置如下图所示，下面哪个选项能让小猫吃到老鼠？（ ）",
                "![DOCX 图片 1](/media/exam/docx_media/docx_image_001.png)",
                "A.",
                "![DOCX 图片 2](/media/exam/docx_media/docx_image_002.png)",
                "B.",
                "![DOCX 图片 3](/media/exam/docx_media/docx_image_003.png)",
                "C.",
                "![DOCX 图片 4](/media/exam/docx_media/docx_image_004.png)",
                "D.",
                "![DOCX 图片 5](/media/exam/docx_media/docx_image_005.png)",
                "标准答案：B",
                "试题解析：Cat 2要朝着Mouse1的方向走，所以答案是B。",
                "二、判断题(共1题，共2分)",
                "26.",
                "默认角色小猫，运行程序后小猫会消失。（ ）",
                "正确",
                "错误",
                "标准答案：错误",
                "试题解析：运行后角色仍会显示，故答案为错误。",
                "三、编程题(共2题，共30分)",
                "36.",
                "魔法扫帚",
                "1.准备工作",
                "（1）添加背景：Night City With Street；",
                "2.功能实现",
                "（1）扫帚不停左右移动，碰到边缘就反弹；",
                "参考程序：",
                "Broom角色",
                "![DOCX 图片 6](/media/exam/docx_media/docx_image_006.png)",
                "评分标准：",
                "（1）能够添加角色Broom；（2分）",
                "展示地址：点击浏览",
                "37.",
                "绘制乒乓球拍",
                "1.准备工作",
                "（1）默认小猫角色；",
                "评分标准：",
                "（1）设置画笔颜色为黑色；（1分）",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown, scratch_docx_mode=True)

        self.assertEqual([question["question_no"] for question in parsed_questions], [1, 26, 36, 37])
        self.assertEqual(parsed_questions[0]["answer_json"]["correct_answer"], "B")
        self.assertIn("Cat 2要朝着Mouse1", parsed_questions[0]["analysis_md"])
        self.assertEqual(parsed_questions[1]["question_type"], ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE)
        self.assertEqual(parsed_questions[1]["answer_json"]["answer"], "×")
        self.assertEqual(parsed_questions[2]["question_type"], ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING)
        self.assertIn("1.准备工作", parsed_questions[2]["stem_md"])
        self.assertIn("参考程序", parsed_questions[2]["analysis_md"])
        self.assertIn("能够添加角色Broom", parsed_questions[2]["analysis_md"])
        self.assertEqual(parsed_questions[0]["score"], "")

    def test_scratch_docx_rules_do_not_apply_without_explicit_mode(self) -> None:
        markdown = "\n".join(
            [
                "1.",
                "小猫初始位置如下图所示，下面哪个选项能让小猫吃到老鼠？（ ）",
                "A. 向左走",
                "B. 向右走",
                "标准答案：B",
                "试题解析：朝目标方向移动。",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown)

        self.assertEqual(parsed_questions, [])

    def test_exam_markdown_display_renders_docx_images(self) -> None:
        html_text = str(
            render_exam_markdown_for_display(
                "题目图片\n![DOCX 图片 1](/media/exam_paper_import_workspace/demo/docx_media/docx_image_001.png)"
            )
        )

        self.assertIn('<img class="exam-markdown-body__image"', html_text)
        self.assertIn('src="/media/exam_paper_import_workspace/demo/docx_media/docx_image_001.png"', html_text)
        self.assertIn('alt="DOCX 图片 1"', html_text)

    def test_markdown_embedded_images_are_not_rendered_twice_as_extra_paths(self) -> None:
        markdown = "题干\n![DOCX 图片 1](/media/exam_paper_import_workspace/demo/docx_media/docx_image_001.png)"

        remaining_paths = exclude_markdown_embedded_image_paths(
            [
                "exam_paper_import_workspace/demo/docx_media/docx_image_001.png",
                "exam_paper_import_workspace/demo/docx_media/docx_image_002.png",
            ],
            markdown,
        )

        self.assertEqual(remaining_paths, ["exam_paper_import_workspace/demo/docx_media/docx_image_002.png"])

    def test_teacher_new_exam_paper_page_defaults_pdf_to_crop_demo(self) -> None:
        self.sign_in(self.teacher)
        pdf_bytes = self.build_demo_text_layer_pdf_bytes()
        upload_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "course_id": str(self.cpp_course.id),
                "level_code": "GESP1",
                "title": "2024年5月C++一级截图试卷",
                "source_pdf": SimpleUploadedFile(
                    "2024年5月C++一级试题.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
            },
        )

        self.assertEqual(upload_response.status_code, 302)
        self.assertIn("/teacher/exams/pdf-crop-demo/", upload_response["Location"])
        self.assertEqual(ExamQuestionBankImportJob.objects.count(), 0)
        session_id = upload_response["Location"].rstrip("/").split("/")[-1]
        with default_storage.open(f"exam_assets/demo_sessions/{session_id}.json", "r") as state_file:
            state = json.load(state_file)
        self.assertEqual(state["subject"], "cpp")
        self.assertEqual(state["exam_type"], "gesp1")
        self.assertEqual(state["year"], "2024")
        self.assertEqual(state["month"], "05")
        self.assertGreaterEqual(len(state["crops"]), 27)

    def test_teacher_new_exam_paper_page_uses_csp_j_round1_grouped_material_crop_mode(self) -> None:
        self.sign_in(self.teacher)
        pdf_bytes = self.build_demo_csp_j_round1_pdf_bytes()
        upload_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "course_id": str(self.cpp_course.id),
                "level_code": "CSP-J",
                "title": "2023年CSP-J初赛真题",
                "source_pdf": SimpleUploadedFile(
                    "2023年CSP-J初赛真题.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
            },
        )

        self.assertEqual(upload_response.status_code, 302)
        self.assertIn("/teacher/exams/pdf-crop-demo/", upload_response["Location"])
        session_id = upload_response["Location"].rstrip("/").split("/")[-1]
        with default_storage.open(f"exam_assets/demo_sessions/{session_id}.json", "r") as state_file:
            state = json.load(state_file)
        self.assertEqual(state["crop_mode"], "csp_j_round1")
        self.assertEqual(state["exam_type"], "csp_j")
        grouped_records = [record for record in state["crops"] if record["question_no"] == 16]
        self.assertTrue(grouped_records)
        self.assertEqual(grouped_records[0]["display_mode"], "grouped_material")
        self.assertTrue(grouped_records[0]["material_image_paths"])
        self.assertTrue(grouped_records[0]["material_image_paths"][0].endswith("_g001_material_p01.png"))

        confirm_response = self.client.post(
            reverse("teacher-exam-pdf-crop-demo", args=[session_id]),
            {"form_action": "confirm_pdf_crop_demo_to_bank"},
        )
        self.assertEqual(confirm_response.status_code, 302)
        bank_paper = ExamQuestionBankPaper.objects.get(import_batch_uid=session_id)
        bank_question = bank_paper.questions.get(question_no=16)
        full_json = bank_question.full_json
        self.assertEqual(full_json["display_mode"], "grouped_material")
        self.assertTrue(full_json["material_image_paths"])
        self.assertTrue(full_json["question_image_paths"])
        content_assets = list(bank_question.assets.filter(asset_role="content").order_by("id"))
        self.assertGreaterEqual(len(content_assets), 2)
        self.assertEqual(content_assets[0].asset_type, "material_crop")

    def test_teacher_new_exam_paper_page_detects_csp_j1_filename_as_round1(self) -> None:
        self.sign_in(self.teacher)
        pdf_bytes = self.build_demo_csp_j_round1_pdf_bytes()
        upload_response = self.client.post(
            reverse("teacher-exam-paper-new"),
            {
                "course_id": str(self.cpp_course.id),
                "level_code": "CSP-J",
                "title": "CSP-J 2022 题目",
                "source_pdf": SimpleUploadedFile(
                    "CSP-J1-2022-题目.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
            },
        )

        self.assertEqual(upload_response.status_code, 302)
        self.assertIn("/teacher/exams/pdf-crop-demo/", upload_response["Location"])
        session_id = upload_response["Location"].rstrip("/").split("/")[-1]
        with default_storage.open(f"exam_assets/demo_sessions/{session_id}.json", "r") as state_file:
            state = json.load(state_file)
        self.assertEqual(state["crop_mode"], "csp_j_round1")
        self.assertTrue([record for record in state["crops"] if record["question_no"] == 16 and record["material_image_paths"]])

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

    def test_graphical_docx_bank_paper_is_inferred_as_scratch_subject(self) -> None:
        scratch_teacher = PortalUser.objects.create(
            username="scratch_bank_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="Scratch 题库老师",
            phone="13810002001",
        )
        scratch_course = Course.objects.create(slug="scratch-bank", title="Scratch", summary="Scratch")
        teacher_profile = Teacher.objects.create(
            user=scratch_teacher,
            display_name=scratch_teacher.full_name,
            phone=scratch_teacher.phone,
            subject="Scratch",
        )
        teacher_profile.courses.add(scratch_course)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="S1",
            year=2025,
            month=9,
            source_pdf_id="2025_9_202509",
            source_file="202509图形化一级.docx",
            title="202509图形化一级",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )

        self.assertEqual(infer_exam_bank_paper_subject(bank_paper), "Scratch")
        self.assertTrue(teacher_can_operate_exam_subject(scratch_teacher, "Scratch"))
        self.assertTrue(teacher_can_operate_exam_subject(scratch_teacher, infer_exam_bank_paper_subject(bank_paper)))

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
            analysis_md="原始解析\n\n```cpp\ncout << 1;\n```",
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
        self.assertContains(exam_page_response, "知识点识别状态")
        self.assertNotContains(exam_page_response, ">预览<", html=False)
        self.assertContains(exam_page_response, reverse("teacher-exam-bank-paper-edit", args=[paper.id]))
        self.assertContains(exam_page_response, "知识点识别")
        available_row = next(row for row in exam_page_response.context["available_paper_rows"] if row["id"] == paper.id)
        self.assertEqual(available_row["knowledge_status_text"], "未识别")

        preview_response = self.client.get(reverse("teacher-exam-bank-paper-preview", args=[paper.id]))
        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "试卷内容预览")
        self.assertContains(preview_response, "原始题干 N")
        self.assertContains(preview_response, "原 A")
        self.assertContains(preview_response, "cout &lt;&lt; 1;")
        self.assertContains(preview_response, "重新解析")

        edit_response = self.client.get(reverse("teacher-exam-bank-paper-edit", args=[paper.id]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, "手动修改试卷内容")
        self.assertContains(edit_response, 'name="question_ids"', html=False)
        self.assertContains(edit_response, 'data-add-new-question', html=False)
        self.assertContains(edit_response, f'name="question_{question.id}_option_d"', html=False)
        self.assertContains(edit_response, f'<textarea name="question_{question.id}_option_a"', html=False)
        self.assertContains(edit_response, "int main()")
        self.assertContains(edit_response, "```cpp")
        self.assertContains(edit_response, "重新解析")
        self.assertContains(edit_response, f'name="question_{question.id}_knowledge_level_1"', html=False)
        self.assertContains(edit_response, f'id="exam-bank-question-analysis-{question.id}"', html=False)

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
                f"question_{question.id}_option_a": "```cpp\ncout << 1;\n```",
                f"question_{question.id}_option_b": "修改 B",
                f"question_{question.id}_option_c": "新增 C",
                f"question_{question.id}_option_d": "",
                f"question_{question.id}_analysis": "修改解析",
                f"question_{question.id}_knowledge_level_1": "程序设计语言基础",
                f"question_{question.id}_knowledge_level_2": "C++ 程序结构",
                f"question_{question.id}_knowledge_level_3": "main 函数",
                "new_question_1_type": ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                "new_question_1_stem_md": "新增题干：下列说法正确的是？",
                "new_question_1_answer": "C",
                "new_question_1_option_a": "新增 A",
                "new_question_1_option_b": "新增 B",
                "new_question_1_option_c": "新增 C",
                "new_question_1_option_d": "",
                "new_question_1_analysis": "",
                "new_question_1_knowledge_level_1": "运算符与表达式",
                "new_question_1_knowledge_level_2": "逻辑运算",
                "new_question_1_knowledge_level_3": "",
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
        self.assertEqual(question.full_json["knowledge_level_1"], "程序设计语言基础")
        self.assertEqual(question.full_json["knowledge_level_2"], "C++ 程序结构")
        self.assertEqual(question.full_json["knowledge_level_3"], "main 函数")
        self.assertEqual(
            list(question.options.order_by("sort_order").values_list("option_key", "option_text_md")),
            [("A", "```cpp\ncout << 1;\n```"), ("B", "修改 B"), ("C", "新增 C")],
        )
        self.assertEqual(added_question.question_type, ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE)
        self.assertEqual(added_question.answer_json["correct_answer"], "C")
        self.assertIn("新增题干", added_question.stem_md)
        self.assertIn("正确答案：C", added_question.analysis_md)
        self.assertEqual(added_question.full_json["knowledge_level_1"], "运算符与表达式")
        self.assertEqual(added_question.full_json["knowledge_level_2"], "逻辑运算")
        self.assertEqual(
            list(added_question.options.order_by("sort_order").values_list("option_key", "option_text_md")),
            [("A", "新增 A"), ("B", "新增 B"), ("C", "新增 C")],
        )

        updated_preview_response = self.client.get(reverse("teacher-exam-bank-paper-preview", args=[paper.id]))
        self.assertContains(updated_preview_response, "修改后的试卷")
        self.assertContains(updated_preview_response, "修改后题干 N")
        self.assertContains(updated_preview_response, "cout &lt;&lt; 1;")
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

    def test_publish_screenshot_bank_paper_does_not_generate_ai_analysis_block(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2024,
            month=5,
            source_pdf_id="ai_analysis_crop_paper",
            source_file="2024年5月C++1级试题.pdf",
            title="AI解析截图卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        bank_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="ai-analysis-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列输出正确的是？",
            answer_json={"correct_answer": "A"},
            analysis_md="",
            programming_json={},
            full_json={"source": "pdf_crop_demo", "score": "2"},
        )
        for index, (key, text) in enumerate({"A": "2", "B": "3", "C": "4", "D": "5"}.items(), start=1):
            ExamQuestionBankOption.objects.create(
                question=bank_question,
                option_key=key,
                option_text_md=text,
                sort_order=index,
            )
        ExamQuestionBankAsset.objects.create(
            question=bank_question,
            asset_role="content",
            asset_type="image/png",
            relative_path="exam_assets/demo_questions/cpp/gesp1/2024_05/q001.png",
            alt="第 1 题截图",
        )

        with patch("entry.views.generate_ai_analysis_for_bank_question") as mock_generate:
            response = self.client.post(
                reverse("teacher-exams"),
                {
                    "form_action": "create_exam_from_bank_paper",
                    "bank_paper_id": str(bank_paper.id),
                    "exam_schedule_mode": "countdown",
                    "exam_schedule_duration_minutes": "45",
                },
            )

        self.assertEqual(response.status_code, 302)
        exam_question = ExamQuestion.objects.get(paper__title="AI解析截图卷", question_no=1)
        self.assertEqual(mock_generate.call_count, 0)
        self.assertFalse(
            ExamQuestionAnalysisBlock.objects.filter(
                question=exam_question,
                source_type=ExamQuestionAnalysisBlock.SOURCE_AI,
                is_visible=True,
            ).exists()
        )
        self.assertEqual(exam_question.analysis, "")

    def test_teacher_starts_bank_paper_analysis_generation_from_management_grid(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2024,
            month=5,
            source_pdf_id="analysis_button_paper",
            source_file="analysis-button.pdf",
            title="待生成解析试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )

        exam_page_response = self.client.get(reverse("teacher-exams"))
        self.assertContains(exam_page_response, "试卷管理")
        self.assertContains(exam_page_response, "解析状态")
        self.assertContains(exam_page_response, "知识点识别状态")
        self.assertContains(exam_page_response, "增加解析")
        self.assertContains(exam_page_response, "知识点识别")

        with patch("entry.views.start_exam_bank_paper_analysis_generation", return_value=True) as mock_start:
            response = self.client.post(
                reverse("teacher-exams"),
                {
                    "form_action": "generate_bank_paper_analysis",
                    "bank_paper_id": str(bank_paper.id),
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=bank_paper_analysis_started", response["Location"])
        self.assertIn("#available-exam-papers", response["Location"])
        mock_start.assert_called_once_with(bank_paper.id)

    def test_teacher_generates_bank_paper_knowledge_from_management_grid(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2026,
            month=6,
            source_pdf_id="knowledge_button_paper",
            source_file="knowledge-button.pdf",
            title="待识别知识点试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )

        with patch("entry.views.start_exam_bank_paper_knowledge_generation", return_value=True) as mock_start:
            response = self.client.post(
                reverse("teacher-exams"),
                {
                    "form_action": "generate_bank_paper_knowledge",
                    "bank_paper_id": str(bank_paper.id),
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=bank_paper_knowledge_generated", response["Location"])
        self.assertIn("#available-exam-papers", response["Location"])
        mock_start.assert_called_once_with(bank_paper.id)

    def test_missing_bank_paper_knowledge_mapping_shows_error_instead_of_500(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP8",
            year=2026,
            month=6,
            source_pdf_id="missing_knowledge_mapping_paper",
            source_file="missing-knowledge-mapping.pdf",
            title="缺少知识对照表试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="missing-knowledge-mapping-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列说法正确的是？",
            answer_json={"correct_answer": "A"},
            analysis_md="",
            programming_json={},
            full_json={},
        )

        response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "generate_bank_paper_knowledge",
                "bank_paper_id": str(bank_paper.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "未找到 GESP8 对应的知识对照表")

    def test_teacher_exam_bank_paper_status_reports_running_knowledge_generation(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2026,
            month=6,
            source_pdf_id="knowledge_status_paper",
            source_file="knowledge-status.pdf",
            title="知识点状态试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="knowledge-status-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列说法正确的是？",
            answer_json={"correct_answer": "A"},
            analysis_md="",
            programming_json={},
            full_json={"knowledge_status": "pending"},
        )

        response = self.client.get(reverse("teacher-exam-bank-paper-status"))

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()["rows"] if item["id"] == bank_paper.id)
        self.assertEqual(row["knowledge_status_text"], "识别中 0/1")
        self.assertFalse(row["can_generate_knowledge"])

    @override_settings(EXAM_AI_KNOWLEDGE_STALE_MINUTES=1)
    def test_teacher_exam_bank_paper_status_expires_stale_knowledge_generation(self) -> None:
        teacher_profile = Teacher.objects.create(
            user=self.teacher,
            display_name=self.teacher.full_name,
            phone=self.teacher.phone,
            subject="C++",
        )
        teacher_profile.courses.add(self.cpp_course)
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2026,
            month=6,
            source_pdf_id="stale_knowledge_status_paper",
            source_file="stale-knowledge-status.pdf",
            title="卡住的知识点状态试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        pending_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="stale-knowledge-status-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列说法正确的是？",
            answer_json={"correct_answer": "A"},
            analysis_md="",
            programming_json={},
            full_json={"knowledge_status": "pending"},
        )
        running_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="stale-knowledge-status-q-002",
            question_no=2,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 2 题 下列说法正确的是？",
            answer_json={"correct_answer": "B"},
            analysis_md="",
            programming_json={},
            full_json={"knowledge_status": "running"},
        )
        stale_time = timezone.now() - timedelta(minutes=15)
        ExamQuestionBankQuestion.objects.filter(id__in=[pending_question.id, running_question.id]).update(updated_at=stale_time)

        response = self.client.get(reverse("teacher-exam-bank-paper-status"))

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()["rows"] if item["id"] == bank_paper.id)
        self.assertEqual(row["knowledge_status_text"], "识别失败")
        self.assertTrue(row["can_generate_knowledge"])
        self.assertEqual(row["knowledge_failed_count"], 2)
        statuses = list(
            ExamQuestionBankQuestion.objects.filter(paper=bank_paper)
            .order_by("question_no")
            .values_list("full_json", flat=True)
        )
        self.assertEqual([item["knowledge_status"] for item in statuses], ["failed", "failed"])

    def test_gesp2_cpp_knowledge_mapping_is_seeded_for_search_and_ai_prompt(self) -> None:
        from entry.views import load_exam_knowledge_mapping_markdown, search_exam_knowledge_point_maps

        seeded_rows = ExamKnowledgePointMap.objects.filter(subject="cpp", category_code="GESP2")
        self.assertTrue(seeded_rows.filter(level_1="数学判断", level_2="奇偶判断").exists())
        search_rows = list(search_exam_knowledge_point_maps(subject="cpp", category_code="GESP2", query="奇偶"))
        self.assertTrue(any(row.level_2 == "奇偶判断" for row in search_rows))

        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2026,
            month=6,
            source_pdf_id="knowledge_map_prompt_paper",
            source_file="knowledge-map-prompt.pdf",
            title="知识映射提示试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        mapping_markdown = load_exam_knowledge_mapping_markdown(bank_paper)

        self.assertIn("| 数学判断 | 奇偶判断 |", mapping_markdown)

    def test_scratch_bank_paper_loads_knowledge_mapping_by_course_level_code(self) -> None:
        from entry.views import get_exam_bank_paper_edit_knowledge_rows, load_exam_knowledge_mapping_markdown

        ExamKnowledgePointMap.objects.update_or_create(
            subject="scratch",
            category_code="中国电子学会",
            level_1="熟悉编程软件",
            level_2="舞台区和角色区",
            level_3="认识 Scratch 基本区域",
            defaults={"course_level_code": "S1", "is_active": True, "sort_order": 1},
        )
        ExamKnowledgePointMap.objects.update_or_create(
            subject="scratch",
            category_code="中国电子学会",
            level_1="选择语句",
            level_2="如果那么",
            level_3="条件判断",
            defaults={"course_level_code": "S2", "is_active": True, "sort_order": 2},
        )
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="S1",
            year=2026,
            month=9,
            source_pdf_id="scratch_s1_knowledge_map_prompt_paper",
            source_file="202609图形化一级.docx",
            title="202609图形化一级",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )

        mapping_markdown = load_exam_knowledge_mapping_markdown(bank_paper)

        self.assertIn("| 熟悉编程软件 | 舞台区和角色区 | 认识 Scratch 基本区域 |", mapping_markdown)
        self.assertNotIn("| 选择语句 | 如果那么 | 条件判断 |", mapping_markdown)
        edit_rows = get_exam_bank_paper_edit_knowledge_rows(bank_paper)
        self.assertIn(
            {"level_1": "熟悉编程软件", "level_2": "舞台区和角色区", "level_3": "认识 Scratch 基本区域"},
            edit_rows,
        )
        self.assertNotIn(
            {"level_1": "选择语句", "level_2": "如果那么", "level_3": "条件判断"},
            edit_rows,
        )

    def test_teacher_can_create_single_exam_knowledge_point_for_owned_subject(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "create_exam_knowledge_point",
                "knowledge_subject": "python",
                "knowledge_course_level_code": "P1",
                "knowledge_category_manual": "Python等级考试",
                "knowledge_level_1": "基础语法",
                "knowledge_level_2": "变量",
                "knowledge_level_3": "赋值语句",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=knowledge_created", response["Location"])
        knowledge = ExamKnowledgePointMap.objects.get(
            subject="python",
            category_code="PYTHON等级考试",
            level_1="基础语法",
            level_2="变量",
            level_3="赋值语句",
        )
        self.assertEqual(knowledge.course_level_code, "P1")
        self.assertEqual(knowledge.uploaded_by, self.teacher)

    def test_bank_paper_knowledge_generation_updates_editable_fields_and_published_exam(self) -> None:
        from entry.views import generate_bank_paper_knowledge_points

        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP2",
            year=2026,
            month=6,
            source_pdf_id="knowledge_generation_paper",
            source_file="knowledge-generation.pdf",
            title="知识点识别试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        bank_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="knowledge-generation-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列哪个表达式可以判断偶数？",
            answer_json={"correct_answer": "A"},
            analysis_md="使用取余判断。",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankOption.objects.create(question=bank_question, option_key="A", option_text_md="x % 2 == 0", sort_order=1)
        ExamQuestionBankOption.objects.create(question=bank_question, option_key="B", option_text_md="x / 2 == 0", sort_order=2)
        self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "create_exam_from_bank_paper",
                "bank_paper_id": str(bank_paper.id),
                "exam_schedule_mode": "countdown",
                "exam_schedule_duration_minutes": "45",
            },
        )

        with patch("entry.views.identify_bank_question_knowledge_points") as mock_identify:
            mock_identify.return_value = {
                "level_1": "数学判断",
                "level_2": "奇偶判断",
                "level_3": "x % 2 == 0",
            }
            updated_count = generate_bank_paper_knowledge_points(bank_paper.id)

        self.assertEqual(updated_count, 1)
        bank_question.refresh_from_db()
        self.assertEqual(bank_question.full_json["knowledge_level_1"], "数学判断")
        self.assertEqual(bank_question.full_json["knowledge_level_2"], "奇偶判断")
        self.assertEqual(bank_question.full_json["knowledge_level_3"], "x % 2 == 0")
        edit_response = self.client.get(reverse("teacher-exam-bank-paper-edit", args=[bank_paper.id]))
        self.assertContains(edit_response, "一级目录")
        self.assertContains(edit_response, "数学判断")
        exam_page_response = self.client.get(reverse("teacher-exams"))
        available_row = next(row for row in exam_page_response.context["available_paper_rows"] if row["id"] == bank_paper.id)
        self.assertEqual(available_row["knowledge_status_text"], "识别完成")
        exam_question = ExamQuestion.objects.get(paper__title="知识点识别试卷", question_no=1)
        self.assertEqual(exam_question.wrong_point_label, "数学判断 / 奇偶判断 / x % 2 == 0")

    def test_teacher_generates_single_missing_bank_question_analysis_from_preview(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2026,
            month=6,
            source_pdf_id="single-analysis-paper",
            source_file="single-analysis.pdf",
            title="单题解析试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="single-analysis-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列输出正确的是？",
            answer_json={"correct_answer": "A"},
            analysis_md="",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankOption.objects.create(question=question, option_key="A", option_text_md="2", sort_order=1)
        ExamQuestionBankOption.objects.create(question=question, option_key="B", option_text_md="3", sort_order=2)

        preview_response = self.client.get(reverse("teacher-exam-bank-paper-preview", args=[bank_paper.id]))
        self.assertContains(preview_response, "生成解析")
        self.assertContains(preview_response, "当前没有解析。")

        with patch("entry.views.generate_ai_analysis_for_bank_question") as mock_generate:
            mock_generate.return_value = "单题解析已生成。\n\n```cpp\ncout << 2;\n```"
            response = self.client.post(
                reverse("teacher-exam-bank-paper-preview", args=[bank_paper.id]),
                {
                    "form_action": "generate_bank_question_analysis",
                    "question_id": str(question.id),
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=question_analysis_generated", response["Location"])
        self.assertIn(f"#bank-question-{question.id}", response["Location"])
        question.refresh_from_db()
        bank_paper.refresh_from_db()
        self.assertIn("单题解析已生成", question.analysis_md)
        self.assertEqual(bank_paper.analysis_generation_done_count, 1)

        updated_preview_response = self.client.get(reverse("teacher-exam-bank-paper-preview", args=[bank_paper.id]))
        self.assertContains(updated_preview_response, "cout &lt;&lt; 2;")

    def test_teacher_generates_single_bank_question_analysis_from_edit_page(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2026,
            month=6,
            source_pdf_id="single-analysis-edit-paper",
            source_file="single-analysis-edit.pdf",
            title="编辑页单题解析试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="single-analysis-edit-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列输出正确的是？",
            answer_json={"correct_answer": "A"},
            analysis_md="",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankOption.objects.create(question=question, option_key="A", option_text_md="2", sort_order=1)
        ExamQuestionBankOption.objects.create(question=question, option_key="B", option_text_md="3", sort_order=2)

        edit_response = self.client.get(reverse("teacher-exam-bank-paper-edit", args=[bank_paper.id]))
        self.assertContains(edit_response, "单独解析")
        self.assertContains(edit_response, f'id="exam-bank-question-analysis-{question.id}"', html=False)

        with patch("entry.views.generate_ai_analysis_for_bank_question") as mock_generate:
            mock_generate.return_value = "编辑页单题解析已生成。"
            response = self.client.post(
                reverse("teacher-exam-bank-paper-preview", args=[bank_paper.id]),
                {
                    "form_action": "generate_bank_question_analysis",
                    "question_id": str(question.id),
                    "return_to": "edit",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("teacher-exam-bank-paper-edit", args=[bank_paper.id]), response["Location"])
        self.assertIn(f"#bank-question-{question.id}", response["Location"])
        question.refresh_from_db()
        self.assertIn("编辑页单题解析已生成", question.analysis_md)

    def test_bank_paper_analysis_generation_updates_snapshot_and_published_exam(self) -> None:
        from entry.views import run_exam_bank_paper_analysis_generation

        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2024,
            month=5,
            source_pdf_id="analysis_background_paper",
            source_file="analysis-background.pdf",
            title="后台解析试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
            analysis_generation_status=ExamQuestionBankPaper.ANALYSIS_STATUS_RUNNING,
        )
        bank_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="analysis-background-q-001",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="第 1 题 下列输出正确的是？",
            answer_json={"correct_answer": "A"},
            analysis_md="",
            programming_json={},
            full_json={"source": "pdf_crop_demo", "score": "2"},
        )
        for index, (key, text) in enumerate({"A": "2", "B": "3", "C": "4", "D": "5"}.items(), start=1):
            ExamQuestionBankOption.objects.create(
                question=bank_question,
                option_key=key,
                option_text_md=text,
                sort_order=index,
            )
        start_at = timezone.now() + timedelta(minutes=10)
        self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "create_exam_from_bank_paper",
                "bank_paper_id": str(bank_paper.id),
                "exam_schedule_mode": "scheduled",
                "exam_schedule_start_at": self.format_datetime_local(start_at),
                "exam_schedule_end_at": self.format_datetime_local(start_at + timedelta(minutes=45)),
            },
        )

        with patch("entry.views.generate_ai_analysis_for_bank_question") as mock_generate:
            mock_generate.return_value = "因为输出结果是 2。\n\n**此解析由 AI 生成，你要挑战吗？**"
            run_exam_bank_paper_analysis_generation(bank_paper.id)

        bank_question.refresh_from_db()
        bank_paper.refresh_from_db()
        self.assertIn("因为输出结果是 2", bank_question.analysis_md)
        self.assertEqual(bank_paper.analysis_generation_status, ExamQuestionBankPaper.ANALYSIS_STATUS_COMPLETED)
        self.assertEqual(bank_paper.analysis_generation_done_count, 1)
        exam_question = ExamQuestion.objects.get(paper__title="后台解析试卷", question_no=1)
        self.assertIn("因为输出结果是 2", exam_question.analysis)

    def test_deleted_exam_management_record_allows_bank_paper_hard_delete(self) -> None:
        self.sign_in(self.teacher)
        bank_paper = ExamQuestionBankPaper.objects.create(
            level="GESP1",
            year=2026,
            month=6,
            source_pdf_id="delete_after_exam_management_removed",
            source_file="delete-after-management.pdf",
            title="管理删除后可硬删除试卷",
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            is_active=True,
        )
        bank_question = ExamQuestionBankQuestion.objects.create(
            paper=bank_paper,
            question_uid="delete_after_exam_management_removed_q1",
            question_no=1,
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem_md="1 + 1 = ?",
            answer_json={"correct_answer": "A"},
            analysis_md="",
            programming_json={},
            full_json={},
        )
        ExamQuestionBankOption.objects.create(question=bank_question, option_key="A", option_text_md="2", sort_order=1)
        start_at = timezone.now() + timedelta(minutes=5)
        end_at = start_at + timedelta(minutes=45)
        publish_response = self.client.post(
            reverse("teacher-exams"),
            {
                "form_action": "create_exam_from_bank_paper",
                "bank_paper_id": str(bank_paper.id),
                "exam_schedule_mode": "scheduled",
                "exam_schedule_start_at": self.format_datetime_local(start_at),
                "exam_schedule_end_at": self.format_datetime_local(end_at),
            },
        )
        self.assertEqual(publish_response.status_code, 302)
        exam_paper = ExamPaper.objects.get(title=bank_paper.title)

        published_page = self.client.get(reverse("teacher-exams"))
        published_row = next(row for row in published_page.context["available_paper_rows"] if row["id"] == bank_paper.id)
        self.assertEqual(published_row["delete_label"], "删除")

        delete_exam_response = self.client.post(
            reverse("teacher-exams"),
            {"form_action": "delete_exam", "paper_id": str(exam_paper.id)},
        )
        self.assertEqual(delete_exam_response.status_code, 302)
        exam_paper.refresh_from_db()
        self.assertFalse(exam_paper.is_active)

        after_management_delete_page = self.client.get(reverse("teacher-exams"))
        hard_delete_row = next(row for row in after_management_delete_page.context["available_paper_rows"] if row["id"] == bank_paper.id)
        self.assertEqual(hard_delete_row["delete_label"], "硬删除")
        hard_delete_response = self.client.post(
            reverse("teacher-exams"),
            {"form_action": "delete_bank_paper", "bank_paper_id": str(bank_paper.id)},
        )
        self.assertEqual(hard_delete_response.status_code, 302)
        self.assertIn("mode=hard", hard_delete_response["Location"])
        self.assertFalse(ExamQuestionBankPaper.objects.filter(id=bank_paper.id).exists())

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
                "3",
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
        self.assertNotIn("\n3\n", question["stem_md"])
        self.assertNotIn("1 int a", question["stem_md"])
        self.assertNotIn("2 | cout", question["stem_md"])

    def test_ocr_markdown_wraps_unfenced_numbered_cpp_block(self) -> None:
        markdown_text = "\n".join(
            [
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 7 |",
                "| 答案 | A |",
                "第 7 题 下面 C++ 代码执行时，其说法正确的是（ ）。",
                "1 int M = 0, N = 0;",
                "2 cin >> M;",
                "3 cin >> N;",
                "4",
                "5 if (N > M)",
                "6     cout << (N - M);",
                "7 else",
                "8     cout << (M - N);",
                "A. 正确",
                "B. 错误",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)

        stem_md = parsed_questions[0]["stem_md"]
        self.assertIn("```cpp", stem_md)
        self.assertIn("int M = 0, N = 0;", stem_md)
        self.assertIn("\n\nif (N > M)", stem_md)
        self.assertIn("    cout << (N - M);", stem_md)
        self.assertIn("    cout << (M - N);", stem_md)
        self.assertNotIn("\n4\n", stem_md)
        self.assertNotIn("1 int M", stem_md)

    def test_display_markdown_strips_indented_code_line_numbers(self) -> None:
        rendered = render_exam_markdown_for_display(
            "\n".join(
                [
                    "```cpp",
                    "int i;",
                    "for (i = 1; i < 10; i++){",
                    "3     if (i % 2 == 0){",
                    "4         continue;     // L1",
                    "5     }",
                    "}",
                    'printf("%2d%2d\\n", i, i);',
                    "```",
                ]
            )
        )

        rendered_text = str(rendered)
        self.assertIn("    if (i % 2 == 0){", rendered_text)
        self.assertIn("        continue;     // L1", rendered_text)
        self.assertNotIn("3     if", rendered_text)
        self.assertIn("%2d%2d\\n", rendered_text)

    def test_choice_option_numeric_code_content_is_not_stripped_as_line_number(self) -> None:
        markdown_text = "\n".join(
            [
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 8 |",
                "| 答案 | A |",
                "第 8 题 下面程序输出结果是？（ ）",
                "A.",
                "```",
                "1 | 24 5",
                "```",
                "B.",
                "```",
                "1 | 10 5",
                "```",
                "C.",
                "```",
                "1 0 4",
                "```",
                "D.",
                "```",
                "1 0 5",
                "```",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)

        question = parsed_questions[0]
        self.assertIn("24 5", question["options"]["A"])
        self.assertNotIn("1 | 24 5", question["options"]["A"])
        self.assertNotIn("``` 5 ```", question["options"]["A"])
        self.assertIn("10 5", question["options"]["B"])
        self.assertIn("0 4", question["options"]["C"])
        self.assertIn("0 5", question["options"]["D"])

    def test_choice_option_compact_code_fence_splits_output_lines(self) -> None:
        markdown_text = "\n".join(
            [
                "1 单选题（每题 2 分，共 30 分）",
                "| 题号 | 5 |",
                "| 答案 | A |",
                "第 5 题 输出是？（ ）",
                "A. ```6143```",
                "B. ```5234```",
                "C. ```6244```",
                "D. ```6232```",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)

        options = parsed_questions[0]["options"]
        self.assertIn("61\n43", options["A"])
        self.assertIn("52\n34", options["B"])
        self.assertNotIn("6143", options["A"])

    def test_render_exam_markdown_expands_literal_newline_options(self) -> None:
        rendered = render_exam_markdown_for_display("61\\n43")

        self.assertIn("61<br>43", str(rendered))

    def test_render_exam_markdown_handles_inline_fenced_option_code(self) -> None:
        rendered = render_exam_markdown_for_display("```cpp tnt += N / 10\\n N /= 10```")

        rendered_text = str(rendered)
        self.assertIn("<pre", rendered_text)
        self.assertIn("tnt += N / 10\n N /= 10", rendered_text)
        self.assertNotIn("```cpp", rendered_text)

    def test_render_exam_markdown_formats_common_analysis_markdown(self) -> None:
        rendered = render_exam_markdown_for_display(
            "\n".join(
                [
                    "**此解析由 AI 生成，你要挑战吗？**",
                    "",
                    "- 先看 `return` 的作用",
                    "- 再判断分支结构",
                ]
            )
        )

        rendered_text = str(rendered)
        self.assertIn("<strong>此解析由 AI 生成，你要挑战吗？</strong>", rendered_text)
        self.assertIn('<code class="exam-markdown-body__inline-code">return</code>', rendered_text)
        self.assertIn('<ul class="exam-markdown-body__list">', rendered_text)
        self.assertNotIn("**此解析", rendered_text)

    def test_programming_reference_solution_stops_before_next_programming_question(self) -> None:
        markdown_text = "\n".join(
            [
                "3 编程题（每题 25 分，共 50 分）",
                "3.1 编程题 1",
                "试题名称：交朋友",
                "3.1.1 题目描述",
                "Alice 想交朋友。",
                "3.1.7 参考程序",
                "```cpp",
                "#include <iostream>",
                "int main() { return 0; }",
                "```",
                "3.2 编程题 2",
                "试题名称：数字替换",
                "3.2.1 题目描述",
                "把 4 替换成 8。",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)
        first_programming = next(item for item in parsed_questions if item["question_no"] == 1)
        second_programming = next(item for item in parsed_questions if item["question_no"] == 2)

        self.assertIn("交朋友", first_programming["stem_md"])
        self.assertNotIn("参考程序", first_programming["stem_md"])
        self.assertNotIn("数字替换", first_programming["analysis_md"])
        self.assertIn("参考程序", first_programming["analysis_md"])
        self.assertIn("数字替换", second_programming["stem_md"])

    def test_programming_unfenced_reference_solution_moves_to_analysis(self) -> None:
        markdown_text = "\n".join(
            [
                "3 编程题（每题 25 分，共 50 分）",
                "3.1 编程题 1",
                "试题名称：交朋友",
                "3.1.1 题目描述",
                "Alice 想交朋友。",
                "### 3.1.7 参考程序",
                "1 #include <iostream>",
                "2",
                "3 using namespace std;",
                "4 int main(){",
                "5     return 0;",
                "6 }",
                "3.2 编程题 2",
                "试题名称：数字替换",
                "3.2.1 题目描述",
                "把 4 替换成 8。",
            ]
        )

        parsed_questions = split_ocr_markdown_into_question_blocks(markdown_text)
        first_programming = next(item for item in parsed_questions if item["question_no"] == 1)
        second_programming = next(item for item in parsed_questions if item["question_no"] == 2)

        self.assertNotIn("参考程序", first_programming["stem_md"])
        self.assertNotIn("#include <iostream>", first_programming["stem_md"])
        self.assertIn("参考程序", first_programming["analysis_md"])
        self.assertIn("#include <iostream>", first_programming["analysis_md"])
        self.assertIn("数字替换", second_programming["stem_md"])

    def test_teacher_edit_display_keeps_code_fence_and_save_restores_cpp_marker(self) -> None:
        original = "第 1 题 阅读代码。\n\n```cpp\n1 int main() {\n2     return 0;\n3 }\n```"

        display_text = format_exam_markdown_for_teacher_edit(original)
        saved_text = restore_exam_markdown_code_fences_from_original(
            "第 1 题 修改后阅读代码。\nint main() {\n    return 0;\n}",
            original,
        )

        self.assertIn("```cpp", display_text)
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
        answer_sheet_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertEqual(answer_sheet_response.status_code, 200)
        self.assertContains(answer_sheet_response, 'id="student-exam-paper-layout"', html=False)
        self.assertContains(answer_sheet_response, 'data-student-exam-question-button', html=False)
        self.assertContains(answer_sheet_response, "上一题")
        self.assertContains(answer_sheet_response, "下一题")

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

    def test_student_submits_scratch_programming_question_for_manual_review(self) -> None:
        paper = ExamPaper.objects.create(
            teacher=self.teacher,
            course=self.course,
            title="Scratch 编程题测试",
            status=ExamPaper.STATUS_PUBLISHED,
            mode=ExamPaper.MODE_DEADLINE,
            start_at=timezone.now() - timedelta(minutes=5),
            end_at=timezone.now() + timedelta(days=1),
        )
        question = ExamQuestion.objects.create(
            paper=paper,
            question_no=1,
            question_type=ExamQuestion.QUESTION_TYPE_PROGRAMMING,
            stem="制作一个角色说你好。",
            options_json={},
            correct_answer="",
            score="25",
            source_snapshot_json={"programming_json": {"submission_mode": "scratch_project", "grading_mode": "manual"}},
        )
        session = ExamSession.objects.create(
            paper=paper,
            student=self.student,
            assigned_by=self.teacher,
            status=ExamSession.STATUS_IN_PROGRESS,
            started_at=timezone.now(),
        )

        self.sign_in(self.student_user)
        detail_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, f'name="programming_submission_{question.id}"', html=False)
        self.assertContains(detail_response, "提交 Scratch 作品链接")

        submit_response = self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {
                "form_action": "submit_exam",
                f"programming_submission_{question.id}": "https://scratch.mit.edu/projects/123456789/ 角色会说你好",
            },
        )
        self.assertEqual(submit_response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.status, ExamSession.STATUS_SUBMITTED)
        self.assertEqual(session.total_count, 0)
        answer = ExamSubmissionAnswer.objects.get(session=session, question=question)
        self.assertEqual(answer.selected_answer, "")
        self.assertIn("scratch.mit.edu/projects/123456789", answer.explanation_text)

        result_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertContains(result_response, "已交卷")
        self.assertContains(result_response, "你的提交")
        self.assertContains(result_response, "scratch.mit.edu/projects/123456789")

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

    def test_student_exam_detail_shows_question_knowledge_attribution(self) -> None:
        session = self.create_exam_via_teacher_view()
        question = session.paper.questions.get()
        question.wrong_point_label = "数学判断 / 奇偶判断 / x % 2 == 0"
        question.source_snapshot_json = {
            "level_code": "GESP2",
            "knowledge_level_1": "数学判断",
            "knowledge_level_2": "奇偶判断",
            "knowledge_level_3": "x % 2 == 0",
        }
        question.save(update_fields=["wrong_point_label", "source_snapshot_json", "updated_at"])

        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "submit_exam", f"question_{question.id}": "A"},
        )
        response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertContains(response, "知识点：数学判断 / 奇偶判断 / x % 2 == 0")

    def test_student_practice_exposes_free_practice_entry(self) -> None:
        self.sign_in(self.student_user)
        response = self.client.get(reverse("student-practice"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "自由练习")
        self.assertContains(response, reverse("student-free-practice"))

    def test_student_free_practice_requires_level_before_knowledge_directories(self) -> None:
        self.sign_in(self.student_user)
        response = self.client.get(reverse("student-free-practice"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["level1_disabled"])
        self.assertTrue(response.context["level2_disabled"])
        self.assertContains(response, "请先选择等级，再选择知识点一级目录。")
        self.assertContains(response, 'data-free-practice-combobox="level1"', html=False)
        self.assertContains(response, "disabled")

    def test_student_free_practice_level_options_follow_cpp_permission_level(self) -> None:
        for category_code in ["GESP1", "GESP5", "GESP8"]:
            ExamKnowledgePointMap.objects.update_or_create(
                subject="cpp",
                category_code=category_code,
                level_1="测试目录",
                level_2=f"{category_code} 二级",
                level_3="",
                defaults={"is_active": True},
        )
        self.student.primary_level_name = "C1"
        self.student.save(update_fields=["primary_level_name"])
        self.sign_in(self.student_user)

        c1_response = self.client.get(reverse("student-free-practice"))
        c1_values = {item["value"] for item in c1_response.context["level_options"]}
        self.assertIn("GESP1", c1_values)
        self.assertIn("GESP2", c1_values)
        self.assertNotIn("GESP5", c1_values)
        self.assertNotIn("CSP-J", c1_values)

        self.student.primary_level_name = "C3"
        self.student.save(update_fields=["primary_level_name"])
        c3_response = self.client.get(reverse("student-free-practice"))
        c3_values = {item["value"] for item in c3_response.context["level_options"]}
        self.assertIn("GESP8", c3_values)
        self.assertIn("CSP-J", c3_values)
        self.assertNotIn("CSP-S", c3_values)

        self.student.primary_level_name = "C4"
        self.student.save(update_fields=["primary_level_name"])
        c4_response = self.client.get(reverse("student-free-practice"))
        c4_values = {item["value"] for item in c4_response.context["level_options"]}
        self.assertIn("GESP8", c4_values)
        self.assertIn("CSP-J", c4_values)
        self.assertIn("CSP-S", c4_values)

    def test_student_free_practice_caps_selection_at_twenty(self) -> None:
        paper = ExamPaper.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            title="GESP2自由练习来源卷",
            description="自由练习测试",
            mode=ExamPaper.MODE_DEADLINE,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=1),
            duration_minutes=45,
            status=ExamPaper.STATUS_PUBLISHED,
            is_active=True,
        )
        question_ids = []
        for index in range(1, 22):
            question = ExamQuestion.objects.create(
                paper=paper,
                question_no=index,
                question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                stem=f"判断 {index} 是否为偶数？",
                options_json={"A": "是", "B": "否", "C": "不确定", "D": "无法判断"},
                correct_answer="A",
                analysis="偶数可以被 2 整除。",
                score="1",
                wrong_point_label="数学判断 / 奇偶判断",
                source_snapshot_json={
                    "level_code": "GESP2",
                    "knowledge_level_1": "数学判断",
                    "knowledge_level_2": "奇偶判断",
                },
                is_active=True,
            )
            question_ids.append(question.id)

        self.sign_in(self.student_user)
        list_response = self.client.get(
            reverse("student-free-practice"),
            {"level_code": "GESP2", "knowledge_query": "数学"},
        )
        self.assertEqual(len(list_response.context["question_rows"]), 20)
        over_limit_response = self.client.post(
            reverse("student-free-practice"),
            {
                "form_action": "start_free_practice",
                "level_code": "GESP2",
                "knowledge_query": "数学",
                "question_ids": [str(question_id) for question_id in question_ids],
            },
        )
        self.assertEqual(over_limit_response.status_code, 200)
        self.assertContains(over_limit_response, "练习不在多，而在精。")
        self.assertEqual(len(over_limit_response.context["selected_question_ids"]), 20)

    def test_student_free_practice_csp_j_all_level1_shows_twenty_questions(self) -> None:
        paper = ExamPaper.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            title="CSP-J自由练习综合来源卷",
            description="自由练习测试",
            mode=ExamPaper.MODE_DEADLINE,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=1),
            duration_minutes=45,
            status=ExamPaper.STATUS_PUBLISHED,
            is_active=True,
        )
        for index in range(1, 26):
            ExamQuestion.objects.create(
                paper=paper,
                question_no=index,
                question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                stem=f"CSP 综合题 {index}",
                options_json={"A": "正确", "B": "错误"},
                correct_answer="A",
                analysis="综合练习解析。",
                score="1",
                wrong_point_label="数学判断 / 奇偶判断",
                source_snapshot_json={
                    "level_code": "GESP2",
                    "knowledge_level_1": "数学判断",
                    "knowledge_level_2": "奇偶判断",
                },
                is_active=True,
            )

        self.sign_in(self.student_user)
        response = self.client.get(reverse("student-free-practice"), {"level_code": "CSP-J"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["question_rows"]), 20)
        self.assertContains(response, "CSP 综合题")

    def test_student_can_start_free_practice_from_selected_questions(self) -> None:
        paper = ExamPaper.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            title="GESP2自由练习创建卷",
            description="自由练习测试",
            mode=ExamPaper.MODE_DEADLINE,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=1),
            duration_minutes=45,
            status=ExamPaper.STATUS_PUBLISHED,
            is_active=True,
        )
        first_question = ExamQuestion.objects.create(
            paper=paper,
            question_no=1,
            question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem="1 是否为奇数？",
            options_json={"A": "是", "B": "否", "C": "不确定", "D": "无法判断"},
            correct_answer="A",
            analysis="1 是奇数。",
            score="1",
            wrong_point_label="数学判断 / 奇偶判断",
            source_snapshot_json={
                "level_code": "GESP2",
                "knowledge_level_1": "数学判断",
                "knowledge_level_2": "奇偶判断",
            },
            is_active=True,
        )
        second_question = ExamQuestion.objects.create(
            paper=paper,
            question_no=2,
            question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem="2 是否为偶数？",
            options_json={"A": "是", "B": "否", "C": "不确定", "D": "无法判断"},
            correct_answer="A",
            analysis="2 是偶数。",
            score="1",
            wrong_point_label="数学判断 / 奇偶判断",
            source_snapshot_json={
                "level_code": "GESP2",
                "knowledge_level_1": "数学判断",
                "knowledge_level_2": "奇偶判断",
            },
            is_active=True,
        )

        self.sign_in(self.student_user)
        ExamSession.objects.create(
            paper=paper,
            student=self.student,
            assigned_by=self.teacher,
            attempt_no=1,
            session_type=ExamSession.SESSION_TYPE_EXAM,
            status=ExamSession.STATUS_ASSIGNED,
            is_active=True,
        )
        paper_count_before = ExamPaper.objects.count()
        response = self.client.post(
            reverse("student-free-practice"),
            {
                "form_action": "start_free_practice",
                "level_code": "GESP2",
                "knowledge_query": "数学",
                "question_ids": [str(first_question.id), str(second_question.id)],
            },
        )
        self.assertEqual(response.status_code, 302)
        practice_session = ExamSession.objects.get(student=self.student, session_type=ExamSession.SESSION_TYPE_FREE_PRACTICE)
        self.assertEqual(practice_session.status, ExamSession.STATUS_IN_PROGRESS)
        self.assertEqual(practice_session.attempt_no, 2)
        self.assertEqual(ExamPaper.objects.count(), paper_count_before)
        self.assertEqual(practice_session.paper_id, paper.id)
        self.assertEqual(practice_session.paper.questions.filter(is_active=True).count(), 2)
        detail_response = self.client.get(reverse("student-exam-detail", args=[practice_session.id]))
        self.assertContains(detail_response, "自由练习")
        self.assertContains(detail_response, "知识点：数学判断 / 奇偶判断")

    def test_student_free_practice_question_list_renders_source_images(self) -> None:
        paper = ExamPaper.objects.create(
            teacher=self.teacher,
            course=self.cpp_course,
            title="GESP2截图题来源卷",
            description="自由练习截图测试",
            mode=ExamPaper.MODE_DEADLINE,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=1),
            duration_minutes=45,
            status=ExamPaper.STATUS_PUBLISHED,
            is_active=True,
        )
        ExamQuestion.objects.create(
            paper=paper,
            question_no=23,
            question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem="第 23 题（见截图）",
            options_json={"A": "正确", "B": "错误"},
            correct_answer="A",
            analysis="看截图判断。",
            score="1",
            wrong_point_label="嵌套循环 / 嵌套枚举",
            image_path="exam_assets/demo_questions/cpp/gesp2/2024_06/q023.png",
            source_snapshot_json={
                "level_code": "GESP2",
                "knowledge_level_1": "嵌套循环",
                "knowledge_level_2": "嵌套枚举",
                "image_paths": ["exam_assets/demo_questions/cpp/gesp2/2024_06/q023.png"],
            },
            is_active=True,
        )

        self.sign_in(self.student_user)
        response = self.client.get(reverse("student-free-practice"), {"level_code": "GESP2", "knowledge_query": "嵌套"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/media/exam_assets/demo_questions/cpp/gesp2/2024_06/q023.png")
        self.assertContains(response, "第 23 题（见截图）")

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

    def test_teacher_can_start_new_exam_run_after_previous_run_ended(self) -> None:
        session = self.create_exam_via_teacher_view()
        paper = session.paper

        self.sign_in(self.teacher)
        first_start_response = self.client.post(
            reverse("teacher-exams"),
            {"form_action": "start_exam", "paper_id": str(paper.id)},
        )
        self.assertEqual(first_start_response.status_code, 302)
        paper.refresh_from_db()
        first_access_code = paper.access_code
        first_run = paper.runs.get(access_code=first_access_code)

        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        session.refresh_from_db()
        self.assertEqual(session.status, ExamSession.STATUS_IN_PROGRESS)
        self.assertEqual(session.exam_run_id, first_run.id)

        paper.end_at = timezone.now() - timedelta(minutes=1)
        paper.save(update_fields=["end_at", "updated_at"])

        self.sign_in(self.teacher)
        second_start_response = self.client.post(
            reverse("teacher-exams"),
            {"form_action": "start_exam", "paper_id": str(paper.id)},
        )
        self.assertEqual(second_start_response.status_code, 302)
        paper.refresh_from_db()
        session.refresh_from_db()
        self.assertRegex(paper.access_code, r"^\d{6}$")
        self.assertNotEqual(paper.access_code, first_access_code)
        self.assertEqual(session.status, ExamSession.STATUS_EXPIRED)
        self.assertEqual(paper.runs.count(), 2)
        second_run = paper.runs.order_by("-generated_at", "-id").first()
        self.assertIsNotNone(second_run)
        self.assertEqual(second_run.access_code, paper.access_code)
        self.assertGreater(paper.end_at, timezone.now())

        self.sign_in(self.student_user)
        enter_response = self.client.post(
            reverse("student-exam-list"),
            {"form_action": "enter_exam_access_code", "exam_access_code": paper.access_code},
        )
        self.assertEqual(enter_response.status_code, 302)
        new_session = ExamSession.objects.get(paper=paper, student=self.student, attempt_no=2)
        self.assertEqual(new_session.exam_run_id, second_run.id)
        self.assertEqual(new_session.status, ExamSession.STATUS_ASSIGNED)
        self.assertIn(reverse("student-exam-detail", args=[new_session.id]), enter_response["Location"])

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

    def test_teacher_updates_exam_answer_and_recalculates_submitted_scores(self) -> None:
        session = self.create_exam_via_teacher_view()
        question = session.paper.questions.get()

        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "submit_exam", f"question_{question.id}": "B"},
        )
        session.refresh_from_db()
        self.assertEqual(session.correct_count, 0)
        self.assertEqual(str(session.earned_score), "0.00")
        answer = ExamSubmissionAnswer.objects.get(session=session, question=question)
        self.assertFalse(answer.is_correct)
        self.assertEqual(answer.correct_answer_snapshot, "A")

        self.sign_in(self.teacher)
        detail_response = self.client.get(reverse("teacher-exam-detail", args=[session.paper_id]))
        self.assertContains(detail_response, "修改答案")
        response = self.client.post(
            reverse("teacher-exam-detail", args=[session.paper_id]),
            {
                "form_action": "update_exam_question_answer",
                "question_id": str(question.id),
                "correct_answer": "B",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "已修改第 1 题标准答案，并同步重算 1 条提交记录。")
        question.refresh_from_db()
        self.assertEqual(question.correct_answer, "B")
        session.refresh_from_db()
        self.assertEqual(session.correct_count, 1)
        self.assertEqual(session.wrong_count, 0)
        self.assertEqual(str(session.earned_score), "2.00")
        answer.refresh_from_db()
        self.assertTrue(answer.is_correct)
        self.assertEqual(str(answer.score), "2.00")
        self.assertEqual(answer.correct_answer_snapshot, "B")
        rows_by_question_no = {row["question_no"]: row for row in response.context["question_rows"]}
        self.assertEqual(rows_by_question_no[1]["correct_count"], 1)
        self.assertEqual(rows_by_question_no[1]["wrong_count"], 0)
        self.assertEqual(rows_by_question_no[1]["wrong_rate_text"], "0%")

        analysis_response = self.client.post(
            reverse("teacher-exam-detail", args=[session.paper_id]),
            {
                "form_action": "update_exam_question_analysis",
                "question_id": str(question.id),
                "analysis": "老师补充解析：选择 B 才符合题意。",
            },
            follow=True,
        )
        self.assertContains(analysis_response, "已更新第 1 题解析")
        question.refresh_from_db()
        self.assertEqual(question.analysis, "老师补充解析：选择 B 才符合题意。")
        answer.refresh_from_db()
        self.assertEqual(answer.analysis_snapshot, "老师补充解析：选择 B 才符合题意。")

        self.sign_in(self.student_user)
        student_result_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertContains(student_result_response, "老师补充解析：选择 B 才符合题意。")

    def test_student_analysis_suggestion_can_be_accepted_by_teacher(self) -> None:
        session = self.create_exam_via_teacher_view()
        question = session.paper.questions.get()
        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {"form_action": "submit_exam", f"question_{question.id}": "A"},
        )

        suggestion_response = self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {
                "form_action": f"submit_analysis_suggestion:{question.id}",
                f"analysis_suggestion_{question.id}": "我觉得可以这样解释：\n\n```cpp\ncout << 2;\n```",
            },
            follow=True,
        )
        self.assertContains(suggestion_response, "你的解析挑战已提交")
        self.assertContains(suggestion_response, "C++代码块")
        self.assertContains(suggestion_response, 'data-markdown-command="cpp-block"')
        suggestion = ExamQuestionAnalysisSuggestion.objects.get(question=question, student=self.student)
        self.assertEqual(suggestion.status, ExamQuestionAnalysisSuggestion.STATUS_PENDING)

        self.sign_in(self.teacher)
        workbench_response = self.client.get(f"{reverse('teacher-students')}?tab=messages")
        self.assertEqual(workbench_response.status_code, 200)
        self.assertContains(workbench_response, "解析挑战消息")
        self.assertContains(workbench_response, "teacher-tab__badge")
        self.assertEqual(workbench_response.context["page_shell"]["message_count"], 1)
        self.assertEqual(workbench_response.context["page_shell"]["message_table_rows"][0]["student_name"], "考试学生")
        self.assertIn(
            f"#analysis-suggestion-{suggestion.id}",
            workbench_response.context["page_shell"]["message_table_rows"][0]["detail_href"],
        )

        detail_response = self.client.get(reverse("teacher-exam-detail", args=[session.paper_id]))
        self.assertContains(detail_response, "学生解析建议 1 条")
        self.assertContains(detail_response, "考试学生")
        self.assertContains(detail_response, "cout &lt;&lt; 2;")

        accept_response = self.client.post(
            reverse("teacher-exam-detail", args=[session.paper_id]),
            {
                "form_action": "accept_exam_analysis_suggestion",
                "question_id": str(question.id),
                "suggestion_id": str(suggestion.id),
                "accepted_analysis": "学生给出的代码推导更清楚。\n\n```cpp\ncout << 2;\n```",
            },
            follow=True,
        )
        self.assertContains(accept_response, "已采纳第 1 题的学生解析")
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, ExamQuestionAnalysisSuggestion.STATUS_ACCEPTED)
        self.assertTrue(
            ExamQuestionAnalysisBlock.objects.filter(
                question=question,
                source_type=ExamQuestionAnalysisBlock.SOURCE_STUDENT,
                content_md__contains="---\n\n本解析由 考试学生 同学提供\n\n学生给出的代码推导更清楚。",
                is_visible=True,
            ).exists()
        )
        cleared_workbench_response = self.client.get(f"{reverse('teacher-students')}?tab=messages")
        self.assertEqual(cleared_workbench_response.context["page_shell"]["message_count"], 0)
        message = StudentSiteMessage.objects.get(source_suggestion=suggestion)
        self.assertFalse(message.is_read)
        self.assertIn(f"#student-analysis-suggestion-{suggestion.id}", message.target_href)

        self.sign_in(self.student_user)
        updated_student_response = self.client.get(reverse("student-exam-detail", args=[session.id]))
        self.assertContains(updated_student_response, "学生给出的代码推导更清楚")
        self.assertContains(updated_student_response, "本解析由 考试学生 同学提供")
        self.assertContains(updated_student_response, "你的解析挑战记录")
        self.assertContains(updated_student_response, "已采纳")
        self.assertContains(updated_student_response, f'id="student-analysis-suggestion-{suggestion.id}"')

        courses_response = self.client.get(reverse("student-courses"))
        self.assertContains(courses_response, "消息")
        self.assertContains(courses_response, "1 条")
        self.assertContains(courses_response, reverse("student-site-messages"))

        inbox_response = self.client.get(reverse("student-site-messages"))
        self.assertContains(inbox_response, "站内信")
        self.assertContains(inbox_response, "解析挑战已采纳")
        self.assertContains(inbox_response, "查看对应题目")

        open_response = self.client.get(reverse("student-site-message-open", args=[message.id]))
        self.assertEqual(open_response.status_code, 302)
        self.assertIn(f"#student-analysis-suggestion-{suggestion.id}", open_response["Location"])
        message.refresh_from_db()
        self.assertTrue(message.is_read)
        read_courses_response = self.client.get(reverse("student-courses"))
        self.assertEqual(read_courses_response.context["page_shell"]["summary_cards"][3]["value"], "0 条")

    def test_teacher_exam_detail_filters_question_stats_by_student_and_shows_wrong_students(self) -> None:
        session = self.create_exam_via_teacher_view()
        first_question = session.paper.questions.get()
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
        second_user = PortalUser.objects.create(
            username="exam_second_student",
            role=PortalUser.ROLE_STUDENT,
            full_name="第二学生",
            phone="13810000088",
        )
        second_student = Student.objects.create(
            user=second_user,
            teacher_user=self.teacher,
            display_name="第二学生",
            grade="五年级",
            campus="虹桥校区",
            primary_course_name="Python",
            primary_track_name="算法",
            primary_level_name="P1",
        )

        self.sign_in(self.student_user)
        self.client.post(reverse("student-exam-detail", args=[session.id]), {"form_action": "start_exam"})
        self.client.post(
            reverse("student-exam-detail", args=[session.id]),
            {
                "form_action": "submit_exam",
                f"question_{first_question.id}": "B",
                f"question_{second_question.id}": "A",
            },
        )
        second_session = ExamSession.objects.create(
            paper=session.paper,
            student=second_student,
            assigned_by=self.teacher,
            attempt_no=1,
            session_type=ExamSession.SESSION_TYPE_EXAM,
            status=ExamSession.STATUS_AUTO_CHECKED,
            total_count=2,
            correct_count=1,
            wrong_count=1,
            total_score="4.00",
            earned_score="2.00",
            started_at=timezone.now() - timedelta(minutes=20),
            submitted_at=timezone.now() - timedelta(minutes=1),
            checked_at=timezone.now(),
        )
        ExamSubmissionAnswer.objects.create(
            session=second_session,
            question=first_question,
            selected_answer="A",
            is_correct=True,
            score="2.00",
            correct_answer_snapshot="A",
            analysis_snapshot=first_question.analysis,
        )
        ExamSubmissionAnswer.objects.create(
            session=second_session,
            question=second_question,
            selected_answer="D",
            is_correct=False,
            score="0.00",
            correct_answer_snapshot="A",
            analysis_snapshot=second_question.analysis,
        )

        self.sign_in(self.teacher)
        response = self.client.get(reverse("teacher-exam-detail", args=[session.paper_id]))
        self.assertContains(response, "逐题统计筛选")
        self.assertContains(response, "考试学生")
        self.assertContains(response, "第二学生")
        self.assertContains(response, "做错学生")
        self.assertContains(response, "答案 B")
        self.assertContains(response, "答案 D")

        filtered_response = self.client.get(
            reverse("teacher-exam-detail", args=[session.paper_id]) + f"?stats_student_id={second_student.id}"
        )
        self.assertContains(filtered_response, "当前优先展示 第二学生 做错的题目")
        self.assertContains(filtered_response, "该生答案：D，错误")
        content = filtered_response.content.decode("utf-8")
        second_index = content.find('data-question-no="2"')
        first_index = content.find('data-question-no="1"')
        self.assertGreaterEqual(second_index, 0)
        self.assertGreater(first_index, second_index)

    def test_teacher_exam_detail_filters_stats_by_exam_run(self) -> None:
        session = self.create_exam_via_teacher_view()
        first_question = session.paper.questions.get()
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
        second_user = PortalUser.objects.create(
            username="exam_run_second_student",
            role=PortalUser.ROLE_STUDENT,
            full_name="场次学生二",
            phone="13810000089",
        )
        second_student = Student.objects.create(
            user=second_user,
            teacher_user=self.teacher,
            display_name="场次学生二",
            grade="五年级",
            campus="虹桥校区",
            primary_course_name="Python",
            primary_track_name="算法",
            primary_level_name="P1",
        )
        now = timezone.now()
        run_one = ExamRun.objects.create(
            paper=session.paper,
            access_code="111111",
            generated_at=now - timedelta(days=20),
            starts_at=now - timedelta(days=20),
            ends_at=now - timedelta(days=19),
            created_by=self.teacher,
        )
        run_two = ExamRun.objects.create(
            paper=session.paper,
            access_code="222222",
            generated_at=now - timedelta(days=1),
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(hours=1),
            created_by=self.teacher,
        )
        session.exam_run = run_one
        session.status = ExamSession.STATUS_AUTO_CHECKED
        session.total_count = 2
        session.correct_count = 1
        session.wrong_count = 1
        session.total_score = "4.00"
        session.earned_score = "2.00"
        session.started_at = now - timedelta(days=20, minutes=-1)
        session.submitted_at = now - timedelta(days=19, hours=12)
        session.checked_at = session.submitted_at
        session.save()
        run_one_second_session = ExamSession.objects.create(
            paper=session.paper,
            exam_run=run_one,
            student=second_student,
            assigned_by=self.teacher,
            attempt_no=1,
            session_type=ExamSession.SESSION_TYPE_EXAM,
            status=ExamSession.STATUS_AUTO_CHECKED,
            total_count=2,
            correct_count=1,
            wrong_count=1,
            total_score="4.00",
            earned_score="2.00",
            started_at=now - timedelta(days=20),
            submitted_at=now - timedelta(days=19, hours=11),
            checked_at=now - timedelta(days=19, hours=11),
        )
        run_two_session = ExamSession.objects.create(
            paper=session.paper,
            exam_run=run_two,
            student=self.student,
            assigned_by=self.teacher,
            attempt_no=2,
            session_type=ExamSession.SESSION_TYPE_EXAM,
            status=ExamSession.STATUS_AUTO_CHECKED,
            total_count=2,
            correct_count=0,
            wrong_count=2,
            total_score="4.00",
            earned_score="0.00",
            started_at=now - timedelta(hours=3),
            submitted_at=now - timedelta(hours=2),
            checked_at=now - timedelta(hours=2),
        )
        answer_rows = [
            (session, first_question, "A", True),
            (session, second_question, "D", False),
            (run_one_second_session, first_question, "B", False),
            (run_one_second_session, second_question, "A", True),
            (run_two_session, first_question, "C", False),
            (run_two_session, second_question, "D", False),
        ]
        for answer_session, question, selected_answer, is_correct in answer_rows:
            ExamSubmissionAnswer.objects.create(
                session=answer_session,
                question=question,
                selected_answer=selected_answer,
                is_correct=is_correct,
                score=question.score if is_correct else "0.00",
                correct_answer_snapshot=question.correct_answer,
                analysis_snapshot=question.analysis,
            )

        self.sign_in(self.teacher)
        response = self.client.get(
            reverse("teacher-exam-detail", args=[session.paper_id]) + f"?stats_exam_run_id={run_one.id}"
        )
        self.assertContains(response, "考试场次")
        self.assertContains(response, "场次全部")
        self.assertContains(response, "场次 1")
        self.assertContains(response, "口令 111111")
        self.assertEqual(response.context["selected_stats_exam_run_id"], run_one.id)
        self.assertEqual(len(response.context["leaderboard_rows"]), 2)
        self.assertEqual({item["name"] for item in response.context["stats_student_options"]}, {"考试学生", "场次学生二"})
        rows_by_question_no = {row["question_no"]: row for row in response.context["question_rows"]}
        self.assertEqual(rows_by_question_no[1]["correct_count"], 1)
        self.assertEqual(rows_by_question_no[1]["wrong_count"], 1)
        self.assertEqual(rows_by_question_no[2]["correct_count"], 1)
        self.assertEqual(rows_by_question_no[2]["wrong_count"], 1)

        run_two_response = self.client.get(
            reverse("teacher-exam-detail", args=[session.paper_id]) + f"?stats_exam_run_id={run_two.id}"
        )
        self.assertEqual(len(run_two_response.context["leaderboard_rows"]), 1)
        self.assertEqual({item["name"] for item in run_two_response.context["stats_student_options"]}, {"考试学生"})
        run_two_rows = {row["question_no"]: row for row in run_two_response.context["question_rows"]}
        self.assertEqual(run_two_rows[1]["wrong_count"], 1)
        self.assertEqual(run_two_rows[2]["wrong_count"], 1)

    def test_teacher_exam_detail_question_no_sort_handles_natural_labels(self) -> None:
        self.assertEqual(normalize_exam_question_no_for_sort("1."), 1)
        self.assertEqual(normalize_exam_question_no_for_sort("第 2 题"), 2)
        self.assertEqual(normalize_exam_question_no_for_sort("3、"), 3)
        self.assertEqual(normalize_exam_question_no_for_sort("第二题"), 2)
        self.assertEqual(normalize_exam_question_no_for_sort("第十一题"), 11)
        self.assertEqual(normalize_exam_question_no_for_sort("第二十题"), 20)

    def test_proctor_event_does_not_interrupt_student_session(self) -> None:
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
        self.assertEqual(session.switch_count, 0)
        self.assertEqual(session.status, ExamSession.STATUS_IN_PROGRESS)
        self.assertEqual(session.proctor_events.filter(event_type=ExamProctorEvent.EVENT_VISIBILITY_HIDDEN).count(), 1)
        for _ in range(2):
            response = self.client.post(
                reverse("api-student-exam-proctor-event", args=[session.id]),
                data=json.dumps({"event_type": ExamProctorEvent.EVENT_VISIBILITY_HIDDEN}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], ExamSession.STATUS_IN_PROGRESS)
        session.refresh_from_db()
        self.assertEqual(session.switch_count, 0)
        self.assertEqual(session.status, ExamSession.STATUS_IN_PROGRESS)

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
