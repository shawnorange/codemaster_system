from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from entry.question_queries import list_enumeration_method_questions
from entry.question_fallbacks import (
    build_enumeration_demo_modules,
    build_enumeration_main_question_groups,
    build_enumeration_question_lookup,
)

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
STUDENT_TOPIC_OVERVIEW = {
    "definition": "把候选对象按范围逐个检查，命中后再输出、计数或停止，这就是枚举法。",
    "summary": "这一页改成学生复盘版：先知道这一专题在学什么，再按分节看主例题，最后用同类题巩固。",
    "learning_targets": [
        "先说清自己在枚举谁，是一个 candidate、一位 digit，还是一对候选。",
        "写循环前先定边界，尤其要判断从 0 还是 1 开始，是否需要包含末端。",
        "命中条件后再决定动作：输出、计数、更新答案，还是在允许时提前 break。",
    ],
    "estimated_minutes": 12,
    "video_note": "暂未上传视频，可先按图文复盘。",
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


def _question_identity(question: dict[str, Any]) -> str:
    return str(
        question.get("code")
        or question.get("question_id")
        or question.get("title")
        or question.get("sort_order")
        or ""
    )


def _pick_demo_question(questions: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_questions = sorted(
        questions,
        key=lambda item: (
            item.get("sort_order", 0),
            item.get("id") or 0,
            item.get("title") or "",
        ),
    )
    demo_candidates = [question for question in ordered_questions if question.get("is_demo")]
    return deepcopy((demo_candidates or ordered_questions)[0])


def build_student_topic_review(
    site_data: dict[str, Any],
    question_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_groups = [
        group
        for _, group in sorted(
            enumerate(question_groups),
            key=lambda item: (
                min(
                    (
                        question.get("sort_order", 10**9)
                        for question in item[1].get("questions", [])
                        if question
                    ),
                    default=10**9,
                ),
                item[0],
            ),
        )
    ]
    sections: list[dict[str, Any]] = []
    total_questions = 0

    for index, group in enumerate(ordered_groups, start=1):
        questions = [deepcopy(question) for question in group.get("questions", []) if question]
        if not questions:
            continue

        demo_question = _pick_demo_question(questions)
        demo_identity = _question_identity(demo_question)
        demo_removed = False
        practice_questions: list[dict[str, Any]] = []

        for question in questions:
            if not demo_removed and _question_identity(question) == demo_identity:
                demo_removed = True
                continue
            practice_questions.append(question)

        total_questions += len(questions)
        focus_statement = (
            demo_question.get("ability_point")
            or group.get("goal")
            or group.get("summary")
            or "先定枚举对象，再写判定条件。"
        )
        pattern_statement = (
            demo_question.get("classification_reason")
            or group.get("summary")
            or "先看候选范围，再看命中条件。"
        )
        practice_hint = (
            f"这一节再用 {len(practice_questions)} 道同类题把边界、判定和输出顺序做扎实。"
            if practice_questions
            else "这一节目前没有额外练习题，先把主例题和规律复盘到能复述出来。"
        )

        sections.append(
            {
                "key": group.get("id") or f"group-{index}",
                "title": group.get("title") or f"第 {index} 节",
                "index": index,
                "section_label": f"第 {index} 节",
                "summary": group.get("summary") or focus_statement,
                "study_goal": group.get("goal") or focus_statement,
                "focus_statement": focus_statement,
                "pattern_statement": pattern_statement,
                "practice_hint": practice_hint,
                "question_count": len(questions),
                "practice_count": len(practice_questions),
                "demo_question": demo_question,
                "practice_questions": practice_questions,
                "prev_section_key": None,
                "prev_section_title": "",
                "next_section_key": None,
                "next_section_title": "",
            }
        )

    sequence = [{"key": "overview", "title": "专题导览"}] + [
        {"key": section["key"], "title": section["title"]}
        for section in sections
    ]

    for index, section in enumerate(sections, start=1):
        previous_item = sequence[index - 1]
        next_item = sequence[index + 1] if index + 1 < len(sequence) else None
        section["prev_section_key"] = previous_item["key"]
        section["prev_section_title"] = previous_item["title"]
        section["next_section_key"] = next_item["key"] if next_item else None
        section["next_section_title"] = next_item["title"] if next_item else ""

    navigation = [
        {
            "key": "overview",
            "title": "专题导览",
            "subtitle": "先知道这次怎么复盘",
            "badge": f"{len(sections)} 节",
        }
    ]
    navigation.extend(
        {
            "key": section["key"],
            "title": section["title"],
            "subtitle": section["study_goal"],
            "badge": f"{section['question_count']} 题",
        }
        for section in sections
    )

    first_section_key = sections[0]["key"] if sections else None
    overview = {
        "title": site_data.get("meta", {}).get("title") or "枚举法",
        "summary": STUDENT_TOPIC_OVERVIEW["summary"],
        "definition": STUDENT_TOPIC_OVERVIEW["definition"],
        "learning_targets": list(STUDENT_TOPIC_OVERVIEW["learning_targets"]),
        "review_sequence": [section["title"] for section in sections],
        "estimated_minutes": STUDENT_TOPIC_OVERVIEW["estimated_minutes"],
        "video_note": STUDENT_TOPIC_OVERVIEW["video_note"],
        "section_count": len(sections),
        "question_count": total_questions,
        "first_section_key": first_section_key,
        "prev_section_key": None,
        "next_section_key": first_section_key,
        "next_section_title": sections[0]["title"] if sections else "",
    }

    return {
        "content_slug": "enumeration-method",
        "topic_title": overview["title"],
        "overview": overview,
        "sections": sections,
        "navigation": navigation,
        "default_section_key": "overview",
    }


def get_topic_page_context(*, view_mode: str = "student") -> dict[str, Any]:
    site_data = enrich_site_data(load_site_data())
    meta = site_data["meta"]
    is_teacher_view = view_mode == "teacher"
    db_questions = list_enumeration_method_questions()
    topic_main_question_groups = build_enumeration_main_question_groups(site_data, db_questions)
    topic_main_question_source = "db" if db_questions else "static"
    topic_main_question_lookup = build_enumeration_question_lookup(topic_main_question_groups)
    site_data["question_lookup"] = topic_main_question_lookup
    site_data["demo_modules"] = build_enumeration_demo_modules(
        site_data.get("demo_modules", []),
        topic_main_question_lookup,
    )
    if not is_teacher_view:
        site_data["demo_modules"] = []
    topic_review_data = build_student_topic_review(site_data, topic_main_question_groups)
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
        "topic_main_question_groups": topic_main_question_groups,
        "topic_main_question_source": topic_main_question_source,
        "topic_review_data": topic_review_data,
        "topic_note": (
            "当前页面是教师版枚举法专题页：题目区已改为数据库优先、静态兜底，同时保留 Teaching Demo、Knowledge Overview、Common Pitfalls、Scope Boundary、Coverage 和 Teaching Notes，便于老师按专题直接备课。"
            if is_teacher_view
            else "当前页面是学生复盘版枚举法专题页：题目区仍然数据库优先、静态兜底，但学生视图已改成专题导览 + 分节学习 + 顺序切换，教师演示与备课辅助块继续只保留在教师版。"
        ),
    }
