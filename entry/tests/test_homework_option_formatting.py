from __future__ import annotations

from django.test import SimpleTestCase

from entry.homework_option_formatting import build_homework_content_display, format_homework_option_display


class HomeworkOptionFormattingTests(SimpleTestCase):
    def test_formats_loop_code_option_into_multiline_block(self) -> None:
        formatted = format_homework_option_display("for (int i = 0; i < n; i++) { sum += a[i]; }")

        self.assertTrue(formatted["is_code_option"])
        self.assertEqual(
            formatted["display_text"],
            "for (int i = 0; i < n; i++)\n{\n    sum += a[i];\n}",
        )

    def test_formats_if_else_code_option_with_indentation(self) -> None:
        formatted = format_homework_option_display("if (x > 0) { return x; } else { return 0; }")

        self.assertTrue(formatted["is_code_option"])
        self.assertEqual(
            formatted["display_text"],
            "if (x > 0)\n{\n    return x;\n}\nelse\n{\n    return 0;\n}",
        )

    def test_keeps_plain_text_option_as_plain_text(self) -> None:
        formatted = format_homework_option_display("把两个数交换后再输出较大的那个。")

        self.assertFalse(formatted["is_code_option"])
        self.assertEqual(formatted["display_text"], "把两个数交换后再输出较大的那个。")

    def test_formats_nested_cpp_code_block_with_indentation(self) -> None:
        formatted = build_homework_content_display(
            "for(int i=0;i<n;i++){for(int j=0;j<m;j++){if(a[i][j]==1){cout<<i<<\" \"<<j<<endl;}}}"
        )

        self.assertTrue(formatted["is_code_content"])
        self.assertEqual(
            formatted["blocks"][0]["text"],
            "for(int i = 0; i < n; i++)\n{\n    for(int j = 0; j < m; j++)\n    {\n        if(a[i][j] == 1)\n        {\n            cout << i << \" \" << j << endl;\n        }\n    }\n}",
        )

    def test_builds_text_and_code_blocks_for_mixed_question_content(self) -> None:
        formatted = build_homework_content_display(
            "阅读下面程序并判断输出：for(int i=0;i<n;i++){if(a[i]>0){cout<<a[i]<<endl;}}"
        )

        self.assertFalse(formatted["blocks"][0]["kind"] == "code")
        self.assertEqual(formatted["blocks"][0]["text"], "阅读下面程序并判断输出")
        self.assertEqual(formatted["blocks"][1]["kind"], "code")
        self.assertIn("if(a[i] > 0)", formatted["blocks"][1]["text"])
