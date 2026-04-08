from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .question_details import DEMO_RELATED_QUESTIONS, QUESTION_DETAILS


TOPIC_DIR = Path(__file__).resolve().parent
SITE_DATA_PATH = TOPIC_DIR / "site-data.json"
_SITE_DATA_CACHE: dict[str, Any] | None = None
_SITE_DATA_MTIME_NS: int | None = None
TEACHER_ONLY_SECTION_IDS = {
    "demo-lab",
    "knowledge-overview",
    "pitfalls",
    "scope-boundary",
    "coverage",
    "teaching-notes",
}


def load_site_data() -> dict[str, Any]:
    global _SITE_DATA_CACHE, _SITE_DATA_MTIME_NS

    current_mtime_ns = SITE_DATA_PATH.stat().st_mtime_ns
    if _SITE_DATA_CACHE is None or _SITE_DATA_MTIME_NS != current_mtime_ns:
        _SITE_DATA_CACHE = json.loads(SITE_DATA_PATH.read_text(encoding="utf-8"))
        _SITE_DATA_MTIME_NS = current_mtime_ns

    return _SITE_DATA_CACHE


def enrich_site_data(raw_site_data: dict[str, Any]) -> dict[str, Any]:
    site_data = deepcopy(raw_site_data)
    question_lookup: dict[str, dict[str, Any]] = {}
    question_index = 0

    for group in site_data.get("ability_groups", []):
        for question in group.get("questions", []):
            question_index += 1
            title = question.get("title", "")
            detail = deepcopy(QUESTION_DETAILS.get(title, {}))
            question.update(detail)

            question["question_id"] = f"question-{question_index:02d}"
            question["question_ref"] = f"{question['source']} {question['question_no']}"
            question.setdefault("statement", [question.get("prompt", "")])
            question.setdefault("options", [])
            question.setdefault("answer_label", "参考答案")
            question.setdefault(
                "answer_analysis",
                [question["answer"]] if question.get("answer") else [],
            )
            question_lookup[title] = deepcopy(question)

    for module in site_data.get("demo_modules", []):
        related_titles = DEMO_RELATED_QUESTIONS.get(module.get("id", ""), [])
        module["related_question_titles"] = related_titles
        module["related_questions"] = related_titles
        module["related_question_cards"] = [
            deepcopy(question_lookup[title])
            for title in related_titles
            if title in question_lookup
        ]

    site_data["question_lookup"] = question_lookup
    return site_data


def build_sidebar_groups(site_data: dict[str, Any], *, view_mode: str) -> list[dict[str, Any]]:
    groups = deepcopy(site_data.get("sidebar_groups", []))
    if view_mode == "teacher":
        return groups

    filtered_groups: list[dict[str, Any]] = []
    for group in groups:
        items = [
            item
            for item in group.get("items", [])
            if item.get("target_id") not in TEACHER_ONLY_SECTION_IDS
        ]
        if items:
            filtered_group = dict(group)
            filtered_group["items"] = items
            filtered_groups.append(filtered_group)
    return filtered_groups


def get_topic_page_context(*, view_mode: str = "student") -> dict[str, Any]:
    site_data = enrich_site_data(load_site_data())
    meta = site_data["meta"]
    is_teacher_view = view_mode == "teacher"
    if not is_teacher_view:
        site_data["demo_modules"] = []
    return {
        "page_title": meta["title"],
        "page_description": meta["subtitle"],
        "breadcrumb_items": [
            (
                {"label": "教师工作台", "href": "/teacher/students?tab=courses"}
                if is_teacher_view
                else {"label": "学生课程页", "href": "/student/courses"}
            ),
            (
                {"label": "C++ 课程页", "href": "/teacher/courses/cpp"}
                if is_teacher_view
                else {"label": "C++", "href": "/student/cpp"}
            ),
            (
                {"label": "GESP 教师分类", "href": "/teacher/courses/cpp"}
                if is_teacher_view
                else {"label": "GESP", "href": "/student/cpp/gesp"}
            ),
            (
                {"label": "GESP2 · 枚举法专题（教师版）"}
                if is_teacher_view
                else {"label": "GESP2", "href": "/student/cpp/gesp/gesp2"}
            ),
            {"label": meta["title"]},
        ],
        "topic_site_data": site_data,
        "topic_sidebar_groups": build_sidebar_groups(site_data, view_mode=view_mode),
        "topic_view_mode": view_mode,
        "show_teacher_support_sections": is_teacher_view,
        "show_teacher_demo": is_teacher_view,
        "topic_note": (
            "当前页面是教师版枚举法专题页：保留 Teaching Demo、Knowledge Overview、Common Pitfalls、Scope Boundary、Coverage 和 Teaching Notes，便于老师按专题直接备课。"
            if is_teacher_view
            else "当前页面是学生版枚举法专题页：保留完整题面、题组、模板和解析，教师讲课演示与备课辅助块已收回到教师版页面。"
        ),
    }
