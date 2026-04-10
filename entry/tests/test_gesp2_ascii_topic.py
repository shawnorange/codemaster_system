from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase

from entry.topic_content.gesp2_ascii_char_encoding.context import (
    build_ascii_question_groups,
    get_topic_page_context,
    load_fallback_questions,
)


class GESP2AsciiTopicTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.fallback_questions = load_fallback_questions()

    def test_build_ascii_question_groups_uses_expected_ability_points(self) -> None:
        groups = build_ascii_question_groups(self.fallback_questions)

        self.assertEqual(len(groups), 6)
        self.assertEqual(sum(len(group["questions"]) for group in groups), 19)
        self.assertEqual(groups[0]["title"], "字符与整数的本质关系")
        self.assertEqual(groups[-1]["title"], "基于字符编码规律的构造输出")

        comparison_question = next(
            question
            for group in groups
            for question in group["questions"]
            if question["code"] == "gesp2-ascii-2023-03-sc14"
        )
        self.assertEqual(comparison_question["question_no"], "第 14 题")
        self.assertEqual(comparison_question["answer_label"], "D")
        self.assertEqual(
            comparison_question["code_text"],
            (
                "#include <iostream>\n"
                "using namespace std;\n"
                "int main() {\n"
                "    int cnt = 0;\n"
                "    for (char ch = '1'; ch <= '9'; ch++)\n"
                "        if (__________)\n"
                "            cnt++;\n"
                "    cout << cnt << endl;\n"
                "    return 0;\n"
                "}"
            ),
        )

        digit_sum_question = next(
            question
            for group in groups
            for question in group["questions"]
            if question["code"] == "gesp2-ascii-2023-06-sc13"
        )
        self.assertEqual(digit_sum_question["answer_label"], "D")
        self.assertEqual(
            digit_sum_question["code_text"],
            (
                "#include <iostream>\n"
                "using namespace std;\n"
                "int main() {\n"
                "    char a = '3', b = '6';\n"
                "    cout << ________ << endl;\n"
                "    return 0;\n"
                "}"
            ),
        )

    @patch(
        "entry.topic_content.gesp2_ascii_char_encoding.context.list_ascii_char_encoding_questions",
        return_value=[],
    )
    def test_get_topic_page_context_falls_back_to_static_questions(self, mock_list_questions) -> None:
        context = get_topic_page_context(view_mode="student")

        self.assertEqual(context["topic_main_question_source"], "static")
        self.assertEqual(len(context["topic_main_question_groups"]), 6)
        self.assertEqual(sum(len(group["questions"]) for group in context["topic_main_question_groups"]), 19)
        self.assertFalse(context["show_teacher_support_sections"])
        mock_list_questions.assert_called_once_with()

    @patch("entry.topic_content.gesp2_ascii_char_encoding.context.list_ascii_char_encoding_questions")
    def test_get_topic_page_context_prefers_db_questions(self, mock_list_questions) -> None:
        db_record = deepcopy(self.fallback_questions[0])
        db_record["payload"]["statement"] = ["数据库覆盖后的 ASCII 题干"]
        mock_list_questions.return_value = [db_record]

        context = get_topic_page_context(view_mode="teacher")

        self.assertEqual(context["topic_main_question_source"], "db")
        self.assertEqual(len(context["topic_main_question_groups"]), 1)
        self.assertEqual(
            context["topic_main_question_groups"][0]["questions"][0]["statement"],
            ["数据库覆盖后的 ASCII 题干"],
        )
        self.assertTrue(context["show_teacher_support_sections"])
