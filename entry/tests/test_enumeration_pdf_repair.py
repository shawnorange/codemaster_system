from __future__ import annotations

from django.test import SimpleTestCase

from entry.enumeration_pdf_repair import (
    extract_code_block,
    extract_counting_samples,
    extract_options,
    extract_reference_code_from_page_text,
)


class EnumerationPdfRepairTests(SimpleTestCase):
    def test_extract_options_merges_multiline_fill_blank_choices(self) -> None:
        block_text = """
        第14题 下面C++代码实现输出如下图形，横线应填入的代码是（）。
        A.
        1
        height -i
        2
        2*i
        口 B.
        1
        height
        2
        2*i
        口 C.
        1
        height-i
        2
        2*i+1
        口 D.
        1
        height - i - 1
        2
        2 * i + 1
        """

        options = extract_options(block_text)

        self.assertEqual(
            options,
            [
                "height -i；2*i",
                "height；2*i",
                "height-i；2*i+1",
                "height - i - 1；2 * i + 1",
            ],
        )

    def test_extract_counting_samples_cleans_ocr_numbering_noise(self) -> None:
        ocr_sections = {
            "sample_input": "1\n2\n2221\n2223",
            "sample_output": "12",
            "sample_explanation": "2221 到 2223中，2221与2223 是美丽的，2222 不是美丽的。",
        }

        sample_input, sample_output = extract_counting_samples(ocr_sections, {})

        self.assertEqual(sample_input, "2221\n2223")
        self.assertEqual(sample_output, "2")

    def test_extract_code_block_keeps_question_code_before_options(self) -> None:
        block_text = """
        第6题 以下C++代码实现从大到小的顺序输出N的所有因子。
        1
        int N= 0；
        2
        cin >> N；
        3
        for （int i = N; i > 0; i--） // 此处填写代码
        4
        if （！（N % i））
        5
        cout << i << ' '；
        口 A. ; ;
        口 B. int i = 1; i < N; i++
        """

        code = extract_code_block(block_text)

        self.assertEqual(
            code,
            "int N= 0;\ncin >> N;\nfor (int i = N; i > 0; i--) // 此处填写代码\nif (!(N % i))\ncout << i << ' ';",
        )

    def test_extract_reference_code_from_page_text_stops_before_footer(self) -> None:
        page_text = """
        3.1.7 参考程序
        #include <iostream>
        using namespace std;
        int main() {
            return 0;
        }
        第 9 页 / 共 10 页
        """

        code = extract_reference_code_from_page_text(page_text)

        self.assertEqual(
            code,
            "#include <iostream>\nusing namespace std;\nint main() {\nreturn 0;\n}",
        )
