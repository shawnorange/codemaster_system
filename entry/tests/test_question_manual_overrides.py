from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from entry.auth import AUTH_COOKIE_NAME, AUTH_COOKIE_SALT
from entry.manual_overrides import build_manual_override_view_data
from entry.models import PortalUser, Question
from entry.question_fallbacks import _enumeration_record_to_page_question
from entry.question_queries import serialize_question


@override_settings(MEDIA_URL="/media/", DEBUG=True)
class QuestionManualOverrideTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_media_root = Path(tempfile.mkdtemp(prefix="codemaster-media-"))
        self.enterContext(override_settings(MEDIA_ROOT=self.temp_media_root))
        self.teacher = PortalUser.objects.create(
            username="teacher_manual_override",
            role=PortalUser.ROLE_TEACHER,
            full_name="截图教师",
        )
        self.teacher.set_password("123456")
        self.teacher.save(update_fields=["password"])
        self.client.cookies[AUTH_COOKIE_NAME] = signing.dumps(
            {"username": self.teacher.username, "role": self.teacher.role},
            salt=AUTH_COOKIE_SALT,
        )
        self.question = Question.objects.create(
            code="gesp2-enumeration-manual-override-test",
            content_slug="enumeration-method",
            level_code="GESP2",
            question_type="single_choice",
            source_year=2025,
            source_month=3,
            source_question_no=12,
            title="人工修正测试题",
            payload={
                "prompt": "原始题面保留",
                "ability_point": "枚举测试",
                "manual_override": {
                    "review_note": "旧备注",
                    "is_reviewed": False,
                },
            },
            sort_order=1,
            is_active=True,
        )
        self.addCleanup(shutil.rmtree, self.temp_media_root, ignore_errors=True)

    def test_teacher_upload_saves_relative_media_path_and_preserves_payload(self) -> None:
        response = self.client.post(
            reverse("teacher-question-manual-override", args=[self.question.id]),
            {
                "next": reverse("teacher-cpp-gesp2-enumeration"),
                "return_anchor": f"question-card-{self.question.id}",
                "review_note": "已补整题截图",
                "is_reviewed": "on",
                "question_image": SimpleUploadedFile(
                    "question.png",
                    b"fake-question-image",
                    content_type="image/png",
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("manual_override_status=saved", response["Location"])

        self.question.refresh_from_db()
        payload = self.question.payload
        manual_override = payload["manual_override"]

        self.assertEqual(payload["prompt"], "原始题面保留")
        self.assertTrue(manual_override["is_reviewed"])
        self.assertEqual(manual_override["review_note"], "已补整题截图")
        self.assertTrue(manual_override["updated_at"])
        self.assertEqual(
            manual_override["question_image"],
            f"question_assets/manual_overrides/GESP2/enumeration-method/q_{self.question.id}/question.png",
        )
        self.assertFalse(manual_override["question_image"].startswith("/"))
        self.assertTrue((self.temp_media_root / manual_override["question_image"]).exists())

    def test_reupload_same_slot_replaces_old_file_and_updates_relative_path(self) -> None:
        first_upload = self.client.post(
            reverse("teacher-question-manual-override", args=[self.question.id]),
            {
                "next": reverse("teacher-cpp-gesp2-enumeration"),
                "return_anchor": f"question-card-{self.question.id}",
                "review_note": "第一次上传",
                "question_image": SimpleUploadedFile(
                    "question.png",
                    b"first-image",
                    content_type="image/png",
                ),
            },
        )
        self.assertEqual(first_upload.status_code, 302)

        self.question.refresh_from_db()
        first_relative_path = self.question.payload["manual_override"]["question_image"]
        first_absolute_path = self.temp_media_root / first_relative_path
        self.assertTrue(first_absolute_path.exists())

        second_upload = self.client.post(
            reverse("teacher-question-manual-override", args=[self.question.id]),
            {
                "next": reverse("teacher-cpp-gesp2-enumeration"),
                "return_anchor": f"question-card-{self.question.id}",
                "review_note": "第二次上传",
                "question_image": SimpleUploadedFile(
                    "question.jpg",
                    b"second-image",
                    content_type="image/jpeg",
                ),
            },
        )
        self.assertEqual(second_upload.status_code, 302)

        self.question.refresh_from_db()
        second_relative_path = self.question.payload["manual_override"]["question_image"]
        second_absolute_path = self.temp_media_root / second_relative_path

        self.assertEqual(
            second_relative_path,
            f"question_assets/manual_overrides/GESP2/enumeration-method/q_{self.question.id}/question.jpg",
        )
        self.assertFalse(first_absolute_path.exists())
        self.assertTrue(second_absolute_path.exists())

    def test_enumeration_page_question_contains_manual_override_preview_urls(self) -> None:
        manual_override = {
            "question_image": f"question_assets/manual_overrides/GESP2/enumeration-method/q_{self.question.id}/question.png",
            "code_image": f"question_assets/manual_overrides/GESP2/enumeration-method/q_{self.question.id}/code.png",
            "options_image": "",
            "review_note": "预览检查",
            "is_reviewed": True,
            "updated_at": "2026-04-11T12:00:00+08:00",
        }
        self.question.payload["manual_override"] = manual_override
        self.question.save(update_fields=["payload"])

        serialized = serialize_question(self.question)
        page_question = _enumeration_record_to_page_question(serialized)
        expected_view = build_manual_override_view_data(manual_override)

        self.assertEqual(page_question["manual_override"], expected_view)
        self.assertEqual(page_question["manual_override"]["code_image_url"], f"/media/{manual_override['code_image']}")
        self.assertEqual(page_question["manual_override"]["question_image"], manual_override["question_image"])
