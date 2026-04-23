from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from entry.homework_question_rendering import (
    QUESTION_STATE_ANSWERING,
    QUESTION_STATE_PRINT_BLANK,
    QUESTION_STATE_PRINT,
    QUESTION_STATE_SUBMITTED,
    build_homework_question_view_model,
)


class HomeworkQuestionRenderingTests(SimpleTestCase):
    def build_question(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=101,
            question_type="single_choice",
            question_no=1,
            stem="下面哪段代码会输出答案？",
            options_json={
                "A": "for (int i = 0; i < n; i++) { sum += a[i]; }",
                "B": "普通文本选项",
                "C": "if (x > 0) { return x; } else { return 0; }",
                "D": "输出最后结果",
            },
            correct_answer="C",
            analysis="因为条件分支控制了返回值。",
        )

    def test_builds_answering_state_view_model(self) -> None:
        question = build_homework_question_view_model(
            self.build_question(),
            state=QUESTION_STATE_ANSWERING,
        )

        self.assertEqual(question["state"], QUESTION_STATE_ANSWERING)
        self.assertEqual(question["question_type"], "single_choice")
        self.assertEqual(question["body_template"], "entry/includes/homework_questions/question_single_choice.html")
        self.assertTrue(question["show_selector"])
        self.assertFalse(question["show_feedback"])
        self.assertEqual(question["result_text"], "单选题")

    def test_builds_submitted_state_with_highlight_flags(self) -> None:
        answer = SimpleNamespace(
            selected_answer="A",
            correct_answer_snapshot="C",
            analysis_snapshot="提交后解析",
        )

        question = build_homework_question_view_model(
            self.build_question(),
            state=QUESTION_STATE_SUBMITTED,
            answer=answer,
        )

        self.assertEqual(question["student_answer"], "A")
        self.assertEqual(question["correct_answer"], "C")
        self.assertTrue(question["show_feedback"])
        self.assertTrue(question["is_wrong"])
        self.assertEqual(question["options"][0]["status_label"], "你的错误选择")
        self.assertEqual(question["options"][0]["style_tone"], "wrong")
        self.assertEqual(question["options"][2]["status_label"], "正确答案")
        self.assertEqual(question["options"][2]["style_tone"], "correct")

    def test_builds_print_state_without_selector(self) -> None:
        answer = SimpleNamespace(
            selected_answer="C",
            correct_answer_snapshot="C",
            analysis_snapshot="打印解析",
        )

        question = build_homework_question_view_model(
            self.build_question(),
            state=QUESTION_STATE_PRINT,
            answer=answer,
        )

        self.assertTrue(question["is_print"])
        self.assertFalse(question["show_selector"])
        self.assertTrue(question["show_feedback"])
        self.assertEqual(question["student_answer_text"], "C")

    def test_builds_blank_print_state_without_feedback(self) -> None:
        question = build_homework_question_view_model(
            self.build_question(),
            state=QUESTION_STATE_PRINT_BLANK,
        )

        self.assertTrue(question["is_print"])
        self.assertTrue(question["is_blank_print"])
        self.assertFalse(question["show_selector"])
        self.assertFalse(question["show_feedback"])
        self.assertEqual(question["badge_text"], "空白练习")
