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

    def test_builds_diagram_block_for_plain_text_shape_matrix(self) -> None:
        formatted = build_homework_content_display(
            "观察下面图形：\n  *\n ***\n*****\n下面哪项描述正确？"
        )

        self.assertFalse(formatted["is_code_content"])
        self.assertEqual(formatted["blocks"][0], {"kind": "text", "text": "观察下面图形："})
        self.assertEqual(formatted["blocks"][1]["kind"], "diagram")
        self.assertEqual(formatted["blocks"][1]["text"], "  *\n ***\n*****")
        self.assertEqual(formatted["blocks"][2], {"kind": "text", "text": "下面哪项描述正确？"})

    def test_restores_flattened_shape_sequence_before_cpp_code(self) -> None:
        formatted = build_homework_content_display(
            '下面代码要输出左上三角形，图形中 # 的位置满足“行号越往下，# 越少”： '
            '当 n = 5 时输出： ##### #### ### ## # 横线处应填入（ ）。 '
            'int n; cin >> n; for (int i = 1; i <= n; i++) { if (__________) cout << "#"; }'
        )

        self.assertEqual(formatted["blocks"][0]["kind"], "text")
        self.assertEqual(formatted["blocks"][1], {"kind": "diagram", "text": "#####\n####\n###\n##\n#"})
        self.assertEqual(formatted["blocks"][2], {"kind": "text", "text": "横线处应填入（ ）。"})
        self.assertEqual(formatted["blocks"][3]["kind"], "code")
        self.assertIn("int n;", formatted["blocks"][3]["text"])
        self.assertIn('cout << "#";', formatted["blocks"][3]["text"])

    def test_does_not_restore_flattened_shape_inside_string_assignment(self) -> None:
        formatted = build_homework_content_display('字符串 s = "##### #### ### ## #"')

        self.assertNotIn("diagram", [block["kind"] for block in formatted["blocks"]])
        self.assertIn('"##### #### ### ## #"', formatted["blocks"][0]["text"])

    def test_does_not_restore_flattened_shape_when_literal_string_is_requested(self) -> None:
        formatted = build_homework_content_display("请输出 ##### #### ### ## # 这个字符串")

        self.assertNotIn("diagram", [block["kind"] for block in formatted["blocks"]])
        self.assertEqual(formatted["blocks"][0]["text"], "请输出 ##### #### ### ## # 这个字符串")

    def test_does_not_restore_flattened_shape_inside_cpp_cout_literal(self) -> None:
        formatted = build_homework_content_display('cout << "##### #### ### ## #";')

        self.assertNotIn("diagram", [block["kind"] for block in formatted["blocks"]])
        self.assertEqual(formatted["blocks"][0]["kind"], "code")
        self.assertIn('"##### #### ### ## #"', formatted["blocks"][0]["text"])

    def test_does_not_restore_irregular_flattened_shape_after_output_cue(self) -> None:
        formatted = build_homework_content_display("当 n = 5 时输出： ##### ### #### #")

        self.assertNotIn("diagram", [block["kind"] for block in formatted["blocks"]])
        self.assertEqual(formatted["blocks"][0]["text"], "当 n = 5 时输出： ##### ### #### #")
