from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import zipfile
from datetime import timedelta
from unittest.mock import patch

import requests
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from entry.auth import AUTH_COOKIE_NAME, AUTH_COOKIE_SALT
from entry.gesp4_catalog import ARRAY_2D_CONTENT_SLUG, GESP4_TOPIC_DEFINITIONS
from entry.homework_online import (
    HomeworkImportParseError,
    call_external_json_api,
    detect_homework_source_type,
    parse_candidates_with_heuristic,
    parse_candidates_with_qwen,
    parse_homework_import_job,
    sanitize_candidate,
    split_vision_ocr_text_into_blocks,
)
from entry.models import (
    Course,
    CourseCategory,
    CourseContent,
    CourseLevel,
    HomeworkAssignment,
    HomeworkImportJob,
    HomeworkQuestion,
    HomeworkSummary,
    HomeworkSubmission,
    HomeworkSubmissionAnswer,
    PortalUser,
    Student,
    TeacherStudentAssignment,
)


@override_settings(
    HOMEWORK_IMPORT_ROUTER_ENABLED=True,
    HOMEWORK_IMPORT_DEBUG=True,
    HOMEWORK_PARSE_PROVIDER_TEXT="qwen",
    DASHSCOPE_API_KEY="",
    HOMEWORK_LLM_MODEL="qwen-plus",
    HOMEWORK_LLM_API_URL="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    HOMEWORK_LLM_TIMEOUT_SECONDS=40,
    HOMEWORK_SSL_VERIFY=True,
    HOMEWORK_SSL_CA_BUNDLE="",
    HOMEWORK_REQUESTS_USER_AGENT="codemaster-homework-import/1.0",
    HOMEWORK_PARSE_PROVIDER_OCR="volc_vision",
    ARK_API_KEY="",
    VOLC_VISION_MODEL="doubao-seed-1-6-vision-250815",
    VOLC_VISION_API_URL="https://ark.cn-beijing.volces.com/api/v3/responses",
    VOLC_VISION_TIMEOUT_SECONDS=40,
    VOLC_VISION_CONNECT_TIMEOUT_SECONDS=10,
    VOLC_VISION_READ_TIMEOUT_SECONDS=60,
    VOLC_VISION_MAX_RETRIES=2,
    VOLC_VISION_RETRY_BACKOFF_SECONDS=0.0,
    HOMEWORK_PDF_FORCE_OCR=False,
    HOMEWORK_PDF_MIN_TEXT_LENGTH=200,
    HOMEWORK_PDF_MIN_LINE_COUNT=8,
    HOMEWORK_PARSE_REQUIRE_REVIEW=True,
    HOMEWORK_IMPORT_ALLOW_FALLBACK_HEURISTIC=True,
    HOMEWORK_IMAGE_SLICE_HEIGHT_THRESHOLD=2400,
    HOMEWORK_IMAGE_SLICE_OVERLAP=120,
    HOMEWORK_PDF_RASTER_MAX_PAGES=6,
    HOMEWORK_PDF_RASTER_SCALE=2,
    HOMEWORK_VISION_LOCAL_TEXT_PREVIEW_LIMIT=1200,
)
class HomeworkOnlineChoiceTests(TestCase):
    class _DummyResponse:
        def __init__(self, *, status_code: int = 200, text: str = "{}") -> None:
            self.status_code = status_code
            self.text = text

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(response=self)

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="codemaster-homework-media-")
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
            username="teacher_online_choice",
            role=PortalUser.ROLE_TEACHER,
            full_name="在线题老师",
            phone="13800000101",
        )
        self.parent = PortalUser.objects.create(
            username="parent_online_choice",
            role=PortalUser.ROLE_PARENT,
            full_name="在线题家长",
            phone="13800000102",
        )
        self.student_user = PortalUser.objects.create(
            username="student_online_choice",
            role=PortalUser.ROLE_STUDENT,
            full_name="在线题学生",
            phone="13800000103",
        )
        self.student = Student.objects.create(
            user=self.student_user,
            parent_user=self.parent,
            teacher_user=self.teacher,
            display_name="在线题学生",
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
        self.array_content = CourseContent.objects.update_or_create(
            slug=ARRAY_2D_CONTENT_SLUG,
            defaults={
                "course": self.cpp_course,
                "level": self.gesp4_level,
                "content_type": "topic",
                "title": next(item["title"] for item in GESP4_TOPIC_DEFINITIONS if item["slug"] == ARRAY_2D_CONTENT_SLUG),
                "phase": "GESP4",
                "sort_order": 1,
                "route_path": "/student/cpp/gesp/gesp4/array-2d",
                "summary": "二维数组专题",
                "has_real_content": True,
                "is_active": True,
            },
        )[0]
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.student,
            course=self.cpp_course,
            level_code="C4",
            is_active=True,
        )

    def sign_in(self, user: PortalUser) -> None:
        self.client.cookies[AUTH_COOKIE_NAME] = signing.dumps(
            {"username": user.username, "role": user.role},
            salt=AUTH_COOKIE_SALT,
        )

    def create_assignment(self, *, title: str = "在线单选题作业") -> HomeworkAssignment:
        return HomeworkAssignment.objects.create(
            teacher=self.teacher,
            student=self.student,
            content=self.array_content,
            title=title,
            description="先在线完成选择题，再看错题解析。",
            due_date=timezone.localdate() + timedelta(days=3),
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )

    def create_question(
        self,
        assignment: HomeworkAssignment,
        *,
        question_no: int,
        stem: str,
        correct_answer: str,
        options_json: dict[str, str] | None = None,
    ) -> HomeworkQuestion:
        return HomeworkQuestion.objects.create(
            assignment=assignment,
            question_no=question_no,
            question_type=HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem=stem,
            options_json=options_json
            or {
                "A": "选项 A",
                "B": "选项 B",
                "C": "选项 C",
                "D": "选项 D",
            },
            correct_answer=correct_answer,
            analysis=f"{stem} 的解析",
            source_snapshot_json={"source": "test"},
            is_active=True,
        )

    def submit_choice_answers(
        self,
        assignment: HomeworkAssignment,
        answers: dict[int, str],
        *,
        follow: bool = False,
    ):
        payload = {f"question_{question_id}": value for question_id, value in answers.items()}
        return self.client.post(
            reverse("student-homework-practice", args=[assignment.id]),
            payload,
            follow=follow,
        )

    def build_candidate(self, *, stem: str = "候选题一", correct_answer: str = "A") -> dict:
        return {
            "stem": stem,
            "options": {
                "A": "选项 A",
                "B": "选项 B",
                "C": "选项 C",
                "D": "选项 D",
            },
            "correct_answer": correct_answer,
            "analysis": f"{stem} 的解析",
            "notes": "",
            "confidence": 0.92,
        }

    def build_png_bytes(self, *, width: int = 80, height: int = 260) -> bytes:
        from PIL import Image  # type: ignore

        image = Image.new("RGB", (width, height), color=(255, 255, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def build_docx_bytes(self, body_text: str) -> bytes:
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            + "".join(
                f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>"
                for line in body_text.splitlines()
                if line.strip()
            )
            + "</w:body></w:document>"
        )
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

    def build_pdf_bytes(self, *, page_count: int = 3) -> bytes:
        from pypdf import PdfWriter  # type: ignore

        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def create_import_job(
        self,
        assignment: HomeworkAssignment,
        *,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> HomeworkImportJob:
        source_type = detect_homework_source_type(filename)
        self.assertTrue(source_type)
        return HomeworkImportJob.objects.create(
            teacher=self.teacher,
            assignment=assignment,
            source_file=SimpleUploadedFile(filename, content, content_type=content_type),
            source_filename=filename,
            source_sha256=hashlib.sha256(content).hexdigest(),
            source_type=source_type,
            parse_status=HomeworkImportJob.STATUS_UPLOADED,
            is_active=True,
        )

    def upload_html_import(self, assignment: HomeworkAssignment) -> HomeworkImportJob:
        self.sign_in(self.teacher)
        html_content = """
        <html><body>
        <p>1. 下面哪个关键字用于条件判断？</p>
        <p>A. if</p><p>B. for</p><p>C. while</p><p>D. break</p>
        <p>答案：A</p>
        <p>解析：if 用于条件判断。</p>
        <p>2. C++ 中用于标准输出的是？</p>
        <p>A. cin</p><p>B. cout</p><p>C. scanf</p><p>D. break</p>
        <p>答案：B</p>
        <p>解析：cout 负责输出。</p>
        </body></html>
        """.strip()
        response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            {
                "form_action": "upload_choice_file",
                "source_file": SimpleUploadedFile(
                    "choice-homework.html",
                    html_content.encode("utf-8"),
                    content_type="text/html",
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("op=parsed", response["Location"])
        return HomeworkImportJob.objects.get(assignment=assignment)

    def build_confirm_payload(
        self,
        import_job: HomeworkImportJob,
        *,
        included_indexes: set[int] | None = None,
        stem_suffix: str = "（确认版）",
    ) -> dict[str, str]:
        candidates = import_job.candidates_json
        included_indexes = included_indexes if included_indexes is not None else set(range(len(candidates)))
        payload = {
            "form_action": "confirm_import_job",
            "import_job_id": str(import_job.id),
            "candidate_count": str(len(candidates)),
        }
        for index, candidate in enumerate(candidates):
            payload[f"candidate_{index}_included"] = "1" if index in included_indexes else "0"
            payload[f"candidate_{index}_stem"] = candidate["stem"] + stem_suffix
            payload[f"candidate_{index}_option_A"] = candidate["options"]["A"] or "A"
            payload[f"candidate_{index}_option_B"] = candidate["options"]["B"] or "B"
            payload[f"candidate_{index}_option_C"] = candidate["options"]["C"] or "C"
            payload[f"candidate_{index}_option_D"] = candidate["options"]["D"] or "D"
            payload[f"candidate_{index}_correct_answer"] = candidate["correct_answer"] or "A"
            payload[f"candidate_{index}_analysis"] = candidate["analysis"] or "老师补的解析"
            payload[f"candidate_{index}_notes"] = candidate.get("notes", "")
        return payload

    def test_teacher_detail_shows_question_builder_entry(self) -> None:
        assignment = self.create_assignment()
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-student-detail", args=[self.student.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "上传生成选择题")
        self.assertContains(response, reverse("teacher-homework-builder", args=[self.student.id, assignment.id]))

    @override_settings(DASHSCOPE_API_KEY="test-qwen-key")
    def test_qwen_prompt_mentions_extended_answer_and_analysis_markers(self) -> None:
        with patch(
            "entry.homework_online.call_external_json_api",
            return_value=(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "questions": [
                                            self.build_candidate(stem="二维数组题", correct_answer="B")
                                        ],
                                        "notes": "ok",
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
                "request note",
            ),
        ) as mock_call:
            candidates, _notes = parse_candidates_with_qwen(
                "1. 二维数组题\nA. 甲\nB. 乙\n参考答案：B\n解析：测试解析",
                source_type=HomeworkImportJob.SOURCE_TYPE_TEXT,
                source_origin="unit-test",
            )

        self.assertEqual(candidates[0]["correct_answer"], "B")
        payload = mock_call.call_args.kwargs["payload"]
        prompt_text = "\n".join(message["content"] for message in payload["messages"])
        self.assertIn("参考答案", prompt_text)
        self.assertIn("标准答案", prompt_text)
        self.assertIn("Correct Answer", prompt_text)
        self.assertIn("Explanation", prompt_text)

    def test_heuristic_parser_recognizes_extended_answer_and_analysis_markers(self) -> None:
        scenarios = [
            ("答案：A", "A"),
            ("参考答案：B", "B"),
            ("正确答案：C", "C"),
            ("标准答案：D", "D"),
            ("【参考答案】A", "A"),
            ("Answer: B", "B"),
            ("Correct Answer: C", "C"),
        ]
        for answer_line, expected_answer in scenarios:
            with self.subTest(answer_line=answer_line):
                source_text = (
                    "1. 二维数组中哪个写法合法？\n"
                    "A. arr[i][j]\n"
                    "B. arr[i,j]\n"
                    "C. arr(i)(j)\n"
                    "D. arr<i><j>\n"
                    f"{answer_line}\n"
                    "答案解析：二维数组使用双下标。\n"
                )
                candidates, _notes = parse_candidates_with_heuristic(source_text)
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["correct_answer"], expected_answer)
                self.assertEqual(candidates[0]["analysis"], "二维数组使用双下标。")
                self.assertNotIn("参考答案", candidates[0]["stem"])

    def test_heuristic_parser_maps_option_text_answer_to_letter_and_splits_inline_analysis(self) -> None:
        source_text = (
            "1. 哪个概念最符合 for 的特点？\n"
            "A. 条件判断\n"
            "B. 循环结构\n"
            "C. 输入函数\n"
            "D. 文件系统\n"
            "参考答案：循环结构。解析：for 常用于循环结构。\n"
        )

        candidates, _notes = parse_candidates_with_heuristic(source_text)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["correct_answer"], "B")
        self.assertEqual(candidates[0]["analysis"], "for 常用于循环结构。")
        self.assertNotIn("参考答案", candidates[0]["stem"])

    def test_sanitize_candidate_maps_labeled_answer_and_inline_analysis(self) -> None:
        candidate = sanitize_candidate(
            {
                "stem": "二维数组中哪个表达式表示第 i 行第 j 列？\n参考答案：B",
                "options": {
                    "A": "arr(i,j)",
                    "B": "arr[i][j]",
                    "C": "arr{i}{j}",
                    "D": "arr<i><j>",
                },
                "correct_answer": "参考答案：arr[i][j] 解析：标准二维数组下标写法。",
                "analysis": "",
            },
            index=1,
        )

        self.assertEqual(candidate["correct_answer"], "B")
        self.assertEqual(candidate["analysis"], "标准二维数组下标写法。")
        self.assertNotIn("参考答案", candidate["stem"])

    def test_call_external_json_api_classifies_ssl_error(self) -> None:
        with patch(
            "entry.homework_online.requests.Session.post",
            side_effect=requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"),
        ):
            with self.assertRaises(HomeworkImportParseError) as captured:
                call_external_json_api(
                    provider_label="qwen-plus",
                    url="https://example.com/api",
                    headers={"Authorization": "Bearer test"},
                    payload={"model": "qwen-plus"},
                    timeout_seconds=5,
                )
        self.assertIn("SSL 握手失败", str(captured.exception))
        self.assertEqual(captured.exception.error_code, "ssl_handshake_failed")
        self.assertEqual(captured.exception.failure_type, "SSL 握手失败")

    def test_call_external_json_api_classifies_connect_read_timeout_and_empty_response(self) -> None:
        with patch(
            "entry.homework_online.requests.Session.post",
            side_effect=requests.exceptions.ConnectTimeout("connect deadline"),
        ):
            with self.assertRaises(HomeworkImportParseError) as captured_connect:
                call_external_json_api(
                    provider_label="qwen-plus",
                    url="https://example.com/api",
                    headers={"Authorization": "Bearer test"},
                    payload={"model": "qwen-plus"},
                    timeout_seconds=5,
                )
        self.assertIn("connect timeout", str(captured_connect.exception))
        self.assertEqual(captured_connect.exception.error_code, "connect_timeout")
        self.assertEqual(captured_connect.exception.failure_type, "connect timeout")

        with patch(
            "entry.homework_online.requests.Session.post",
            side_effect=requests.exceptions.ReadTimeout("read deadline"),
        ):
            with self.assertRaises(HomeworkImportParseError) as captured_read:
                call_external_json_api(
                    provider_label="qwen-plus",
                    url="https://example.com/api",
                    headers={"Authorization": "Bearer test"},
                    payload={"model": "qwen-plus"},
                    timeout_seconds=5,
                )
        self.assertIn("read timeout", str(captured_read.exception))
        self.assertEqual(captured_read.exception.error_code, "read_timeout")
        self.assertEqual(captured_read.exception.failure_type, "read timeout")

        with patch(
            "entry.homework_online.requests.Session.post",
            return_value=self._DummyResponse(status_code=200, text=""),
        ):
            with self.assertRaises(HomeworkImportParseError) as captured_empty:
                call_external_json_api(
                    provider_label="qwen-plus",
                    url="https://example.com/api",
                    headers={"Authorization": "Bearer test"},
                    payload={"model": "qwen-plus"},
                    timeout_seconds=5,
                )
        self.assertIn("空响应", str(captured_empty.exception))
        self.assertEqual(captured_empty.exception.error_code, "empty_response")
        self.assertEqual(captured_empty.exception.failure_type, "空响应")

    def test_call_external_json_api_classifies_http_status_and_invalid_json(self) -> None:
        with patch(
            "entry.homework_online.requests.Session.post",
            return_value=self._DummyResponse(status_code=502, text="bad gateway"),
        ):
            with self.assertRaisesMessage(HomeworkImportParseError, "HTTP 状态异常（502）"):
                call_external_json_api(
                    provider_label="qwen-plus",
                    url="https://example.com/api",
                    headers={"Authorization": "Bearer test"},
                    payload={"model": "qwen-plus"},
                    timeout_seconds=5,
                )

        with patch(
            "entry.homework_online.requests.Session.post",
            return_value=self._DummyResponse(status_code=200, text="not-json"),
        ):
            with self.assertRaisesMessage(HomeworkImportParseError, "JSON 解析失败"):
                call_external_json_api(
                    provider_label="qwen-plus",
                    url="https://example.com/api",
                    headers={"Authorization": "Bearer test"},
                    payload={"model": "qwen-plus"},
                    timeout_seconds=5,
                )

    @override_settings(DASHSCOPE_API_KEY="test-qwen-key")
    def test_parse_notes_report_qwen_ssl_failure_and_fallback(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="ssl-fallback.html",
            content=(
                "<html><body><p>1. 下面哪个关键字用于条件判断？</p><p>A. if</p><p>B. for</p>"
                "<p>C. while</p><p>D. break</p><p>答案：A</p></body></html>"
            ).encode("utf-8"),
            content_type="text/html",
        )

        with patch(
            "entry.homework_online.call_external_json_api",
            side_effect=HomeworkImportParseError("SSL 失败：CERTIFICATE_VERIFY_FAILED"),
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertIn("qwen-plus：是，失败（SSL 失败：CERTIFICATE_VERIFY_FAILED）", import_job.parse_notes)
        self.assertIn("fallback heuristic：是", import_job.parse_notes)

    @override_settings(ARK_API_KEY="test-ark-key")
    def test_parse_notes_report_vision_ssl_failure(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="ssl-image.png",
            content=self.build_png_bytes(width=32, height=32),
            content_type="image/png",
        )

        with patch(
            "entry.homework_online.call_external_json_api",
            side_effect=HomeworkImportParseError(
                "SSL 握手失败：CERTIFICATE_VERIFY_FAILED",
                error_code="ssl_handshake_failed",
                failure_type="SSL 握手失败",
                retryable=False,
            ),
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_FAILED)
        self.assertIn("火山视觉：是，失败", import_job.parse_notes)
        self.assertIn("视觉最终失败类型：SSL 握手失败", import_job.parse_notes)
        self.assertIn("页面提示：视觉识别失败（SSL 握手失败），未生成候选题，请稍后重试。", import_job.parse_notes)
        self.assertIn("失败步骤：火山视觉", import_job.parse_notes)

    def test_html_document_uses_local_text_then_qwen(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="router.html",
            content=(
                "<html><body><p>1. 条件判断关键字是？</p><p>A. if</p><p>B. for</p>"
                "<p>C. while</p><p>D. break</p><p>答案：A</p></body></html>"
            ).encode("utf-8"),
            content_type="text/html",
        )

        with (
            patch("entry.homework_online.parse_candidates_with_qwen", return_value=([self.build_candidate(stem="HTML 路由题")], "Qwen HTML")),
            patch("entry.homework_online.extract_text_with_volc_vision") as mock_vision,
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertEqual(len(import_job.candidates_json), 1)
        self.assertFalse(mock_vision.called)
        self.assertIn("路由：html_local_text_to_qwen", import_job.parse_notes)
        self.assertIn("本地抽文本：是", import_job.parse_notes)
        self.assertIn("火山视觉：否", import_job.parse_notes)
        self.assertIn("qwen-plus：是，成功", import_job.parse_notes)

    def test_docx_document_uses_local_text_then_qwen(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="router.docx",
            content=self.build_docx_bytes(
                "1. C++ 输出使用哪个对象？\nA. cin\nB. cout\nC. scanf\nD. break\n答案：B"
            ),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        with (
            patch("entry.homework_online.parse_candidates_with_qwen", return_value=([self.build_candidate(stem="DOCX 路由题", correct_answer="B")], "Qwen DOCX")),
            patch("entry.homework_online.extract_text_with_volc_vision") as mock_vision,
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertFalse(mock_vision.called)
        self.assertIn("路由：docx_local_text_to_qwen", import_job.parse_notes)
        self.assertIn("qwen-plus：是，成功", import_job.parse_notes)

    def test_pdf_with_enough_text_skips_vision_route(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="enough-text.pdf",
            content=b"%PDF-1.4 enough-text",
            content_type="application/pdf",
        )
        rich_text = "\n".join([f"第{i}行 这是足够长的 PDF 文本内容，用来满足长度和行数判断。答案 A 解析说明。" for i in range(1, 16)])

        with (
            patch("entry.homework_online.extract_local_source_text", return_value=rich_text),
            patch("entry.homework_online.parse_candidates_with_qwen", return_value=([self.build_candidate(stem="PDF 本地题")], "Qwen PDF")),
            patch("entry.homework_online.extract_text_with_volc_vision") as mock_vision,
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertFalse(mock_vision.called)
        self.assertIn("路由：pdf_local_text_to_qwen", import_job.parse_notes)
        self.assertIn("火山视觉：否", import_job.parse_notes)

    def test_pdf_with_low_text_enters_vision_route(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="low-text.pdf",
            content=b"%PDF-1.4 low-text",
            content_type="application/pdf",
        )

        with (
            patch("entry.homework_online.extract_local_source_text", return_value="第1题 模糊内容"),
            patch("entry.homework_online.extract_text_with_volc_vision", return_value=("1. 视觉提取题\nA. 1\nB. 2\nC. 3\nD. 4\n答案：A", "Volc Vision OCR")),
            patch("entry.homework_online.parse_candidates_with_qwen", return_value=([self.build_candidate(stem="PDF 视觉题")], "Qwen Vision")),
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertIn("路由：pdf_low_text_to_volc_vision_to_qwen", import_job.parse_notes)
        self.assertIn("本地抽文本：是", import_job.parse_notes)
        self.assertIn("火山视觉：是，成功", import_job.parse_notes)
        self.assertIn("qwen-plus：是，成功", import_job.parse_notes)

    def test_image_enters_vision_route(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="choice.png",
            content=b"\x89PNG\r\n\x1a\nfake",
            content_type="image/png",
        )

        with (
            patch("entry.homework_online.extract_text_with_volc_vision", return_value=("1. 图片题\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n答案：A", "Volc Vision Image")),
            patch("entry.homework_online.parse_candidates_with_qwen", return_value=([self.build_candidate(stem="图片视觉题")], "Qwen Image")),
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertIn("路由：image_to_volc_vision_to_qwen", import_job.parse_notes)
        self.assertIn("本地抽文本：否", import_job.parse_notes)
        self.assertIn("火山视觉：是，成功", import_job.parse_notes)

    def test_split_vision_ocr_text_into_blocks_preserves_question_boundaries(self) -> None:
        ocr_text = "\n".join(
            [
                "第1题 下列哪个关键字用于循环？",
                "A. if",
                "B. for",
                "C. break",
                "D. return",
                "答案：B",
                "第2题 哪个对象用于标准输出？",
                "A. cin",
                "B. cout",
                "C. scanf",
                "D. getchar",
                "答案：B",
            ]
        )

        blocks = split_vision_ocr_text_into_blocks(ocr_text)

        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0].startswith("第1题"))
        self.assertTrue(blocks[1].startswith("第2题"))

    @override_settings(ARK_API_KEY="test-ark-key")
    def test_visual_qwen_failure_does_not_fallback_to_heuristic(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="vision-fail.png",
            content=self.build_png_bytes(width=80, height=80),
            content_type="image/png",
        )

        with (
            patch(
                "entry.homework_online.extract_text_with_volc_vision",
                return_value=(
                    "第1题 条件判断关键字是？\nA. if\nB. for\nC. while\nD. break\n答案：A",
                    "vision-note",
                ),
            ),
            patch(
                "entry.homework_online.parse_candidates_with_qwen",
                side_effect=HomeworkImportParseError("SSL 失败：CERTIFICATE_VERIFY_FAILED"),
            ),
            patch("entry.homework_online.parse_candidates_with_heuristic") as mock_heuristic,
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertEqual(import_job.candidates_json, [])
        self.assertFalse(mock_heuristic.called)
        self.assertIn("题块切分：否，按 1 个 block 结构化", import_job.parse_notes)
        self.assertIn("block1: 长度", import_job.parse_notes)
        self.assertIn("qwen 失败：SSL 失败：CERTIFICATE_VERIFY_FAILED", import_job.parse_notes)
        self.assertIn("视觉链路下 qwen 失败，已停止 heuristic 自动产题。", import_job.parse_notes)
        self.assertIn("已完成 OCR，但结构化失败，请人工确认。", import_job.parse_notes)
        self.assertIn("最终候选题数量：0 道", import_job.parse_notes)

    @override_settings(ARK_API_KEY="test-ark-key")
    def test_visual_blocks_are_structured_individually_without_merging_into_one_question(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="multi-question.png",
            content=self.build_png_bytes(width=120, height=120),
            content_type="image/png",
        )
        ocr_text = "\n".join(
            [
                "第1题 下列哪个关键字用于循环？",
                "A. if",
                "B. for",
                "C. break",
                "D. return",
                "答案：B",
                "第2题 哪个对象用于标准输出？",
                "A. cin",
                "B. cout",
                "C. scanf",
                "D. getchar",
                "答案：B",
            ]
        )
        qwen_results = [
            ([self.build_candidate(stem="第一题候选", correct_answer="B")], "block1"),
            ([self.build_candidate(stem="第二题候选", correct_answer="B")], "block2"),
        ]

        with (
            patch("entry.homework_online.extract_text_with_volc_vision", return_value=(ocr_text, "vision-note")),
            patch("entry.homework_online.parse_candidates_with_qwen", side_effect=qwen_results) as mock_qwen,
            patch("entry.homework_online.parse_candidates_with_heuristic") as mock_heuristic,
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertEqual(len(import_job.candidates_json), 2)
        self.assertEqual(mock_qwen.call_count, 2)
        self.assertFalse(mock_heuristic.called)
        self.assertIn("题块切分：是，切出 2 个 block", import_job.parse_notes)
        self.assertIn("block1: 长度", import_job.parse_notes)
        self.assertIn("block2: 长度", import_job.parse_notes)
        self.assertIn("最终候选题数量：2 道", import_job.parse_notes)

    def test_missing_qwen_falls_back_to_heuristic_with_clear_notes(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="fallback.html",
            content=(
                "<html><body><p>1. 下面哪个关键字用于条件判断？</p><p>A. if</p><p>B. for</p>"
                "<p>C. while</p><p>D. break</p><p>答案：A</p>"
                "<p>2. 标准输出对象是？</p><p>A. cin</p><p>B. cout</p><p>C. scanf</p><p>D. break</p><p>答案：B</p></body></html>"
            ).encode("utf-8"),
            content_type="text/html",
        )

        parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertGreaterEqual(len(import_job.candidates_json), 2)
        self.assertIn("qwen-plus：是，失败", import_job.parse_notes)
        self.assertIn("fallback heuristic：是", import_job.parse_notes)

    def test_missing_volc_vision_produces_clear_error_notes(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="missing-vision.png",
            content=self.build_png_bytes(width=36, height=36),
            content_type="image/png",
        )

        parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_FAILED)
        self.assertIn("火山视觉：是，失败", import_job.parse_notes)
        self.assertIn("ARK_API_KEY", import_job.parse_notes)
        self.assertIn("失败步骤：火山视觉", import_job.parse_notes)

    def test_parse_notes_reflect_route_diagnostics(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="diagnostic.pdf",
            content=b"%PDF-1.4 diagnostics",
            content_type="application/pdf",
        )

        with (
            patch("entry.homework_online.extract_local_source_text", return_value="短文本"),
            patch("entry.homework_online.extract_text_with_volc_vision", return_value=("1. 诊断题\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n答案：A", "Vision 诊断")),
            patch("entry.homework_online.parse_candidates_with_qwen", return_value=([self.build_candidate(stem="诊断题")], "Qwen 诊断")),
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertIn("文件类型：PDF", import_job.parse_notes)
        self.assertIn("本地抽文本：是", import_job.parse_notes)
        self.assertIn("火山视觉：是，成功", import_job.parse_notes)
        self.assertIn("qwen-plus：是，成功", import_job.parse_notes)
        self.assertIn("fallback heuristic：否", import_job.parse_notes)
        self.assertIn("最终候选题数量：1 道", import_job.parse_notes)

    def test_teacher_upload_html_creates_import_job(self) -> None:
        assignment = self.create_assignment()

        import_job = self.upload_html_import(assignment)

        self.assertEqual(import_job.source_type, HomeworkImportJob.SOURCE_TYPE_HTML)
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertGreaterEqual(len(import_job.candidates_json), 2)
        self.assertTrue(import_job.source_file.name.startswith("homework_imports/"))

    def test_teacher_builder_page_renders_upload_progress_and_double_submit_guards(self) -> None:
        assignment = self.create_assignment()
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-homework-builder", args=[self.student.id, assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-upload-form", html=False)
        self.assertContains(response, "data-upload-progress", html=False)
        self.assertContains(response, "上传中…")
        self.assertContains(response, "识别中…")

    def test_teacher_builder_shows_manual_review_notice_when_visual_chain_has_no_candidates(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="visual-empty.png",
            content=self.build_png_bytes(width=80, height=80),
            content_type="image/png",
        )
        import_job.parse_status = HomeworkImportJob.STATUS_PARSED
        import_job.candidates_json = []
        import_job.parse_notes = "\n".join(
            [
                "视觉链路：是",
                "OCR 文本预览：第1题 条件判断关键字是？ A. if B. for",
                "已完成 OCR，但结构化失败，请人工确认。",
                "最终候选题数量：0 道",
            ]
        )
        import_job.save(update_fields=["parse_status", "candidates_json", "parse_notes", "updated_at"])
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-homework-builder", args=[self.student.id, assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "未生成候选题")
        self.assertContains(response, "已完成 OCR，但结构化失败，请人工确认。")
        self.assertContains(response, "OCR 文本预览")
        self.assertContains(response, "候选题为空")

    def test_upload_is_blocked_when_pending_import_job_exists(self) -> None:
        assignment = self.create_assignment()
        pending_job = self.create_import_job(
            assignment,
            filename="pending.html",
            content=b"<html><body><p>pending</p></body></html>",
            content_type="text/html",
        )
        pending_job.parse_status = HomeworkImportJob.STATUS_PARSING
        pending_job.save(update_fields=["parse_status", "updated_at"])
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            {
                "form_action": "upload_choice_file",
                "source_file": SimpleUploadedFile(
                    "choice-homework.html",
                    "<html><body><p>1. 新题</p><p>A. A</p><p>B. B</p><p>C. C</p><p>D. D</p><p>答案：A</p></body></html>".encode("utf-8"),
                    content_type="text/html",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前作业已有导入任务正在上传或识别中，请等待完成后再试。")
        self.assertEqual(HomeworkImportJob.objects.filter(assignment=assignment).count(), 1)

    def test_confirm_import_job_writes_homework_questions(self) -> None:
        assignment = self.create_assignment()
        import_job = self.upload_html_import(assignment)
        self.sign_in(self.teacher)
        payload = self.build_confirm_payload(import_job)

        response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            payload,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("op=confirmed", response["Location"])
        candidates = import_job.candidates_json
        questions = list(assignment.questions.filter(is_active=True).order_by("question_no"))
        self.assertEqual(len(questions), len(candidates))
        self.assertTrue(questions[0].stem.endswith("（确认版）"))
        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_CONFIRMED)
        self.assertIsNotNone(import_job.confirmed_at)

    def test_confirm_import_job_success_feedback_is_visible(self) -> None:
        assignment = self.create_assignment()
        import_job = self.upload_html_import(assignment)
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            self.build_confirm_payload(import_job, included_indexes={0, 1}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "确认成功，已写入 2 道正式题目。")
        self.assertContains(response, "当前正式题目")
        self.assertEqual(assignment.questions.filter(is_active=True).count(), 2)

    def test_confirm_import_job_logs_payload_and_written_counts(self) -> None:
        assignment = self.create_assignment()
        import_job = self.upload_html_import(assignment)
        self.sign_in(self.teacher)

        with self.assertLogs("entry.views", level="INFO") as captured_logs:
            response = self.client.post(
                reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
                self.build_confirm_payload(import_job, included_indexes={0, 1}),
            )

        self.assertEqual(response.status_code, 302)
        combined_logs = "\n".join(captured_logs.output)
        self.assertIn("payload_count=2", combined_logs)
        self.assertIn("included_count=2", combined_logs)
        self.assertIn("written_count=2", combined_logs)

    def test_confirm_import_job_replaces_existing_active_questions(self) -> None:
        assignment = self.create_assignment()
        old_question = self.create_question(assignment, question_no=1, stem="旧正式题", correct_answer="D")
        import_job = self.upload_html_import(assignment)
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            self.build_confirm_payload(import_job, included_indexes={0}, stem_suffix="（新版）"),
        )

        self.assertEqual(response.status_code, 302)
        active_questions = list(assignment.questions.filter(is_active=True).order_by("question_no"))
        self.assertEqual(len(active_questions), 1)
        self.assertEqual(active_questions[0].question_no, 1)
        self.assertTrue(active_questions[0].stem.endswith("（新版）"))
        self.assertEqual(active_questions[0].import_job_id, import_job.id)
        old_question.refresh_from_db()
        self.assertFalse(old_question.is_active)

    def test_confirm_import_job_allows_completed_and_reviewed_without_submission_and_resets_assignment(self) -> None:
        for status in [HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED]:
            with self.subTest(status=status):
                assignment = self.create_assignment(title=f"{status} 作业")
                assignment.status = status
                assignment.completed_at = timezone.now()
                assignment.reviewed_at = timezone.now() if status == HomeworkAssignment.STATUS_REVIEWED else None
                assignment.teacher_comment = "旧评语"
                assignment.save(update_fields=["status", "completed_at", "reviewed_at", "teacher_comment", "updated_at"])
                import_job = self.upload_html_import(assignment)
                self.sign_in(self.teacher)

                response = self.client.post(
                    reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
                    self.build_confirm_payload(import_job, included_indexes={0}, stem_suffix="（重置版）"),
                )

                self.assertEqual(response.status_code, 302)
                self.assertIn("op=confirmed", response["Location"])
                assignment.refresh_from_db()
                self.assertEqual(assignment.status, HomeworkAssignment.STATUS_ASSIGNED)
                self.assertIsNone(assignment.completed_at)
                self.assertIsNone(assignment.reviewed_at)
                self.assertEqual(assignment.teacher_comment, "")

    def test_confirm_import_job_is_blocked_after_submission_exists(self) -> None:
        assignment = self.create_assignment()
        question = self.create_question(assignment, question_no=1, stem="先提交的题", correct_answer="A")
        self.sign_in(self.student_user)
        self.submit_choice_answers(assignment, {question.id: "A"})
        import_job = self.upload_html_import(assignment)
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            self.build_confirm_payload(import_job, included_indexes={0}, stem_suffix="（覆盖失败）"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前作业已经有学生提交记录，不能再覆盖正式题目。")
        active_questions = list(assignment.questions.filter(is_active=True).order_by("question_no"))
        self.assertEqual(len(active_questions), 1)
        self.assertEqual(active_questions[0].id, question.id)
        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)

    def test_duplicate_upload_same_content_shows_hint_but_is_not_blocked(self) -> None:
        assignment = self.create_assignment()
        self.sign_in(self.teacher)
        html_content = (
            "<html><body><p>1. 条件判断关键字是？</p><p>A. if</p><p>B. for</p>"
            "<p>C. while</p><p>D. break</p><p>答案：A</p></body></html>"
        ).encode("utf-8")

        first_response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            {
                "form_action": "upload_choice_file",
                "source_file": SimpleUploadedFile("first-upload.html", html_content, content_type="text/html"),
            },
        )
        second_response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            {
                "form_action": "upload_choice_file",
                "source_file": SimpleUploadedFile("second-upload.html", html_content, content_type="text/html"),
            },
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        latest_job, previous_job = list(HomeworkImportJob.objects.filter(assignment=assignment).order_by("-created_at", "-id"))[:2]
        self.assertEqual(latest_job.source_sha256, previous_job.source_sha256)
        self.assertIn("重复内容提示：是", latest_job.parse_notes)
        self.assertIn(f"#{previous_job.id}", latest_job.parse_notes)

    @override_settings(
        ARK_API_KEY="test-ark-key",
        HOMEWORK_IMAGE_SLICE_HEIGHT_THRESHOLD=100,
        HOMEWORK_IMAGE_SLICE_OVERLAP=10,
        VOLC_VISION_MAX_RETRIES=2,
        VOLC_VISION_RETRY_BACKOFF_SECONDS=0.0,
    )
    def test_long_image_is_sliced_before_ocr(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="long-image.png",
            content=self.build_png_bytes(height=260),
            content_type="image/png",
        )

        with (
            patch(
                "entry.homework_online.call_external_json_api",
                side_effect=[
                    HomeworkImportParseError(
                        "read timeout：first slice slow",
                        error_code="read_timeout",
                        failure_type="read timeout",
                        retryable=True,
                    ),
                    ({"output_text": "第1片文本"}, "vision-1"),
                    ({"output_text": "第2片文本"}, "vision-2"),
                    ({"output_text": "第3片文本"}, "vision-3"),
                ],
            ) as mock_vision_api,
            patch("entry.homework_online.parse_candidates_with_qwen", return_value=([self.build_candidate(stem="长图切片题")], "Qwen Long Image")),
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertEqual(mock_vision_api.call_count, 4)
        self.assertIn("图片切片：是，3 片", import_job.parse_notes)
        self.assertIn("切片结果：第1片成功；第2片成功；第3片成功", import_job.parse_notes)
        self.assertIn("第1片: 第1次请求失败（read timeout）", import_job.parse_notes)
        self.assertIn("第1片: 第2次请求成功", import_job.parse_notes)

    @override_settings(
        ARK_API_KEY="test-ark-key",
        HOMEWORK_IMAGE_SLICE_HEIGHT_THRESHOLD=100,
        HOMEWORK_IMAGE_SLICE_OVERLAP=10,
        VOLC_VISION_MAX_RETRIES=2,
        VOLC_VISION_RETRY_BACKOFF_SECONDS=0.0,
    )
    def test_visual_timeout_page_message_is_precise_and_no_ssl_mislabel(self) -> None:
        assignment = self.create_assignment()
        self.sign_in(self.teacher)

        def raise_read_timeout(*args, **kwargs):
            raise HomeworkImportParseError(
                "read timeout：vision service slow",
                error_code="read_timeout",
                failure_type="read timeout",
                retryable=True,
            )

        with patch("entry.homework_online.call_external_json_api", side_effect=raise_read_timeout):
            response = self.client.post(
                reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
                {
                    "form_action": "upload_choice_file",
                    "source_file": SimpleUploadedFile(
                        "timeout-image.png",
                        self.build_png_bytes(width=80, height=180),
                        content_type="image/png",
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "视觉识别超时，未生成候选题，请稍后重试。")
        self.assertNotContains(response, "SSL 配置错误")
        self.assertNotContains(response, "SSL 握手失败")

        import_job = HomeworkImportJob.objects.get(assignment=assignment)
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_FAILED)
        self.assertIn("全部片段超时中止：是", import_job.parse_notes)
        self.assertIn("视觉最终失败类型：全部片段超时", import_job.parse_notes)
        self.assertIn("第1片: 第1次请求失败（read timeout）", import_job.parse_notes)
        self.assertIn("第2片: 第3次请求失败（read timeout）", import_job.parse_notes)
        self.assertIn("页面提示：视觉识别超时，未生成候选题，请稍后重试。", import_job.parse_notes)

    @override_settings(ARK_API_KEY="test-ark-key", HOMEWORK_PDF_RASTER_MAX_PAGES=2)
    def test_pdf_without_text_uses_page_rasterization(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="rasterized.pdf",
            content=self.build_pdf_bytes(page_count=3),
            content_type="application/pdf",
        )

        with (
            patch("entry.homework_online.extract_local_source_text", return_value=""),
            patch(
                "entry.homework_online.call_external_json_api",
                side_effect=[
                    ({"output_text": "第1题 PDF 第一页题目\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n答案：A"}, "vision-page-1"),
                    ({"output_text": "第2题 PDF 第二页题目\nA. 一\nB. 二\nC. 三\nD. 四\n答案：B"}, "vision-page-2"),
                ],
            ) as mock_vision_api,
            patch("entry.homework_online.parse_candidates_with_qwen", return_value=([self.build_candidate(stem="PDF 光栅题")], "Qwen Raster")),
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertEqual(mock_vision_api.call_count, 2)
        self.assertIn("视觉链路：是", import_job.parse_notes)
        self.assertIn("PDF 光栅化：是，处理 2/3 页（已命中页数上限）", import_job.parse_notes)
        self.assertIn("第1页渲染成功", import_job.parse_notes)
        self.assertIn("第2页渲染成功", import_job.parse_notes)
        self.assertIn("第1页继续切片：否，1 片", import_job.parse_notes)
        self.assertIn("第2页继续切片：否，1 片", import_job.parse_notes)
        self.assertIn("第1页: 第1次请求成功", import_job.parse_notes)
        self.assertIn("第2页: 第1次请求成功", import_job.parse_notes)
        self.assertIn("图片切片：否", import_job.parse_notes)

    @override_settings(ARK_API_KEY="test-ark-key")
    def test_pdf_missing_pypdfium2_shows_clear_failure_reason(self) -> None:
        assignment = self.create_assignment()
        self.sign_in(self.teacher)

        with (
            patch("entry.homework_online.extract_local_source_text", return_value=""),
            patch.dict("sys.modules", {"pypdfium2": None}),
        ):
            response = self.client.post(
                reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
                {
                    "form_action": "upload_choice_file",
                    "source_file": SimpleUploadedFile(
                        "missing-pdfium.pdf",
                        self.build_pdf_bytes(page_count=1),
                        content_type="application/pdf",
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前环境缺少 PDF 光栅化依赖，未生成候选题。")
        import_job = HomeworkImportJob.objects.get(assignment=assignment)
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_FAILED)
        self.assertIn("PDF 光栅化：是，失败（pdf_render_unavailable）", import_job.parse_notes)
        self.assertIn("视觉最终失败原因：pdf_render_unavailable", import_job.parse_notes)
        self.assertIn("页面提示：当前环境缺少 PDF 光栅化依赖，未生成候选题。", import_job.parse_notes)

    @override_settings(ARK_API_KEY="test-ark-key")
    def test_pdf_render_failure_shows_clear_page_message(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="broken-render.pdf",
            content=self.build_pdf_bytes(page_count=1),
            content_type="application/pdf",
        )

        class _BrokenPdfium:
            class PdfDocument:
                def __init__(self, file_path):
                    raise RuntimeError("renderer boot failed")

        with (
            patch("entry.homework_online.extract_local_source_text", return_value=""),
            patch.dict("sys.modules", {"pypdfium2": _BrokenPdfium}),
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_FAILED)
        self.assertIn("PDF 光栅化：是，失败（pdf_render_failed）", import_job.parse_notes)
        self.assertIn("视觉最终失败原因：pdf_render_failed", import_job.parse_notes)
        self.assertIn("页面提示：PDF 页面渲染失败，未生成候选题，请稍后重试。", import_job.parse_notes)

    @override_settings(ARK_API_KEY="test-ark-key", HOMEWORK_PDF_RASTER_MAX_PAGES=2)
    def test_pdf_visual_qwen_failure_does_not_fallback_to_heuristic(self) -> None:
        assignment = self.create_assignment()
        import_job = self.create_import_job(
            assignment,
            filename="pdf-qwen-fail.pdf",
            content=self.build_pdf_bytes(page_count=2),
            content_type="application/pdf",
        )

        with (
            patch("entry.homework_online.extract_local_source_text", return_value=""),
            patch(
                "entry.homework_online.call_external_json_api",
                side_effect=[
                    ({"output_text": "第1题 PDF 第一页题目\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n答案：A"}, "vision-page-1"),
                    ({"output_text": "第2题 PDF 第二页题目\nA. 一\nB. 二\nC. 三\nD. 四\n答案：B"}, "vision-page-2"),
                ],
            ),
            patch(
                "entry.homework_online.parse_candidates_with_qwen",
                side_effect=HomeworkImportParseError("read timeout：qwen slow", error_code="read_timeout", failure_type="read timeout", retryable=True),
            ),
            patch("entry.homework_online.parse_candidates_with_heuristic") as mock_heuristic,
        ):
            parse_homework_import_job(import_job)

        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        self.assertEqual(import_job.candidates_json, [])
        self.assertFalse(mock_heuristic.called)
        self.assertIn("PDF 光栅化：是，处理 2/2 页", import_job.parse_notes)
        self.assertIn("第1页渲染成功", import_job.parse_notes)
        self.assertIn("视觉链路下 qwen 失败，已停止 heuristic 自动产题。", import_job.parse_notes)
        self.assertIn("已完成 OCR，但结构化失败，请人工确认。", import_job.parse_notes)

    def test_confirm_import_job_rejects_empty_payload_with_visible_error(self) -> None:
        assignment = self.create_assignment()
        import_job = self.upload_html_import(assignment)
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            {
                "form_action": "confirm_import_job",
                "import_job_id": str(import_job.id),
                "candidate_count": "0",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "确认失败：候选题提交数据为空，请刷新页面后重试。")
        self.assertEqual(assignment.questions.filter(is_active=True).count(), 0)
        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)

    def test_confirm_import_job_with_incomplete_option_shows_error_and_writes_nothing(self) -> None:
        assignment = self.create_assignment()
        import_job = self.upload_html_import(assignment)
        self.sign_in(self.teacher)
        payload = self.build_confirm_payload(import_job, included_indexes={0})
        payload["candidate_0_option_C"] = ""

        with self.assertLogs("entry.views", level="WARNING") as captured_logs:
            response = self.client.post(
                reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
                payload,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "确认失败：A/B/C/D 选项必须全部填写完整。")
        self.assertEqual(assignment.questions.filter(is_active=True).count(), 0)
        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)
        combined_logs = "\n".join(captured_logs.output)
        self.assertIn("payload_count=2", combined_logs)
        self.assertIn("included_count=1", combined_logs)
        self.assertIn("written_count=0", combined_logs)

    def test_confirm_import_job_requires_at_least_one_selected_candidate(self) -> None:
        assignment = self.create_assignment()
        import_job = self.upload_html_import(assignment)
        self.sign_in(self.teacher)

        response = self.client.post(
            reverse("teacher-homework-builder", args=[self.student.id, assignment.id]),
            self.build_confirm_payload(import_job, included_indexes=set()),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "确认失败：请至少保留一道候选题后再确认导入。")
        self.assertEqual(assignment.questions.filter(is_active=True).count(), 0)
        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_PARSED)

    def test_student_can_submit_online_choice_homework_and_auto_grade(self) -> None:
        assignment = self.create_assignment()
        question_1 = self.create_question(assignment, question_no=1, stem="第一题", correct_answer="A")
        question_2 = self.create_question(assignment, question_no=2, stem="第二题", correct_answer="C")
        self.sign_in(self.student_user)

        get_response = self.client.get(reverse("student-homework-practice", args=[assignment.id]))
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "开始新的练习")
        self.assertContains(get_response, 'name="question_%s"' % question_1.id, html=False)

        post_response = self.submit_choice_answers(
            assignment,
            {
                question_1.id: "A",
                question_2.id: "B",
            },
            follow=True,
        )

        self.assertEqual(post_response.status_code, 200)
        self.assertContains(post_response, "本次练习已提交并自动判分")
        self.assertContains(post_response, "逐题结果")
        self.assertContains(post_response, "错题区")
        submission = HomeworkSubmission.objects.get(assignment=assignment, student=self.student)
        self.assertEqual(submission.status, HomeworkSubmission.STATUS_AUTO_CHECKED)
        self.assertEqual(submission.total_count, 2)
        self.assertEqual(submission.correct_count, 1)
        self.assertEqual(submission.wrong_count, 1)
        self.assertEqual(HomeworkSubmissionAnswer.objects.filter(submission=submission).count(), 2)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, HomeworkAssignment.STATUS_COMPLETED)

    def test_student_detail_page_uses_scoped_option_classes_for_radio_layout(self) -> None:
        assignment = self.create_assignment()
        question = self.create_question(assignment, question_no=1, stem="样式题", correct_answer="A")
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-practice", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "student-homework-detail")
        self.assertContains(response, "homework-question")
        self.assertContains(response, "homework-question__option homework-question__option--interactive")
        self.assertContains(response, "homework-question__selector")
        self.assertContains(response, 'name="question_%s"' % question.id, html=False)

    def test_student_detail_page_formats_code_like_options(self) -> None:
        assignment = self.create_assignment()
        self.create_question(
            assignment,
            question_no=1,
            stem="代码选项题",
            correct_answer="A",
            options_json={
                "A": "for (int i = 0; i < n; i++) { sum += a[i]; }",
                "B": "普通文本选项",
                "C": "if (x > 0) { return x; } else { return 0; }",
                "D": "输出最后结果",
            },
        )
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-practice", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "homework-question__option-code")
        self.assertContains(response, "for (int i = 0; i &lt; n; i++)", html=False)
        self.assertContains(response, "{", html=False)
        self.assertContains(response, "return 0;", html=False)
        self.assertContains(response, "普通文本选项")

    def test_student_practice_page_formats_cpp_code_stem_into_code_block(self) -> None:
        assignment = self.create_assignment()
        self.create_question(
            assignment,
            question_no=1,
            stem='for(int i=0;i<n;i++){for(int j=0;j<m;j++){if(a[i][j]==1){cout<<i<<" "<<j<<endl;}}}',
            correct_answer="A",
        )
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-practice", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "homework-code-block")
        self.assertContains(response, "<pre", html=False)
        self.assertContains(response, "for(int i = 0; i &lt; n; i++)", html=False)
        self.assertContains(response, "    for(int j = 0; j &lt; m; j++)", html=False)
        self.assertContains(response, "        if(a[i][j] == 1)", html=False)

    def test_student_practice_page_escapes_html_like_code_stem(self) -> None:
        assignment = self.create_assignment()
        self.create_question(
            assignment,
            question_no=1,
            stem='if(x<y){cout<<"<script>alert(1)</script>"<<endl;}',
            correct_answer="A",
        )
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-practice", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)
        self.assertNotContains(response, "<script>alert(1)</script>", html=False)

    def test_student_practice_page_keeps_plain_text_stem_out_of_code_block(self) -> None:
        assignment = self.create_assignment()
        self.create_question(
            assignment,
            question_no=1,
            stem="下面哪个说法是正确的？",
            correct_answer="A",
        )
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-practice", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "下面哪个说法是正确的？")
        self.assertNotContains(response, "homework-code-block")

    def test_student_result_page_marks_wrong_selected_answer_in_red_state(self) -> None:
        assignment = self.create_assignment()
        question_1 = self.create_question(assignment, question_no=1, stem="错题一", correct_answer="A")
        question_2 = self.create_question(assignment, question_no=2, stem="对题二", correct_answer="B")
        self.sign_in(self.student_user)

        response = self.submit_choice_answers(
            assignment,
            {
                question_1.id: "D",
                question_2.id: "B",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "homework-question__option--wrong")
        self.assertContains(response, "homework-question__option-status--wrong")
        self.assertContains(response, "homework-question__feedback--wrong")
        self.assertContains(response, "你的错误选择")

    def test_wrong_questions_are_derived_from_submission_answers(self) -> None:
        assignment = self.create_assignment()
        question_1 = self.create_question(assignment, question_no=1, stem="错题一", correct_answer="A")
        question_2 = self.create_question(assignment, question_no=2, stem="对题二", correct_answer="B")
        self.sign_in(self.student_user)
        self.submit_choice_answers(
            assignment,
            {
                question_1.id: "D",
                question_2.id: "B",
            },
        )

        submission = HomeworkSubmission.objects.get(assignment=assignment, student=self.student)
        wrong_answers = HomeworkSubmissionAnswer.objects.filter(submission=submission, is_correct=False)
        self.assertEqual(wrong_answers.count(), 1)
        self.assertEqual(wrong_answers.first().homework_question, question_1)

    def test_cancelled_assignment_cannot_submit_online_homework(self) -> None:
        assignment = self.create_assignment()
        question = self.create_question(assignment, question_no=1, stem="取消题", correct_answer="A")
        assignment.cancel()
        assignment.save(update_fields=["status", "updated_at"])
        self.sign_in(self.student_user)

        response = self.submit_choice_answers(
            assignment,
            {question.id: "A"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前作业已取消，不能继续提交。")
        self.assertFalse(HomeworkSubmission.objects.filter(assignment=assignment, student=self.student).exists())

    def test_assignment_without_questions_keeps_task_mode(self) -> None:
        assignment = self.create_assignment(title="知识点任务型作业")
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-detail", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "在线选择题作答")
        self.assertContains(response, "标记已完成")
        self.assertNotContains(response, "submit_choice_answers")

    def test_online_homework_detail_shows_practice_entry(self) -> None:
        assignment = self.create_assignment()
        self.create_question(assignment, question_no=1, stem="入口题", correct_answer="A")
        self.sign_in(self.student_user)

        response = self.client.get(reverse("student-homework-detail", args=[assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "在线选择题 1 题")
        self.assertContains(response, reverse("student-homework-practice", args=[assignment.id]))
        self.assertContains(response, "开始第一次练习")

    def test_print_pages_open_after_submission(self) -> None:
        assignment = self.create_assignment()
        question_1 = self.create_question(assignment, question_no=1, stem="打印错题", correct_answer="A")
        question_2 = self.create_question(assignment, question_no=2, stem="打印对题", correct_answer="B")
        self.sign_in(self.student_user)
        self.submit_choice_answers(
            assignment,
            {
                question_1.id: "C",
                question_2.id: "B",
            },
        )
        submission = HomeworkSubmission.objects.get(assignment=assignment, student=self.student)

        print_all = self.client.get(reverse("student-homework-print", args=[assignment.id, submission.id]))
        print_wrong = self.client.get(reverse("student-homework-print-wrong", args=[assignment.id, submission.id]))
        print_blank = self.client.get(reverse("student-homework-print-blank", args=[assignment.id]))

        self.assertEqual(print_all.status_code, 200)
        self.assertContains(print_all, "打印")
        self.assertContains(print_all, "打印错题")
        self.assertContains(print_all, "打印对题")
        self.assertContains(print_all, "正确答案")
        self.assertNotContains(print_all, 'type="radio"', html=False)
        self.assertContains(print_all, "homework-question__option--static")
        self.assertEqual(print_wrong.status_code, 200)
        self.assertContains(print_wrong, "错题打印")
        self.assertContains(print_wrong, "打印错题")
        self.assertNotContains(print_wrong, "打印对题")
        self.assertNotContains(print_wrong, 'type="radio"', html=False)
        self.assertEqual(print_blank.status_code, 200)
        self.assertContains(print_blank, "空白练习卷")
        self.assertContains(print_blank, "打印错题")
        self.assertContains(print_blank, "打印对题")
        self.assertNotContains(print_blank, "正确答案")
        self.assertNotContains(print_blank, "解析")

    def test_repractice_creates_new_submission_and_preserves_history(self) -> None:
        assignment = self.create_assignment()
        question = self.create_question(assignment, question_no=1, stem="重新练习题", correct_answer="A")
        self.sign_in(self.student_user)

        self.submit_choice_answers(assignment, {question.id: "B"})
        first_submission = HomeworkSubmission.objects.get(assignment=assignment, student=self.student)
        first_answer = HomeworkSubmissionAnswer.objects.get(submission=first_submission, homework_question=question)
        self.assertEqual(first_answer.selected_answer, "B")

        response = self.submit_choice_answers(
            assignment,
            {question.id: "A"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "本次练习已提交并自动判分")
        submissions = list(HomeworkSubmission.objects.filter(assignment=assignment, student=self.student).order_by("id"))
        self.assertEqual(len(submissions), 2)
        self.assertEqual(submissions[0].id, first_submission.id)
        self.assertEqual(submissions[1].correct_count, 1)
        self.assertEqual(
            HomeworkSubmissionAnswer.objects.get(submission=submissions[0], homework_question=question).selected_answer,
            "B",
        )
        self.assertEqual(
            HomeworkSubmissionAnswer.objects.get(submission=submissions[1], homework_question=question).selected_answer,
            "A",
        )

    def test_assignment_detail_shows_submission_datagrid_and_detail_links(self) -> None:
        assignment = self.create_assignment()
        question = self.create_question(assignment, question_no=1, stem="submission 列表题", correct_answer="A")
        self.sign_in(self.student_user)
        self.submit_choice_answers(assignment, {question.id: "B"})
        self.submit_choice_answers(assignment, {question.id: "A"})

        response = self.client.get(reverse("student-homework-detail", args=[assignment.id]))

        submissions = list(HomeworkSubmission.objects.filter(assignment=assignment, student=self.student).order_by("-id"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Submission History")
        self.assertContains(response, "第 1 次提交")
        self.assertContains(response, "第 2 次提交")
        self.assertContains(response, "查看详情")
        self.assertContains(
            response,
            reverse("student-homework-submission-detail", args=[assignment.id, submissions[0].id]),
        )

    def test_submission_detail_route_renders_specific_submission(self) -> None:
        assignment = self.create_assignment()
        question = self.create_question(assignment, question_no=1, stem="指定批次题", correct_answer="A")
        self.sign_in(self.student_user)
        self.submit_choice_answers(assignment, {question.id: "B"})
        self.submit_choice_answers(assignment, {question.id: "A"})
        first_submission, second_submission = HomeworkSubmission.objects.filter(
            assignment=assignment,
            student=self.student,
        ).order_by("id")

        response = self.client.get(
            reverse("student-homework-submission-detail", args=[assignment.id, first_submission.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "第 1 次提交")
        self.assertContains(response, "你的错误选择")
        self.assertContains(response, reverse("student-homework-print", args=[assignment.id, first_submission.id]))
        self.assertNotContains(response, reverse("student-homework-print", args=[assignment.id, second_submission.id]))

    def test_parent_can_view_child_homework_records_and_print_but_cannot_repractice(self) -> None:
        assignment = self.create_assignment()
        question_1 = self.create_question(assignment, question_no=1, stem="家长查看错题", correct_answer="A")
        question_2 = self.create_question(assignment, question_no=2, stem="家长查看对题", correct_answer="B")
        self.sign_in(self.student_user)
        self.submit_choice_answers(
            assignment,
            {
                question_1.id: "C",
                question_2.id: "B",
            },
        )
        submission = HomeworkSubmission.objects.get(assignment=assignment, student=self.student)
        self.sign_in(self.parent)

        list_response = self.client.get(reverse("parent-homework-list"))
        detail_response = self.client.get(reverse("parent-homework-detail", args=[assignment.id]))
        submission_response = self.client.get(
            reverse("parent-homework-submission-detail", args=[assignment.id, submission.id])
        )
        print_all = self.client.get(reverse("parent-homework-print", args=[assignment.id, submission.id]))
        print_wrong = self.client.get(reverse("parent-homework-print-wrong", args=[assignment.id, submission.id]))
        print_blank = self.client.get(reverse("parent-homework-print-blank", args=[assignment.id]))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "孩子作业记录")
        self.assertContains(list_response, assignment.title)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "提交记录")
        self.assertNotContains(detail_response, "重新练习")
        self.assertNotContains(detail_response, "打开知识点")
        self.assertContains(detail_response, "仅学生账号可进入内容")
        self.assertEqual(submission_response.status_code, 200)
        self.assertContains(submission_response, "逐题结果")
        self.assertContains(submission_response, reverse("parent-homework-print", args=[assignment.id, submission.id]))
        self.assertNotContains(submission_response, "重新练习")
        self.assertEqual(print_all.status_code, 200)
        self.assertContains(print_all, "正确答案")
        self.assertEqual(print_wrong.status_code, 200)
        self.assertContains(print_wrong, "家长查看错题")
        self.assertNotContains(print_wrong, "家长查看对题")
        self.assertEqual(print_blank.status_code, 200)
        self.assertNotContains(print_blank, "正确答案")
        self.assertNotContains(print_blank, "解析")


class HomeworkBatchCreateTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="codemaster-homework-batch-media-")
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
            username="teacher_batch_homework",
            role=PortalUser.ROLE_TEACHER,
            full_name="批量作业老师",
            phone="13800000201",
        )
        self.peer_teacher = PortalUser.objects.create(
            username="teacher_batch_peer",
            role=PortalUser.ROLE_TEACHER,
            full_name="别的作业老师",
            phone="13800000202",
        )
        self.parent = PortalUser.objects.create(
            username="parent_batch_homework",
            role=PortalUser.ROLE_PARENT,
            full_name="批量作业家长",
            phone="13800000203",
        )
        self.source_student_user = PortalUser.objects.create(
            username="student_batch_source",
            role=PortalUser.ROLE_STUDENT,
            full_name="题目来源学生",
            phone="13800000204",
        )
        self.target_student_user = PortalUser.objects.create(
            username="student_batch_target",
            role=PortalUser.ROLE_STUDENT,
            full_name="批量目标学生甲",
            phone="13800000205",
        )
        self.second_target_student_user = PortalUser.objects.create(
            username="student_batch_target_two",
            role=PortalUser.ROLE_STUDENT,
            full_name="批量目标学生乙",
            phone="13800000206",
        )
        self.outsider_student_user = PortalUser.objects.create(
            username="student_batch_outsider",
            role=PortalUser.ROLE_STUDENT,
            full_name="别的老师学生",
            phone="13800000207",
        )

        self.source_student = Student.objects.create(
            user=self.source_student_user,
            parent_user=self.parent,
            teacher_user=self.teacher,
            display_name="题目来源学生",
            grade="四年级",
            campus="虹桥校区",
            primary_course_name="C++",
            primary_track_name="GESP",
            primary_level_name="GESP4",
        )
        self.target_student = Student.objects.create(
            user=self.target_student_user,
            teacher_user=self.teacher,
            display_name="批量目标学生甲",
            grade="四年级",
            campus="虹桥校区",
            primary_course_name="C++",
            primary_track_name="GESP",
            primary_level_name="GESP4",
        )
        self.second_target_student = Student.objects.create(
            user=self.second_target_student_user,
            teacher_user=self.teacher,
            display_name="批量目标学生乙",
            grade="五年级",
            campus="虹桥校区",
            primary_course_name="C++",
            primary_track_name="GESP",
            primary_level_name="GESP4",
        )
        self.outsider_student = Student.objects.create(
            user=self.outsider_student_user,
            teacher_user=self.peer_teacher,
            display_name="别的老师学生",
            grade="五年级",
            campus="徐汇校区",
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
        self.array_content, _ = CourseContent.objects.update_or_create(
            slug=ARRAY_2D_CONTENT_SLUG,
            defaults={
                "course": self.cpp_course,
                "level": self.gesp4_level,
                "content_type": "topic",
                "title": next(item["title"] for item in GESP4_TOPIC_DEFINITIONS if item["slug"] == ARRAY_2D_CONTENT_SLUG),
                "phase": "GESP4",
                "sort_order": 1,
                "route_path": "/student/cpp/gesp/gesp4/array-2d",
                "summary": "二维数组专题",
                "has_real_content": True,
                "is_active": True,
            },
        )

        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.source_student,
            course=self.cpp_course,
            level_code="C4",
            is_active=True,
        )
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.target_student,
            course=self.cpp_course,
            level_code="C4",
            is_active=True,
        )
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.second_target_student,
            course=self.cpp_course,
            level_code="C4",
            is_active=True,
        )
        TeacherStudentAssignment.objects.create(
            teacher=self.peer_teacher,
            student=self.outsider_student,
            course=self.cpp_course,
            level_code="C4",
            is_active=True,
        )

        self.source_import_job = self.create_confirmed_import_job(
            teacher=self.teacher,
            student=self.source_student,
            title="二维数组批量题单",
            filename="array-batch-source.txt",
        )
        self.peer_import_job = self.create_confirmed_import_job(
            teacher=self.peer_teacher,
            student=self.outsider_student,
            title="别的老师题单",
            filename="peer-batch-source.txt",
        )
        self.question_backed_import_job = self.create_confirmed_import_job(
            teacher=self.teacher,
            student=self.source_student,
            title="已有正式题目的未确认题单",
            filename="question-backed-source.txt",
        )
        self.question_backed_import_job.parse_status = HomeworkImportJob.STATUS_UPLOADED
        self.question_backed_import_job.candidates_json = [
            {
                "index": 1,
                "stem": "fallback 候选题干",
                "options": {
                    "A": "候选 A",
                    "B": "候选 B",
                    "C": "候选 C",
                    "D": "候选 D",
                },
                "correct_answer": "A",
                "analysis": "候选解析",
                "notes": "",
                "included": True,
            }
        ]
        self.question_backed_import_job.save(update_fields=["parse_status", "candidates_json", "updated_at"])
        HomeworkQuestion.objects.filter(
            import_job=self.question_backed_import_job,
            question_no=1,
        ).update(
            stem="正式题优先返回",
            options_json={
                "A": "正式 A",
                "B": "正式 B",
                "C": "正式 C",
                "D": "正式 D",
            },
            correct_answer="C",
            analysis="正式题解析",
        )
        self.batch_create_url = reverse("teacher-homework-batch-create") + "?course=cpp"

    def sign_in(self, user: PortalUser) -> None:
        self.client.cookies[AUTH_COOKIE_NAME] = signing.dumps(
            {"username": user.username, "role": user.role},
            salt=AUTH_COOKIE_SALT,
        )

    def create_confirmed_import_job(
        self,
        *,
        teacher: PortalUser,
        student: Student,
        title: str,
        filename: str,
    ) -> HomeworkImportJob:
        assignment = HomeworkAssignment.objects.create(
            teacher=teacher,
            student=student,
            content=self.array_content,
            title=title,
            description="源作业说明",
            due_date=timezone.localdate() + timedelta(days=2),
            status=HomeworkAssignment.STATUS_ASSIGNED,
        )
        candidates = [
            {
                "index": 1,
                "stem": "二维数组第 1 题",
                "options": {
                    "A": "选项 A1",
                    "B": "选项 B1",
                    "C": "选项 C1",
                    "D": "选项 D1",
                },
                "correct_answer": "A",
                "analysis": "二维数组第 1 题解析",
                "notes": "",
                "included": True,
            },
            {
                "index": 2,
                "stem": "二维数组第 2 题",
                "options": {
                    "A": "选项 A2",
                    "B": "选项 B2",
                    "C": "选项 C2",
                    "D": "选项 D2",
                },
                "correct_answer": "B",
                "analysis": "二维数组第 2 题解析",
                "notes": "",
                "included": True,
            },
        ]
        import_job = HomeworkImportJob.objects.create(
            teacher=teacher,
            assignment=assignment,
            source_file=SimpleUploadedFile(filename, b"batch homework source", content_type="text/plain"),
            source_filename=filename,
            source_sha256=hashlib.sha256(b"batch homework source").hexdigest(),
            source_type=HomeworkImportJob.SOURCE_TYPE_TEXT,
            parse_status=HomeworkImportJob.STATUS_CONFIRMED,
            candidates_json=candidates,
            parse_notes="教师已确认题目，可用于批量布置。",
            confirmed_at=timezone.now(),
            is_active=True,
        )
        for index, candidate in enumerate(candidates, start=1):
            HomeworkQuestion.objects.create(
                assignment=assignment,
                import_job=import_job,
                question_no=index,
                question_type=HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                stem=candidate["stem"],
                options_json=candidate["options"],
                correct_answer=candidate["correct_answer"],
                analysis=candidate["analysis"],
                source_snapshot_json={"source_import_job_id": import_job.id, "candidate_index": index},
                is_active=True,
        )
        return import_job

    def build_candidate(self, *, stem: str = "候选题一", correct_answer: str = "A") -> dict:
        return {
            "stem": stem,
            "options": {
                "A": "选项 A",
                "B": "选项 B",
                "C": "选项 C",
                "D": "选项 D",
            },
            "correct_answer": correct_answer,
            "analysis": f"{stem} 的解析",
            "notes": "",
            "confidence": 0.92,
        }

    def build_png_bytes(self, *, width: int = 80, height: int = 260) -> bytes:
        from PIL import Image  # type: ignore

        image = Image.new("RGB", (width, height), color=(255, 255, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def post_batch_create(
        self,
        *,
        student_ids: list[int] | None = None,
        import_job_id: int | None = None,
        requirement: str = "先完成选择题，再口头讲解。",
        due_date: str | None = None,
        summary_title: str = "",
        summary_html: str = "",
        summary_file: SimpleUploadedFile | None = None,
        follow: bool = False,
    ):
        payload = {
            "student_ids": student_ids or [],
            "assignment_requirement": requirement,
            "due_date": due_date or (timezone.localdate() + timedelta(days=5)).isoformat(),
            "summary_title": summary_title,
            "summary_html": summary_html,
        }
        if import_job_id is not None:
            payload["import_job_id"] = str(import_job_id)
        if summary_file is not None:
            payload["summary_html_file"] = summary_file
        return self.client.post(self.batch_create_url, payload, follow=follow)

    def get_expected_question_source_import_href(self) -> str:
        return reverse("teacher-question-source-import") + "?course=cpp"

    def post_question_source_import_upload(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        content_id: int | None = None,
        follow: bool = False,
    ):
        return self.client.post(
            self.get_expected_question_source_import_href(),
            {
                "form_action": "upload_choice_file",
                "content_id": str(content_id or self.array_content.id),
                "source_file": SimpleUploadedFile(filename, content, content_type=content_type),
            },
            follow=follow,
        )

    def post_question_source_import_confirm(
        self,
        *,
        import_job: HomeworkImportJob,
        follow: bool = False,
    ):
        payload = {
            "form_action": "confirm_import_job",
            "import_job_id": str(import_job.id),
            "candidate_count": str(len(import_job.candidates_json)),
        }
        for index, candidate in enumerate(import_job.candidates_json):
            payload[f"candidate_{index}_included"] = "1"
            payload[f"candidate_{index}_stem"] = candidate["stem"]
            payload[f"candidate_{index}_option_A"] = candidate["options"]["A"]
            payload[f"candidate_{index}_option_B"] = candidate["options"]["B"]
            payload[f"candidate_{index}_option_C"] = candidate["options"]["C"]
            payload[f"candidate_{index}_option_D"] = candidate["options"]["D"]
            payload[f"candidate_{index}_correct_answer"] = candidate["correct_answer"]
            payload[f"candidate_{index}_analysis"] = candidate["analysis"]
            payload[f"candidate_{index}_notes"] = candidate.get("notes", "")
        return self.client.post(
            self.get_expected_question_source_import_href(),
            payload,
            follow=follow,
        )

    def create_public_question_source_import_job(
        self,
        *,
        filename: str = "public-bank.txt",
        stem: str = "公共题池文本题",
        correct_answer: str = "B",
        source_type: str = "text",
    ) -> HomeworkImportJob:
        self.sign_in(self.teacher)
        if source_type == "image":
            with patch(
                "entry.homework_online.extract_text_with_volc_vision",
                return_value=("1. 图片题\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n参考答案：A\n", "Doubao OCR"),
            ), patch(
                "entry.homework_online.parse_candidates_with_qwen",
                return_value=([self.build_candidate(stem=stem, correct_answer=correct_answer)], "Qwen Public Image"),
            ):
                upload_response = self.post_question_source_import_upload(
                    filename=filename,
                    content=self.build_png_bytes(width=120, height=120),
                    content_type="image/png",
                )
        else:
            with patch(
                "entry.homework_online.parse_candidates_with_qwen",
                return_value=([self.build_candidate(stem=stem, correct_answer=correct_answer)], "Qwen Public Text"),
            ):
                upload_response = self.post_question_source_import_upload(
                    filename=filename,
                    content=(
                        "1. 二维数组哪种写法正确？\n"
                        "A. arr(i,j)\nB. arr[i][j]\nC. arr{i}{j}\nD. arr<i><j>\n参考答案：B\n"
                    ).encode("utf-8"),
                    content_type="text/plain",
                )
        self.assertEqual(upload_response.status_code, 302)
        import_job = HomeworkImportJob.objects.get(source_filename=filename)
        confirm_response = self.post_question_source_import_confirm(import_job=import_job)
        self.assertEqual(confirm_response.status_code, 302)
        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_CONFIRMED)
        return import_job

    def test_teacher_workbench_shows_batch_homework_button(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(reverse("teacher-students") + "?tab=students")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "布置作业")
        self.assertContains(response, reverse("teacher-homework-batch-create") + "?course=cpp")
        self.assertNotContains(response, "导入学生")

    def test_batch_homework_page_returns_200_for_teacher(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "批量布置作业")
        self.assertContains(response, "选择题目")
        self.assertContains(response, "课后总结区块")
        self.assertContains(response, 'name="summary_title"', html=False)
        self.assertContains(response, 'name="summary_html_file"', html=False)
        self.assertContains(response, 'name="summary_html"', html=False)
        self.assertContains(response, 'enctype="multipart/form-data"', html=False)
        self.assertContains(response, "保存并批量布置作业")
        self.assertEqual(response.context["form_values"]["summary_title"], "")

    def test_batch_homework_page_shows_import_practice_button_with_public_question_source_entry(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 200)
        expected_href = self.get_expected_question_source_import_href()
        self.assertEqual(response.context["question_source_import_href"], expected_href)
        self.assertContains(response, "导入练习题")
        self.assertContains(response, expected_href)
        self.assertNotIn("/teacher/students/", expected_href)
        self.assertNotIn("student_id=", expected_href)

    def test_question_source_import_entry_keeps_teacher_only_permission(self) -> None:
        protected_href = self.get_expected_question_source_import_href()
        self.sign_in(self.target_student_user)

        response = self.client.get(protected_href)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("student-courses"))

    def test_question_source_import_entry_returns_public_import_page_not_student_homework_page(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(self.get_expected_question_source_import_href())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "上传文件生成公共题池选择题候选")
        self.assertContains(response, 'name="content_id"', html=False)
        self.assertNotContains(response, f"/teacher/students/{self.source_student.id}/homework/")

    def test_question_source_text_import_creates_public_import_job_and_questions_without_assignment(self) -> None:
        self.sign_in(self.teacher)
        assignment_count_before = HomeworkAssignment.objects.count()

        with patch(
            "entry.homework_online.parse_candidates_with_qwen",
            return_value=([self.build_candidate(stem="公共题池文本题", correct_answer="B")], "Qwen Public Text"),
        ):
            upload_response = self.post_question_source_import_upload(
                filename="public-bank.txt",
                content=(
                    "1. 二维数组哪种写法正确？\n"
                    "A. arr(i,j)\nB. arr[i][j]\nC. arr{i}{j}\nD. arr<i><j>\n参考答案：B\n"
                ).encode("utf-8"),
                content_type="text/plain",
            )

        self.assertEqual(upload_response.status_code, 302)
        self.assertIn("op=parsed", upload_response["Location"])
        import_job = HomeworkImportJob.objects.get(source_filename="public-bank.txt")
        self.assertIsNone(import_job.assignment_id)
        self.assertEqual(import_job.content_id, self.array_content.id)
        self.assertEqual(HomeworkAssignment.objects.count(), assignment_count_before)

        confirm_response = self.post_question_source_import_confirm(import_job=import_job)

        self.assertEqual(confirm_response.status_code, 302)
        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_CONFIRMED)
        self.assertEqual(HomeworkAssignment.objects.count(), assignment_count_before)
        created_questions = list(
            HomeworkQuestion.objects.filter(import_job=import_job, is_active=True).order_by("question_no", "id")
        )
        self.assertGreaterEqual(len(created_questions), 1)
        self.assertTrue(all(question.assignment_id is None for question in created_questions))

    def test_question_source_image_import_creates_public_import_job_and_questions_without_assignment(self) -> None:
        self.sign_in(self.teacher)
        assignment_count_before = HomeworkAssignment.objects.count()

        with patch(
            "entry.homework_online.extract_text_with_volc_vision",
            return_value=("1. 图片题\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n参考答案：A\n", "Doubao OCR"),
        ), patch(
            "entry.homework_online.parse_candidates_with_qwen",
            return_value=([self.build_candidate(stem="公共题池图片题", correct_answer="A")], "Qwen Public Image"),
        ):
            upload_response = self.post_question_source_import_upload(
                filename="public-bank.png",
                content=self.build_png_bytes(width=120, height=120),
                content_type="image/png",
            )

        self.assertEqual(upload_response.status_code, 302)
        import_job = HomeworkImportJob.objects.get(source_filename="public-bank.png")
        self.assertIsNone(import_job.assignment_id)
        self.assertEqual(import_job.content_id, self.array_content.id)
        self.assertEqual(HomeworkAssignment.objects.count(), assignment_count_before)

        confirm_response = self.post_question_source_import_confirm(import_job=import_job)

        self.assertEqual(confirm_response.status_code, 302)
        import_job.refresh_from_db()
        self.assertEqual(import_job.parse_status, HomeworkImportJob.STATUS_CONFIRMED)
        created_questions = list(
            HomeworkQuestion.objects.filter(import_job=import_job, is_active=True).order_by("question_no", "id")
        )
        self.assertGreaterEqual(len(created_questions), 1)
        self.assertTrue(all(question.assignment_id is None for question in created_questions))
        self.assertEqual(HomeworkAssignment.objects.count(), assignment_count_before)

    def test_batch_homework_page_opens_question_source_panel_when_import_jobs_exist(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["import_job_selector_should_open"])
        self.assertNotContains(response, 'id="homework-import-job-selector-panel" hidden')

    def test_batch_homework_page_uses_versioned_teacher_tabulator_asset(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "teacher_tabulator.js?v=20260503-homework-batch-submit-fix")

    def test_batch_page_lists_public_question_source_import_job(self) -> None:
        public_import_job = self.create_public_question_source_import_job(filename="public-visible.txt")

        self.sign_in(self.teacher)
        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 200)
        import_job_ids = {item["import_job_id"] for item in response.context["import_job_rows"]}
        self.assertIn(public_import_job.id, import_job_ids)

    def test_batch_page_can_preselect_public_import_job_from_query_param(self) -> None:
        public_import_job = self.create_public_question_source_import_job(filename="public-selected.txt")

        self.sign_in(self.teacher)
        response = self.client.get(self.batch_create_url + f"&import_job_id={public_import_job.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_import_job_row"]["import_job_id"], public_import_job.id)
        self.assertContains(response, f'value="{public_import_job.id}"', html=False)

    def test_non_teacher_is_redirected_from_batch_homework_page(self) -> None:
        self.sign_in(self.target_student_user)

        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("student-courses"))

    def test_batch_page_lists_only_current_teacher_students(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 200)
        student_names = {item["name"] for item in response.context["student_rows"]}
        self.assertSetEqual(
            student_names,
            {"题目来源学生", "批量目标学生甲", "批量目标学生乙"},
        )

    def test_batch_page_lists_students_by_teacher_user_even_without_assignment(self) -> None:
        TeacherStudentAssignment.objects.filter(
            teacher=self.teacher,
            student=self.target_student,
        ).delete()
        self.sign_in(self.teacher)

        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 200)
        student_names = {item["name"] for item in response.context["student_rows"]}
        self.assertIn("批量目标学生甲", student_names)

    def test_batch_create_creates_assignment_for_each_selected_student(self) -> None:
        self.sign_in(self.teacher)
        import_job_count_before = HomeworkImportJob.objects.count()
        question_count_before = HomeworkQuestion.objects.count()

        response = self.post_batch_create(
            student_ids=[self.target_student.id, self.second_target_student.id],
            import_job_id=self.source_import_job.id,
        )

        self.assertEqual(response.status_code, 302)
        created_assignments = HomeworkAssignment.objects.filter(
            teacher=self.teacher,
            student_id__in=[self.target_student.id, self.second_target_student.id],
            title="二维数组批量题单",
        ).order_by("student_id")
        self.assertEqual(created_assignments.count(), 2)
        for assignment in created_assignments:
            self.assertEqual(assignment.description, "先完成选择题，再口头讲解。")
            self.assertEqual(assignment.content, self.array_content)
            self.assertEqual(assignment.source_import_job_id, self.source_import_job.id)
            self.assertEqual(assignment.import_jobs.count(), 0)
            self.assertEqual(assignment.questions.count(), 0)
            self.assertEqual(assignment.get_effective_online_question_count(), 2)
            self.assertEqual(
                [question.id for question in assignment.get_effective_questions_queryset()],
                [question.id for question in self.source_import_job.questions.filter(is_active=True).order_by("question_no", "id")],
            )
        self.assertEqual(HomeworkImportJob.objects.count(), import_job_count_before)
        self.assertEqual(HomeworkQuestion.objects.count(), question_count_before)

    def test_batch_create_with_public_question_source_import_succeeds_in_one_post(self) -> None:
        public_import_job = self.create_public_question_source_import_job(filename="public-batch.txt")

        self.sign_in(self.teacher)
        response = self.post_batch_create(
            student_ids=[self.target_student.id, self.second_target_student.id],
            import_job_id=public_import_job.id,
            summary_file=SimpleUploadedFile(
                "public-summary.html",
                "<h2>公共题池总结</h2><p>一次提交即可成功。</p>".encode("utf-8"),
                content_type="text/html",
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.redirect_chain), 1)
        created_assignments = HomeworkAssignment.objects.filter(
            teacher=self.teacher,
            student_id__in=[self.target_student.id, self.second_target_student.id],
            title=self.array_content.title,
        ).order_by("student_id")
        self.assertEqual(created_assignments.count(), 2)
        self.assertTrue(all(item.source_import_job_id == public_import_job.id for item in created_assignments))
        self.assertEqual(HomeworkSummary.objects.count(), 1)
        self.assertEqual(response.context["batch_result"]["status"], "success")
        self.assertEqual(response.context["batch_result"]["title"], "批量布置成功")
        self.assertContains(response, "homework-batch-result-data")
        self.assertContains(response, "已为 2 名学生创建作业")

    def test_batch_create_still_works_when_teacher_student_assignment_is_missing(self) -> None:
        TeacherStudentAssignment.objects.filter(
            teacher=self.teacher,
            student=self.target_student,
        ).delete()
        self.sign_in(self.teacher)

        response = self.post_batch_create(
            student_ids=[self.target_student.id],
            import_job_id=self.source_import_job.id,
        )

        self.assertEqual(response.status_code, 302)
        assignment = HomeworkAssignment.objects.get(
            teacher=self.teacher,
            student=self.target_student,
            title="二维数组批量题单",
        )
        self.assertEqual(assignment.questions.count(), 0)
        self.assertEqual(assignment.source_import_job_id, self.source_import_job.id)
        self.assertEqual(assignment.get_effective_online_question_count(), 2)

    def test_batch_create_requires_student_selection(self) -> None:
        self.sign_in(self.teacher)

        response = self.post_batch_create(
            student_ids=[],
            import_job_id=self.source_import_job.id,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请至少选择 1 名学生。")
        self.assertEqual(response.context["batch_result"]["status"], "error")
        self.assertEqual(response.context["batch_result"]["title"], "批量布置失败")
        self.assertContains(response, "homework-batch-result-data")
        self.assertFalse(
            HomeworkAssignment.objects.filter(
                teacher=self.teacher,
                student=self.target_student,
                title="二维数组批量题单",
            ).exists()
        )

    def test_batch_create_requires_import_job_selection(self) -> None:
        self.sign_in(self.teacher)

        response = self.post_batch_create(
            student_ids=[self.target_student.id],
            import_job_id=None,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请先选择 1 条 HomeworkImportJob 题目记录。")
        self.assertEqual(response.context["batch_result"]["status"], "error")
        self.assertContains(response, "homework-batch-result-data")

    def test_batch_create_saves_requirement_and_links_import_job(self) -> None:
        self.sign_in(self.teacher)

        self.post_batch_create(
            student_ids=[self.target_student.id],
            import_job_id=self.source_import_job.id,
            requirement="口头复述二维数组遍历，再完成 2 题。",
        )

        assignment = HomeworkAssignment.objects.get(
            teacher=self.teacher,
            student=self.target_student,
            title="二维数组批量题单",
        )
        self.assertEqual(assignment.description, "口头复述二维数组遍历，再完成 2 题。")
        self.assertEqual(assignment.source_import_job_id, self.source_import_job.id)
        self.assertEqual(assignment.import_jobs.count(), 0)

    def test_batch_create_creates_single_shared_summary_for_multiple_assignments(self) -> None:
        self.sign_in(self.teacher)

        response = self.post_batch_create(
            student_ids=[self.target_student.id, self.second_target_student.id],
            import_job_id=self.source_import_job.id,
            summary_title="二维数组课后总结",
            summary_file=SimpleUploadedFile(
                "weekly-summary.html",
                "<h2>本周总结</h2><p>两位同学共用同一篇总结。</p>".encode("utf-8"),
                content_type="text/html",
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("with_summary=1", response["Location"])
        created_assignments = list(
            HomeworkAssignment.objects.filter(
                teacher=self.teacher,
                student_id__in=[self.target_student.id, self.second_target_student.id],
                title="二维数组批量题单",
            ).order_by("student_id")
        )
        self.assertEqual(len(created_assignments), 2)
        self.assertEqual(HomeworkSummary.objects.count(), 1)
        self.assertIsNotNone(created_assignments[0].summary_id)
        self.assertEqual(created_assignments[0].summary_id, created_assignments[1].summary_id)
        self.assertEqual(created_assignments[0].source_import_job_id, self.source_import_job.id)
        self.assertEqual(created_assignments[1].source_import_job_id, self.source_import_job.id)
        self.assertEqual(created_assignments[0].summary.title, "二维数组课后总结")

        self.sign_in(self.target_student_user)
        list_response = self.client.get(reverse("student-homework-list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, reverse("student-homework-summary", args=[created_assignments[0].id]), html=False)

        detail_response = self.client.get(reverse("student-homework-summary", args=[created_assignments[0].id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "二维数组课后总结")
        self.assertContains(detail_response, "两位同学共用同一篇总结。")

    def test_batch_create_without_summary_keeps_assignment_summary_empty(self) -> None:
        self.sign_in(self.teacher)

        response = self.post_batch_create(
            student_ids=[self.target_student.id],
            import_job_id=self.source_import_job.id,
            summary_title="只填标题不应创建总结",
            summary_html="",
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "已填写课后总结标题，但还没有上传或粘贴 HTML 内容。")
        self.assertEqual(HomeworkSummary.objects.count(), 0)
        self.assertFalse(
            HomeworkAssignment.objects.filter(
                teacher=self.teacher,
                student=self.target_student,
                title="二维数组批量题单",
            ).exists()
        )

        retry_response = self.post_batch_create(
            student_ids=[self.target_student.id],
            import_job_id=self.source_import_job.id,
        )

        self.assertEqual(retry_response.status_code, 302)
        assignment = HomeworkAssignment.objects.get(
            teacher=self.teacher,
            student=self.target_student,
            title="二维数组批量题单",
        )
        self.assertIsNone(assignment.summary_id)

    def test_batch_created_assignments_share_questions_for_practice_and_grading(self) -> None:
        self.sign_in(self.teacher)
        self.post_batch_create(
            student_ids=[self.target_student.id, self.second_target_student.id],
            import_job_id=self.source_import_job.id,
        )

        first_assignment = HomeworkAssignment.objects.get(
            teacher=self.teacher,
            student=self.target_student,
            title="二维数组批量题单",
        )
        second_assignment = HomeworkAssignment.objects.get(
            teacher=self.teacher,
            student=self.second_target_student,
            title="二维数组批量题单",
        )
        shared_questions = list(self.source_import_job.questions.filter(is_active=True).order_by("question_no", "id"))

        self.assertEqual(first_assignment.questions.count(), 0)
        self.assertEqual(second_assignment.questions.count(), 0)
        self.assertEqual(
            [question.id for question in first_assignment.get_effective_questions_queryset()],
            [question.id for question in shared_questions],
        )
        self.assertEqual(
            [question.id for question in second_assignment.get_effective_questions_queryset()],
            [question.id for question in shared_questions],
        )

        self.sign_in(self.target_student_user)
        practice_response = self.client.get(reverse("student-homework-practice", args=[first_assignment.id]))

        self.assertEqual(practice_response.status_code, 200)
        self.assertContains(practice_response, "二维数组第 1 题")
        self.assertContains(practice_response, "二维数组第 2 题")

        submit_response = self.client.post(
            reverse("student-homework-practice", args=[first_assignment.id]),
            {
                f"question_{shared_questions[0].id}": "A",
                f"question_{shared_questions[1].id}": "B",
            },
        )

        self.assertEqual(submit_response.status_code, 302)
        submission = HomeworkSubmission.objects.get(assignment=first_assignment, student=self.target_student)
        self.assertEqual(submission.status, HomeworkSubmission.STATUS_AUTO_CHECKED)
        self.assertEqual(submission.correct_count, 2)
        self.assertEqual(
            HomeworkSubmissionAnswer.objects.filter(
                submission=submission,
                homework_question_id__in=[question.id for question in shared_questions],
            ).count(),
            2,
        )

    def test_forged_student_id_outside_teacher_scope_is_rejected(self) -> None:
        self.sign_in(self.teacher)

        response = self.post_batch_create(
            student_ids=[self.target_student.id, self.outsider_student.id],
            import_job_id=self.source_import_job.id,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不属于你名下")
        self.assertEqual(response.context["batch_result"]["status"], "error")
        self.assertFalse(
            HomeworkAssignment.objects.filter(
                teacher=self.teacher,
                student_id__in=[self.target_student.id, self.outsider_student.id],
                title="二维数组批量题单",
            ).exists()
        )

    def test_forged_other_teacher_import_job_is_rejected(self) -> None:
        self.sign_in(self.teacher)

        response = self.post_batch_create(
            student_ids=[self.target_student.id],
            import_job_id=self.peer_import_job.id,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前老师不能使用这条 HomeworkImportJob 题目记录。")
        self.assertEqual(response.context["batch_result"]["status"], "error")
        self.assertFalse(
            HomeworkAssignment.objects.filter(
                teacher=self.teacher,
                student=self.target_student,
                title="别的老师题单",
            ).exists()
        )

    def test_import_job_preview_endpoint_returns_candidate_preview(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(
            reverse("teacher-homework-import-job-preview", args=[self.source_import_job.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source_filename"], "array-batch-source.txt")
        self.assertEqual(payload["question_count"], 2)
        self.assertEqual(payload["preview_items"][0]["question_no"], 1)
        self.assertIn("二维数组第 1 题", payload["preview_items"][0]["stem"])

    def test_question_source_lists_import_job_when_questions_exist_even_if_not_confirmed(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(self.batch_create_url)

        self.assertEqual(response.status_code, 200)
        import_job_ids = {item["import_job_id"] for item in response.context["import_job_rows"]}
        self.assertIn(self.question_backed_import_job.id, import_job_ids)

    def test_import_job_preview_prefers_homework_questions_over_candidates_json(self) -> None:
        self.sign_in(self.teacher)

        response = self.client.get(
            reverse("teacher-homework-import-job-preview", args=[self.question_backed_import_job.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["parse_status"], HomeworkImportJob.STATUS_UPLOADED)
        self.assertEqual(payload["preview_items"][0]["stem"], "正式题优先返回")
        self.assertEqual(payload["preview_items"][0]["correct_answer"], "C")
        self.assertEqual(payload["preview_items"][0]["analysis"], "正式题解析")

    def test_student_homework_list_shows_batch_created_assignment(self) -> None:
        self.sign_in(self.teacher)
        self.post_batch_create(
            student_ids=[self.target_student.id],
            import_job_id=self.source_import_job.id,
        )

        self.sign_in(self.target_student_user)
        response = self.client.get(reverse("student-homework-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "二维数组批量题单")

    def test_existing_direct_assignment_questions_remain_compatible(self) -> None:
        source_assignment = self.source_import_job.assignment
        self.sign_in(self.source_student_user)

        response = self.client.get(reverse("student-homework-practice", args=[source_assignment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "二维数组第 1 题")
        self.assertContains(response, "二维数组第 2 题")
