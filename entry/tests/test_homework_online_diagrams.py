from __future__ import annotations

from django.test import SimpleTestCase

from entry.homework_online import parse_candidates_with_heuristic, sanitize_candidate


class HomeworkOnlineDiagramTests(SimpleTestCase):
    def test_sanitize_candidate_preserves_diagram_lines_in_stem(self) -> None:
        candidate = sanitize_candidate(
            {
                "stem": "观察下面图形：\n  *\n ***\n*****\n参考答案：A",
                "options": {
                    "A": "上半菱形",
                    "B": "直线",
                    "C": "矩形",
                    "D": "圆形",
                },
                "correct_answer": "A",
                "analysis": "",
            },
            index=1,
        )

        self.assertEqual(candidate["stem"], "观察下面图形：\n  *\n ***\n*****")

    def test_heuristic_parser_preserves_diagram_lines_in_stem(self) -> None:
        candidates, _notes = parse_candidates_with_heuristic(
            "第1题 观察下面图形：\n"
            "  *\n"
            " ***\n"
            "*****\n"
            "A. 上半菱形\n"
            "B. 直线\n"
            "C. 矩形\n"
            "D. 圆形\n"
            "答案：A"
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["stem"], "观察下面图形：\n  *\n ***\n*****")
