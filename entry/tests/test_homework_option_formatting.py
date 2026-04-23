from __future__ import annotations

from django.test import SimpleTestCase

from entry.homework_option_formatting import format_homework_option_display


class HomeworkOptionFormattingTests(SimpleTestCase):
    def test_formats_loop_code_option_into_multiline_block(self) -> None:
        formatted = format_homework_option_display("for (int i = 0; i < n; i++) { sum += a[i]; }")

        self.assertTrue(formatted["is_code_option"])
        self.assertEqual(
            formatted["display_text"],
            "for (int i = 0; i < n; i++) {\n    sum += a[i];\n}",
        )

    def test_formats_if_else_code_option_with_indentation(self) -> None:
        formatted = format_homework_option_display("if (x > 0) { return x; } else { return 0; }")

        self.assertTrue(formatted["is_code_option"])
        self.assertEqual(
            formatted["display_text"],
            "if (x > 0) {\n    return x;\n}\nelse {\n    return 0;\n}",
        )

    def test_keeps_plain_text_option_as_plain_text(self) -> None:
        formatted = format_homework_option_display("把两个数交换后再输出较大的那个。")

        self.assertFalse(formatted["is_code_option"])
        self.assertEqual(formatted["display_text"], "把两个数交换后再输出较大的那个。")
