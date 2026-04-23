from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from entry.question_fallbacks import build_array_2d_static_import_records
from entry.topic_content.gesp4_array_2d.context import (
    get_student_topic_page_context,
    load_site_data,
)
from entry.topic_content.gesp4_array_2d.student_review import (
    build_student_review_data,
    extract_question_no,
    should_hide_prompt,
)


class GESP4Array2DReviewTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.site_data = load_site_data()
        cls.static_records = build_array_2d_static_import_records(cls.site_data)

    def test_build_student_review_data_groups_static_questions_into_six_sections(self) -> None:
        review_data, question_source = build_student_review_data(self.site_data, [])

        self.assertEqual(question_source, "static")
        self.assertEqual(review_data["content_slug"], "array-2d")
        self.assertEqual(review_data["default_section_key"], "overview")
        self.assertEqual(review_data["overview"]["section_count"], 6)
        self.assertEqual(review_data["sections"][0]["key"], "definition-access")
        self.assertEqual(review_data["sections"][-1]["key"], "matrix-operation")
        self.assertGreater(review_data["sections"][0]["question_count"], 0)
        self.assertEqual(review_data["navigation"][0]["key"], "overview")

    def test_build_student_review_data_extracts_theme_stem_options_and_code(self) -> None:
        review_data, _question_source = build_student_review_data(
            self.site_data,
            [deepcopy(self.static_records[3])],
        )

        lead_question = review_data["sections"][0]["lead_question"]

        self.assertEqual(lead_question["theme_stem_lines"][0], "第3题 可采用的代码是：（ ）。")
        self.assertEqual(len(lead_question["option_lines"]), 4)
        self.assertEqual(lead_question["option_lines"][1], "cout << a[1][2] << endl;")
        self.assertIn("int a[3][4] = {", lead_question["inline_code"])

    def test_extract_question_no_supports_expected_formats(self) -> None:
        self.assertEqual(extract_question_no("第7题"), "7")
        self.assertEqual(extract_question_no("第 8 题"), "8")
        self.assertEqual(extract_question_no("题目描述\n第 12 题\n代码如下"), "12")
        self.assertIsNone(extract_question_no("这段文字没有题号"))

    def test_should_hide_prompt_only_when_question_no_matches(self) -> None:
        self.assertTrue(should_hide_prompt("第7题 下面程序输出什么？", "第 7 题\nint a[3][4];"))
        self.assertFalse(should_hide_prompt("第7题 下面程序输出什么？", "第 8 题\nint a[3][4];"))
        self.assertFalse(should_hide_prompt("下面程序输出什么？", "int a[3][4];"))
        self.assertFalse(should_hide_prompt("第7题 下面程序输出什么？", "int a[3][4];"))

    def test_build_student_review_data_marks_prompt_hidden_when_question_no_repeats_in_code(self) -> None:
        db_record = deepcopy(self.static_records[0])
        db_record["payload"]["raw_question_text"] = "第7题 下面程序输出什么？\n第7题 int a[3][4];"
        db_record["payload"]["question_text"] = "第7题 下面程序输出什么？\n第7题 int a[3][4];"
        review_data, _question_source = build_student_review_data(self.site_data, [db_record])

        lead_question = review_data["sections"][0]["lead_question"]
        self.assertFalse(lead_question["should_render_prompt"])

    @patch("entry.topic_content.gesp4_array_2d.context.list_array_2d_questions")
    def test_get_student_topic_page_context_prefers_db_records_and_preserves_group_order(self, mock_list_questions) -> None:
        db_records = [
            deepcopy(self.static_records[0]),
            deepcopy(self.static_records[5]),
            deepcopy(self.static_records[-1]),
        ]
        db_records[0]["payload"]["summary"] = "数据库覆盖后的定义题摘要"
        db_records[1]["payload"]["summary"] = "数据库覆盖后的按行题摘要"
        db_records[2]["payload"]["summary"] = "数据库覆盖后的矩阵题摘要"
        mock_list_questions.return_value = db_records

        context = get_student_topic_page_context()

        self.assertEqual(context["topic_main_question_source"], "db")
        self.assertEqual(context["topic_review_data"]["overview"]["question_count"], 3)
        self.assertEqual(
            [section["key"] for section in context["topic_review_data"]["sections"]],
            ["definition-access", "row-major-parameter", "matrix-operation"],
        )
        self.assertEqual(
            context["topic_review_data"]["sections"][0]["lead_question"]["summary"],
            "数据库覆盖后的定义题摘要",
        )
        mock_list_questions.assert_called_once_with()

    def test_student_template_renders_independent_answer_and_analysis_toggles(self) -> None:
        rendered = render_to_string(
            "entry/topics/gesp4_array_2d_student_page.html",
            {
                "page_title": "GESP4 二维数组专题",
                "topic_note": "复盘提示",
                "breadcrumb_items": [],
                "role_label": "学生",
                "topic_main_question_source": "db",
                "topic_review_data": {
                    "default_section_key": "overview",
                    "topic_title": "GESP4 二维数组专题",
                    "navigation": [
                        {
                            "key": "overview",
                            "title": "专题导览",
                            "subtitle": "先看分组",
                            "badge": "1 组",
                        },
                        {
                            "key": "definition-access",
                            "title": "定义、坐标与基础遍历",
                            "subtitle": "稳住合法定义和坐标转换",
                            "badge": "2 题",
                        },
                    ],
                    "overview": {
                        "title": "GESP4 二维数组专题",
                        "definition": "按知识点组复盘。",
                        "summary": "学生复盘页。",
                        "section_count": 1,
                        "estimated_minutes": 18,
                        "question_count": 2,
                        "learning_targets": ["先看分组。"],
                        "next_section_key": "definition-access",
                        "next_section_title": "定义、坐标与基础遍历",
                        "first_section_key": "definition-access",
                    },
                    "sections": [
                        {
                            "key": "definition-access",
                            "title": "定义、坐标与基础遍历",
                            "section_label": "第 1 组",
                            "summary": "先稳住定义和坐标。",
                            "study_goal": "会写二维数组、会做坐标转换。",
                            "focus_statement": "先行后列。",
                            "pattern_statement": "外层行内层列。",
                            "practice_hint": "继续练习。",
                            "question_count": 2,
                            "practice_count": 1,
                            "question_types": "单选题",
                            "prev_section_key": "overview",
                            "prev_section_title": "专题导览",
                            "next_section_key": None,
                            "next_section_title": "",
                            "lead_question": {
                                "dom_id": "lead-question",
                                "source": "2024 年 09 月",
                                "question_type_label": "单选题",
                                "question_no": "第 7 题",
                                "level_code": "GESP4",
                                "content_slug": "array-2d",
                                "title": "二维数组合法定义",
                                "summary": "判断哪个选项正确。",
                                "classification_reason": "适合作为开篇题。",
                                "pitfall": "容易把二维数组写成逗号版一维数组。",
                                "statement": ["主例题题面"],
                                "theme_stem_lines": ["主例题题面"],
                                "should_render_prompt": False,
                                "option_lines": ["选项一", "选项二"],
                                "inline_code": "第7题\nint arr[3][4];",
                                "reference_code": "",
                                "study_guide_items": [],
                                "answer_text": "正确答案是 B。",
                                "analysis_lines": ["先看声明形式，再看行列位置。"],
                            },
                            "practice_questions": [
                                {
                                    "dom_id": "practice-question",
                                    "source": "2025 年 03 月",
                                    "question_type_label": "单选题",
                                    "question_no": "第 9 题",
                                    "level_code": "GESP4",
                                    "content_slug": "array-2d",
                                    "title": "正确定义二维数组",
                                    "summary": "从多个声明方式中选择合法定义。",
                                    "classification_reason": "继续巩固定义规则。",
                                    "pitfall": "方括号和括号混用。",
                                    "statement": ["练习题题面"],
                                    "theme_stem_lines": ["练习题题面"],
                                    "should_render_prompt": False,
                                    "option_lines": ["选项 A", "选项 B", "选项 C", "选项 D"],
                                    "inline_code": "第8题\nint arr[2][3];",
                                    "answer_text": "典型写法是 int arr[3][4];",
                                    "analysis_lines": ["看清行列和括号。"],
                                }
                            ],
                        }
                    ],
                },
            },
        )

        self.assertIn("显示主例题答案", rendered)
        self.assertIn("显示答案", rendered)
        self.assertIn('id="answer-panel-lead-question"', rendered)
        self.assertNotIn("显示主例题解析", rendered)
        self.assertNotIn("显示解析", rendered)
        self.assertIn("同类题主题干", rendered)
        self.assertIn('<ol class="question-option-list" type="A">', rendered)
        self.assertIn("<strong>易错提醒</strong>", rendered)
        self.assertIn("<strong>题目解析</strong>", rendered)
        self.assertIn("第7题", rendered)
        self.assertIn("第8题", rendered)
        self.assertNotIn("<p class=\"question-card__prompt\">主例题题面</p>", rendered)
        self.assertNotIn("<p class=\"question-card__prompt\">练习题题面</p>", rendered)
        self.assertIn("int arr[3][4];", rendered)
        self.assertIn("data-visibility-toggle", rendered)
        self.assertNotIn("复盘提示", rendered)
        self.assertNotIn("<span>专题范围</span>", rendered)
        self.assertNotIn("<span>知识点组</span>", rendered)
        self.assertNotIn("<span>题目来源</span>", rendered)

    def test_topic_home_template_does_not_render_topic_note_hint_bar(self) -> None:
        rendered = render_to_string(
            "entry/topics/gesp4_array_2d_page.html",
            {
                "page_title": "GESP4 二维数组专题",
                "breadcrumb_items": [],
                "topic_note": "当前页面已切到数据库优先模式",
                "topic_page_mode": "home",
                "topic_slug": "array-2d",
                "topic_site_data_script_id": "gesp4-array-2d-site-data",
                "topic_site_data": {"meta": {"title": "GESP4 二维数组专题"}},
                "topic_badge_text": "GESP4 / 二维数组专题",
            },
        )

        self.assertNotIn("当前页面已切到数据库优先模式", rendered)
        self.assertNotIn("entry-hint-bar", rendered)
