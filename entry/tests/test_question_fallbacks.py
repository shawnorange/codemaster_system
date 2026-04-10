from __future__ import annotations

from copy import deepcopy

from django.test import SimpleTestCase

from entry.question_fallbacks import (
    build_array_2d_db_site_data,
    build_array_2d_static_import_records,
    build_enumeration_main_question_groups,
    build_enumeration_static_import_records,
    merge_array_2d_questions_into_site_data,
    merge_enumeration_questions_into_site_data,
)
from entry.topic_content.gesp2_enumeration.context import (
    enrich_site_data,
    load_site_data as load_enumeration_site_data,
)
from entry.topic_content.gesp2_enumeration.question_details import DEMO_RELATED_QUESTIONS
from entry.topic_content.gesp4_array_2d.context import (
    load_site_data as load_array_2d_site_data,
)


def _find_array_topic_question_ids(site_data: dict, topic_id: str) -> list[str]:
    for lecture in site_data.get("lectures", []):
        for section in lecture.get("sections", []):
            for child in section.get("children", []):
                if child.get("id") == topic_id:
                    return child.get("questionIds", [])
    return []


class QuestionFallbacksTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.demo_question_titles = {
            title
            for titles in DEMO_RELATED_QUESTIONS.values()
            for title in titles
        }
        cls.enumeration_site_data = enrich_site_data(load_enumeration_site_data())
        cls.array_2d_site_data = load_array_2d_site_data()

    def test_build_enumeration_static_import_records(self) -> None:
        records = build_enumeration_static_import_records(
            self.enumeration_site_data,
            demo_question_titles=self.demo_question_titles,
        )

        total_static_questions = sum(
            len(group.get("questions", []))
            for group in self.enumeration_site_data.get("ability_groups", [])
        )

        self.assertEqual(len(records), total_static_questions)
        self.assertEqual(records[0]["content_slug"], "enumeration-method")
        self.assertEqual(records[0]["level_code"], "GESP2")
        self.assertEqual(records[0]["payload"]["group_id"], "group-beginner")
        self.assertEqual(records[0]["payload"]["question_id"], "question-01")
        self.assertTrue(records[0]["code"].startswith("gesp2-enumeration-"))

    def test_merge_enumeration_questions_uses_db_and_keeps_static_fallback(self) -> None:
        static_records = build_enumeration_static_import_records(
            self.enumeration_site_data,
            demo_question_titles=self.demo_question_titles,
        )
        db_override = deepcopy(static_records[0])
        db_override["payload"]["statement"] = ["数据库覆盖后的题干"]
        db_override["payload"]["answer_label"] = "数据库答案标签"

        merged_site_data = merge_enumeration_questions_into_site_data(
            self.enumeration_site_data,
            [db_override],
            demo_question_titles=self.demo_question_titles,
        )

        first_question = merged_site_data["ability_groups"][0]["questions"][0]
        second_question = merged_site_data["ability_groups"][0]["questions"][1]

        self.assertEqual(first_question["statement"], ["数据库覆盖后的题干"])
        self.assertEqual(first_question["answer_label"], "数据库答案标签")
        self.assertEqual(second_question["title"], self.enumeration_site_data["ability_groups"][0]["questions"][1]["title"])
        self.assertEqual(
            merged_site_data["question_lookup"][first_question["title"]]["statement"],
            ["数据库覆盖后的题干"],
        )

    def test_build_enumeration_main_question_groups_prefers_db_only(self) -> None:
        static_records = build_enumeration_static_import_records(
            self.enumeration_site_data,
            demo_question_titles=self.demo_question_titles,
        )
        db_only_records = [deepcopy(static_records[0]), deepcopy(static_records[9])]
        db_only_records[0]["payload"]["statement"] = ["数据库题干 1"]
        db_only_records[1]["payload"]["statement"] = ["数据库题干 2"]

        groups = build_enumeration_main_question_groups(self.enumeration_site_data, db_only_records)

        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["questions"][0]["statement"], ["数据库题干 1"])
        self.assertEqual(groups[1]["questions"][0]["statement"], ["数据库题干 2"])

    def test_build_array_2d_static_import_records(self) -> None:
        records = build_array_2d_static_import_records(self.array_2d_site_data)

        self.assertEqual(len(records), len(self.array_2d_site_data["questions"]))
        self.assertEqual(records[0]["content_slug"], "array-2d")
        self.assertEqual(records[0]["level_code"], "GESP4")
        self.assertEqual(records[0]["payload"]["question_id"], "q-2024-09-single-7")
        self.assertEqual(records[0]["payload"]["lecture_id"], "lecture-1")
        self.assertTrue(records[0]["code"].startswith("gesp4-array-2d-"))

    def test_merge_array_2d_questions_uses_db_and_attaches_extra_question(self) -> None:
        static_records = build_array_2d_static_import_records(self.array_2d_site_data)
        db_override = deepcopy(static_records[0])
        db_override["payload"]["summary"] = "数据库覆盖后的摘要"
        db_override["payload"]["answer_text"] = "数据库覆盖后的答案"

        extra_question = deepcopy(static_records[0])
        extra_question["code"] = "gesp4-array-2d-extra-case"
        extra_question["title"] = "数据库追加题"
        extra_question["source_question_no"] = 88
        extra_question["payload"]["question_id"] = "q-db-extra"
        extra_question["payload"]["question_number"] = 88
        extra_question["payload"]["summary"] = "仅存在于数据库中的追加题"
        extra_question["payload"]["question_text"] = "数据库额外补充的二维数组题面。"
        extra_question["payload"]["answer_text"] = "数据库额外补充的答案。"
        extra_question["payload"]["analysis_text"] = "数据库额外补充的解析。"

        merged_site_data = merge_array_2d_questions_into_site_data(
            self.array_2d_site_data,
            [db_override, extra_question],
        )

        first_question = merged_site_data["questions"][0]
        extra_question_ids = [
            question["id"]
            for question in merged_site_data["questions"]
            if question["id"] == "q-db-extra"
        ]
        lecture_one_core = _find_array_topic_question_ids(
            merged_site_data,
            "lecture-1-core-definition",
        )

        self.assertEqual(first_question["summary"], "数据库覆盖后的摘要")
        self.assertEqual(first_question["answerText"], "数据库覆盖后的答案")
        self.assertEqual(extra_question_ids, ["q-db-extra"])
        self.assertIn("q-db-extra", lecture_one_core)

    def test_build_array_2d_db_site_data_prefers_db_only(self) -> None:
        static_records = build_array_2d_static_import_records(self.array_2d_site_data)
        db_only_records = [deepcopy(static_records[0])]
        db_only_records[0]["payload"]["summary"] = "数据库主区摘要"

        site_data = build_array_2d_db_site_data(self.array_2d_site_data, db_only_records)

        self.assertEqual(len(site_data["questions"]), 1)
        self.assertEqual(site_data["questions"][0]["summary"], "数据库主区摘要")
        self.assertEqual(
            _find_array_topic_question_ids(site_data, "lecture-1-core-definition"),
            ["q-2024-09-single-7"],
        )
