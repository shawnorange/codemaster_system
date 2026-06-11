from __future__ import annotations

import json
import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from entry.models import (
    ExamQuestionBankAsset,
    ExamQuestionBankOption,
    ExamQuestionBankPaper,
    ExamQuestionBankQuestion,
)


HERMES_DATABASE_URL = "postgresql://hermes_user:secret-password@mac-mini/hermes_question_bank"


def build_hermes_rows() -> list[dict]:
    return [
        {
            "question_uid": "gesp1-202603-q001",
            "level": "GESP1",
            "source_pdf_id": "2026_3_c_1",
            "source_file": "2026年3月C++1级试题.pdf",
            "import_batch_uid": "batch-202603",
            "question_no": 1,
            "question_type": "single_choice",
            "stem_md": "以下哪个选项正确？",
            "answer_json": {"answer": "A"},
            "analysis_md": "",
            "full_json": {"raw": {"question_no": 1}},
            "programming": None,
            "options": [
                {"key": "A", "text": "选项 A", "sort_order": 1},
                {"key": "B", "text": "选项 B", "sort_order": 2},
                {"key": "C", "text": "选项 C", "sort_order": 3},
                {"key": "D", "text": "选项 D", "sort_order": 4},
            ],
            "content_assets": [
                {
                    "asset_uid": "asset-content-q1",
                    "role": "content",
                    "type": "image/png",
                    "relative_path": "question-assets/gesp1/q1.png",
                    "public_url": "",
                    "alt": "题目说明图",
                    "width": 640,
                    "height": 320,
                },
                {
                    "asset_uid": "asset-evidence-q1",
                    "role": "evidence",
                    "type": "image/png",
                    "relative_path": "ocr-evidence/q1.png",
                    "public_url": "",
                    "alt": "OCR 证据图",
                    "width": 640,
                    "height": 320,
                },
            ],
        },
        {
            "question_uid": "gesp1-202603-q016",
            "level": "GESP1",
            "source_pdf_id": "2026_3_c_1",
            "source_file": "2026年3月C++1级试题.pdf",
            "import_batch_uid": "batch-202603",
            "question_no": 16,
            "question_type": "true_false",
            "stem_md": "判断：变量名可以用数字开头。",
            "answer_json": {"answer": False},
            "analysis_md": "",
            "full_json": json.dumps({"raw": {"question_no": 16}}),
            "programming": None,
            "options": json.dumps([]),
            "content_assets": json.dumps([]),
        },
        {
            "question_uid": "gesp1-202603-q026",
            "level": "GESP1",
            "source_pdf_id": "2026_3_c_1",
            "source_file": "2026年3月C++1级试题.pdf",
            "import_batch_uid": "batch-202603",
            "question_no": 26,
            "question_type": "programming",
            "stem_md": "编写程序输出 Hello。",
            "answer_json": {"judge": "standard"},
            "analysis_md": "",
            "full_json": {"programming": {"description": "输出 Hello"}},
            "programming": {"description": "输出 Hello", "samples": []},
            "options": [],
            "content_assets": [],
        },
    ]


class ImportHermesPaperCommandTests(TestCase):
    def call_import_command(self) -> str:
        output = StringIO()
        call_command(
            "import_hermes_paper",
            "--level",
            "GESP1",
            "--year",
            "2026",
            "--month",
            "3",
            "--source-pdf-id",
            "2026_3_c_1",
            stdout=output,
        )
        return output.getvalue()

    @patch.dict(os.environ, {"HERMES_SOURCE_DATABASE_URL": HERMES_DATABASE_URL})
    @patch("entry.management.commands.import_hermes_paper.Command.fetch_hermes_rows")
    def test_import_is_idempotent_and_skips_evidence_assets(self, fetch_hermes_rows) -> None:
        fetch_hermes_rows.return_value = build_hermes_rows()

        first_output = self.call_import_command()
        second_output = self.call_import_command()

        fetch_hermes_rows.assert_called_with(
            database_url=HERMES_DATABASE_URL,
            level="GESP1",
            source_pdf_id="2026_3_c_1",
        )
        self.assertNotIn(HERMES_DATABASE_URL, first_output + second_output)

        self.assertIn("卷：导入 1，更新 0", first_output)
        self.assertIn("题：导入 3，更新 0", first_output)
        self.assertIn("选项：导入 4，更新 0", first_output)
        self.assertIn("content 图片：导入 1，更新 0", first_output)
        self.assertIn("卷：导入 0，更新 1", second_output)
        self.assertIn("题：导入 0，更新 3", second_output)
        self.assertIn("选项：导入 0，更新 4", second_output)
        self.assertIn("content 图片：导入 0，更新 1", second_output)

        paper = ExamQuestionBankPaper.objects.get(source="hermes", source_pdf_id="2026_3_c_1")
        self.assertEqual(paper.level, "GESP1")
        self.assertEqual(paper.year, 2026)
        self.assertEqual(paper.month, 3)
        self.assertEqual(paper.source_file, "2026年3月C++1级试题.pdf")
        self.assertEqual(paper.title, "GESP1 2026年3月C++1级真题")
        self.assertEqual(ExamQuestionBankQuestion.objects.filter(paper=paper).count(), 3)
        self.assertEqual(ExamQuestionBankOption.objects.count(), 4)
        self.assertEqual(ExamQuestionBankAsset.objects.count(), 1)
        self.assertEqual(ExamQuestionBankAsset.objects.get().asset_role, "content")
        self.assertEqual(
            paper.questions.get(question_no=26).programming_json,
            {"description": "输出 Hello", "samples": []},
        )

    def test_command_rejects_papers_outside_current_scope(self) -> None:
        with self.assertRaises(CommandError) as context:
            call_command(
                "import_hermes_paper",
                "--level",
                "GESP1",
                "--year",
                "2026",
                "--month",
                "6",
                "--source-pdf-id",
                "2026_6_c_1",
            )

        self.assertIn("本轮只允许导入", str(context.exception))
