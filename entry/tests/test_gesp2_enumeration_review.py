from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase
from django.template.loader import render_to_string

from entry.question_fallbacks import (
    build_enumeration_main_question_groups,
    build_enumeration_static_import_records,
)
from entry.topic_content.gesp2_enumeration.context import (
    build_student_topic_review,
    enrich_site_data,
    get_topic_page_context,
    load_site_data,
)
from entry.topic_content.gesp2_enumeration.question_details import DEMO_RELATED_QUESTIONS


class GESP2EnumerationReviewTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.site_data = enrich_site_data(load_site_data())
        cls.demo_question_titles = {
            title
            for titles in DEMO_RELATED_QUESTIONS.values()
            for title in titles
        }
        cls.static_records = build_enumeration_static_import_records(
            cls.site_data,
            demo_question_titles=cls.demo_question_titles,
        )

    def test_build_student_topic_review_uses_demo_first_and_falls_back_to_first_question(self) -> None:
        beginner_records = [
            deepcopy(record)
            for record in self.static_records
            if record["payload"]["group_id"] == "group-beginner"
        ]
        break_records = [
            deepcopy(record)
            for record in self.static_records
            if record["payload"]["group_id"] == "group-break-boundary"
        ]

        extra_demo = deepcopy(beginner_records[0])
        extra_demo["code"] = "gesp2-enumeration-db-extra-demo"
        extra_demo["title"] = "数据库追加主例题"
        extra_demo["sort_order"] = 5
        extra_demo["is_demo"] = True
        extra_demo["payload"]["prompt"] = "数据库新增的一道主例题。"
        extra_demo["payload"]["statement"] = ["数据库新增的一道主例题。"]

        beginner_practice = deepcopy(beginner_records[1])
        beginner_practice["is_demo"] = False

        for record in break_records:
            record["is_demo"] = False

        question_groups = build_enumeration_main_question_groups(
            self.site_data,
            [extra_demo, beginner_practice, break_records[0], break_records[1]],
        )
        review_data = build_student_topic_review(self.site_data, question_groups)

        self.assertEqual(review_data["overview"]["next_section_key"], "group-beginner")
        self.assertEqual(review_data["navigation"][0]["key"], "overview")
        self.assertEqual(review_data["sections"][0]["key"], "group-beginner")
        self.assertEqual(review_data["sections"][0]["demo_question"]["title"], "数据库追加主例题")
        self.assertEqual(review_data["sections"][0]["practice_count"], 1)
        self.assertEqual(review_data["sections"][1]["key"], "group-break-boundary")
        self.assertEqual(
            review_data["sections"][1]["demo_question"]["title"],
            break_records[0]["title"],
        )
        self.assertEqual(review_data["sections"][1]["practice_count"], 1)

    @patch("entry.topic_content.gesp2_enumeration.context.list_enumeration_method_questions")
    def test_get_topic_page_context_includes_student_review_data(self, mock_list_questions) -> None:
        beginner_records = [
            deepcopy(record)
            for record in self.static_records
            if record["payload"]["group_id"] == "group-beginner"
        ][:2]
        beginner_records[0]["is_demo"] = True
        beginner_records[1]["is_demo"] = False
        mock_list_questions.return_value = beginner_records

        context = get_topic_page_context(view_mode="student")

        self.assertEqual(context["topic_main_question_source"], "db")
        self.assertEqual(context["topic_review_data"]["default_section_key"], "overview")
        self.assertEqual(context["topic_review_data"]["sections"][0]["key"], "group-beginner")
        self.assertEqual(
            context["topic_review_data"]["sections"][0]["demo_question"]["title"],
            beginner_records[0]["title"],
        )
        self.assertEqual(context["topic_review_data"]["sections"][0]["practice_count"], 1)

    def test_student_template_renders_manual_override_images_for_practice_questions(self) -> None:
        rendered = render_to_string(
            "entry/topics/gesp2_enumeration_page.html",
            {
                "page_title": "枚举法",
                "topic_note": "复盘提示",
                "breadcrumb_items": [],
                "role_label": "学生",
                "topic_view_mode": "student",
                "topic_review_data": {
                    "default_section_key": "overview",
                    "topic_title": "枚举法",
                    "navigation": [
                        {
                            "key": "overview",
                            "title": "专题导览",
                            "subtitle": "先知道这次怎么复盘",
                            "badge": "1 节",
                        },
                        {
                            "key": "group-beginner",
                            "title": "枚举法入门",
                            "subtitle": "先看主例题，再做同类题",
                            "badge": "2 题",
                        },
                    ],
                    "overview": {
                        "title": "枚举法",
                        "definition": "先枚举，再判定。",
                        "summary": "学生复盘页。",
                        "section_count": 1,
                        "estimated_minutes": 12,
                        "question_count": 2,
                        "learning_targets": ["先看主例题。"],
                        "video_note": "暂未上传视频，可先按图文复盘。",
                        "next_section_key": "group-beginner",
                        "next_section_title": "枚举法入门",
                        "first_section_key": "group-beginner",
                    },
                    "sections": [
                        {
                            "key": "group-beginner",
                            "title": "枚举法入门",
                            "section_label": "第 1 节",
                            "summary": "先看边界，再看命中条件。",
                            "study_goal": "把单层枚举题做稳。",
                            "pattern_statement": "先看范围，再看条件。",
                            "practice_hint": "继续练习。",
                            "question_count": 2,
                            "practice_count": 1,
                            "prev_section_key": "overview",
                            "prev_section_title": "专题导览",
                            "next_section_key": None,
                            "next_section_title": "",
                            "demo_question": {
                                "title": "主例题",
                                "source": "2023 年 9 月",
                                "question_type": "单选题",
                                "question_no": "第 5 题",
                                "statement": ["主例题题面"],
                                "manual_override": {
                                    "has_any_image": False,
                                },
                                "answer_label": "A",
                                "answer_analysis": [],
                            },
                            "practice_questions": [
                                {
                                    "title": "按从大到小输出 N 的所有因子",
                                    "source": "2023 年 9 月",
                                    "question_type": "单选题",
                                    "question_no": "第 6 题",
                                    "prompt": "输入 N 后，要求按从大到小顺序输出所有因子。",
                                    "statement": ["以下 C++ 代码实现从大到小的顺序输出 N 的所有因子。"],
                                    "manual_override": {
                                        "has_any_image": True,
                                        "question_image_url": "/media/question_assets/manual_overrides/GESP2/enumeration-method/q_54/question.png",
                                        "code_image_url": "",
                                        "options_image_url": "",
                                    },
                                    "answer_label": "C",
                                    "answer_analysis": [],
                                }
                            ],
                        }
                    ],
                },
            },
        )

        self.assertIn("/media/question_assets/manual_overrides/GESP2/enumeration-method/q_54/question.png", rendered)
