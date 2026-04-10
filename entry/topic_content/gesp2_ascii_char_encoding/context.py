from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from entry.question_queries import list_ascii_char_encoding_questions


TOPIC_DIR = Path(__file__).resolve().parent
FALLBACK_QUESTIONS_PATH = TOPIC_DIR / "fallback-questions.json"
_FALLBACK_QUESTIONS_CACHE: list[dict[str, Any]] | None = None
_FALLBACK_QUESTIONS_MTIME_NS: int | None = None

QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "judgement": "判断题",
    "programming": "编程题",
}

DIFFICULTY_LABELS = {
    "basic": "基础",
    "basic_plus": "基础进阶",
}

ABILITY_POINT_DEFINITIONS = [
    {
        "id": "chars-and-integers",
        "ability_point": "字符与整数的本质关系",
        "title": "字符与整数的本质关系",
        "summary": "先建立“字符本质上是编码值”的核心认知，再理解参与算术表达式后的输出类型。",
        "goal": "学生能分清字符本身、字符编码值和表达式输出结果之间的区别。",
    },
    {
        "id": "char-digit-conversion",
        "ability_point": "字符与数字的双向转换",
        "title": "字符与数字的双向转换",
        "summary": "围绕 '0' 偏移法，处理数字字符转整数、整数转字符和字符数字求值。",
        "goal": "学生能稳定写出 b - '0'、a + '0' 这类转换表达式。",
    },
    {
        "id": "char-range-checks",
        "ability_point": "字符区间判断",
        "title": "字符区间判断",
        "summary": "利用编码区间连续性判断小写字母、数字字符等常见字符类别。",
        "goal": "学生能写对两端比较加 && 的标准区间判断式。",
    },
    {
        "id": "char-order-and-comparison",
        "ability_point": "字符比较与编码顺序",
        "title": "字符比较与编码顺序",
        "summary": "通过比较、后移、自增等题型，理解字符之间的先后关系来自编码顺序。",
        "goal": "学生能判断字符比较、加一后移和未赋值表达式的差异。",
    },
    {
        "id": "case-and-offset",
        "ability_point": "大小写转换与字母偏移",
        "title": "大小写转换与字母偏移",
        "summary": "把大小写转换、数字映射字母等题型统一为“固定偏移”的思路。",
        "goal": "学生能把大小写转换和字母序号映射视作同一类偏移问题。",
    },
    {
        "id": "encoding-driven-output",
        "ability_point": "基于字符编码规律的构造输出",
        "title": "基于字符编码规律的构造输出",
        "summary": "编程题和构造题的核心在于把计数器映射到字符编码区间，并循环回绕。",
        "goal": "学生能从字符偏移构造出循环字母输出，而不是硬编码字符表。",
    },
]

ABILITY_POINT_MAP = {
    item["ability_point"]: item
    for item in ABILITY_POINT_DEFINITIONS
}

TEACHER_REVIEW_UPDATES = [
    "questions 单表结构保持不变；本次只修正了会影响导入或与原卷冲突的最小字段。",
    "8 道判断题统一回到现有导入命令支持的 judgement 类型，避免导入报错。",
    "2024 年 3 月判断题第 6 题答案修正为 T，并按原题重新整理解析。",
    "ability_point 统一收敛为 6 组命名，直接对应本次 md 汇总的知识点分组。",
]

TEACHER_BOUNDARY_WATCH_ITEMS = [
    {
        "code": "gesp2-ascii-2023-03-sc07",
        "title": "字符变量与字符字面量运算",
        "reason": "题面带有较强语法/左值判断色彩，但仍建立在“字符可按编码参与运算”的认知上，先保留正式导入。",
    },
    {
        "code": "gesp2-ascii-2024-09-sc06",
        "title": "混合类型中的字符编码值",
        "reason": "命题主轴更接近混合类型与输入输出，但字符 ASCII 值确实是关键干扰项，建议保留并标注边界观察。",
    },
]

OUTLINE_HIGHLIGHTS = [
    "GESP 二级思维导图中明确列出 ASCII 编码，说明该知识点属于 GESP2 范围内的正式内容。",
    "ASCII 编码与数据类型转换、常用数学函数、多层结构并列出现，更适合作为独立知识点，而不是挂靠到其他 slug 下。",
]

SOURCE_DOCUMENTS = [
    "project_inputs/gesp2_reference/ASCIImd汇总版.md",
    "project_inputs/gesp2_reference/ASCIIjson导入版.json",
    "project_inputs/gesp2_exam_sources/2级2023年3月.pdf 至 2级2025年9月.pdf（ASCII 相关题抽样复核）",
    "project_inputs/gesp2_outline/GESP二级思维导图.png",
]


def load_fallback_questions() -> list[dict[str, Any]]:
    global _FALLBACK_QUESTIONS_CACHE, _FALLBACK_QUESTIONS_MTIME_NS

    current_mtime_ns = FALLBACK_QUESTIONS_PATH.stat().st_mtime_ns
    if _FALLBACK_QUESTIONS_CACHE is None or _FALLBACK_QUESTIONS_MTIME_NS != current_mtime_ns:
        raw_data = json.loads(FALLBACK_QUESTIONS_PATH.read_text(encoding="utf-8"))
        _FALLBACK_QUESTIONS_CACHE = raw_data.get("questions", [])
        _FALLBACK_QUESTIONS_MTIME_NS = current_mtime_ns

    return deepcopy(_FALLBACK_QUESTIONS_CACHE)


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()
    return [text] if text else []


def _normalize_options(value: Any) -> list[dict[str, str]]:
    if not value:
        return []

    normalized_options: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            text = str(item.get("text") or "").strip()
        else:
            key = ""
            text = str(item).strip()
        if key or text:
            normalized_options.append({"key": key, "text": text})
    return normalized_options


def _format_source_label(source_year: Any, source_month: Any) -> str:
    if not source_year or not source_month:
        return "来源待补充"
    return f"{source_year} 年 {source_month} 月"


def _format_question_no(value: Any) -> str:
    if not value:
        return ""
    return f"第 {value} 题"


def _record_to_page_question(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    difficulty = str(payload.get("difficulty") or "").strip()
    return {
        "code": str(record.get("code") or "").strip(),
        "title": str(record.get("title") or "").strip(),
        "question_type_label": QUESTION_TYPE_LABELS.get(
            str(record.get("question_type") or "").strip(),
            str(record.get("question_type") or "").strip(),
        ),
        "source": _format_source_label(record.get("source_year"), record.get("source_month")),
        "question_no": _format_question_no(record.get("source_question_no")),
        "difficulty": DIFFICULTY_LABELS.get(difficulty, difficulty or "未标注"),
        "ability_point": str(payload.get("ability_point") or "").strip(),
        "statement": _normalize_text_list(payload.get("statement")),
        "options": _normalize_options(payload.get("options")),
        "code_text": str(payload.get("code") or "").strip(),
        "answer_label": str(payload.get("answer_label") or "").strip(),
        "answer_analysis": _normalize_text_list(payload.get("answer_analysis")),
        "input_format": str(payload.get("input_format") or "").strip(),
        "output_format": str(payload.get("output_format") or "").strip(),
        "sample_input": str(payload.get("sample_input") or "").strip(),
        "sample_output": str(payload.get("sample_output") or "").strip(),
        "source_year": record.get("source_year"),
        "source_month": record.get("source_month"),
        "sort_order": record.get("sort_order") or 0,
    }


def build_ascii_question_groups(question_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group_map = {
        item["ability_point"]: {
            "id": item["id"],
            "title": item["title"],
            "summary": item["summary"],
            "goal": item["goal"],
            "questions": [],
        }
        for item in ABILITY_POINT_DEFINITIONS
    }

    ordered_records = sorted(
        question_records,
        key=lambda item: (
            item.get("sort_order", 0),
            item.get("source_year") or 0,
            item.get("source_month") or 0,
            item.get("source_question_no") or 0,
            item.get("code") or "",
        ),
    )

    for record in ordered_records:
        question = _record_to_page_question(record)
        ability_point = question["ability_point"]
        group = group_map.get(ability_point)
        if group is None:
            fallback_group = group_map.setdefault(
                ability_point or "未分组题目",
                {
                    "id": f"fallback-{len(group_map) + 1}",
                    "title": ability_point or "未分组题目",
                    "summary": "该分组来自 questions 表中的补充题目。",
                    "goal": "保持题库内容完整。",
                    "questions": [],
                },
            )
            group = fallback_group
        group["questions"].append(question)

    grouped_items: list[dict[str, Any]] = []
    for item in ABILITY_POINT_DEFINITIONS:
        group = group_map[item["ability_point"]]
        if group["questions"]:
            grouped_items.append(group)

    for ability_point, group in group_map.items():
        if ability_point in ABILITY_POINT_MAP:
            continue
        if group["questions"]:
            grouped_items.append(group)

    return grouped_items


def build_summary_cards(
    question_groups: list[dict[str, Any]],
    *,
    question_source: str,
) -> list[dict[str, str]]:
    total_questions = sum(len(group.get("questions", [])) for group in question_groups)
    covered_sources = {
        (question.get("source_year"), question.get("source_month"))
        for group in question_groups
        for question in group.get("questions", [])
        if question.get("source_year") and question.get("source_month")
    }
    return [
        {"label": "能力板块", "value": f"{len(question_groups)} 组", "hint": "按能力点而不是按年份堆叠"},
        {"label": "正式题目", "value": f"{total_questions} 道", "hint": "questions 表优先，静态题库兜底"},
        {"label": "覆盖试卷", "value": f"{len(covered_sources)} 套", "hint": "已抽样复核的 GESP2 原卷来源"},
        {
            "label": "当前题源",
            "value": "数据库" if question_source == "db" else "静态兜底",
            "hint": "数据库为空时自动回退到仓库内静态题库",
        },
    ]


def build_sidebar_groups(*, view_mode: str) -> list[dict[str, Any]]:
    groups = [
        {
            "title": "专题概览",
            "items": [
                {"label": "知识点说明", "href": "#topic-overview"},
                {"label": "能力板块", "href": "#ability-map"},
                {"label": "真题题库", "href": "#question-bank"},
            ],
        },
    ]
    if view_mode == "teacher":
        groups.append(
            {
                "title": "教师辅助区",
                "items": [
                    {"label": "复核结论", "href": "#review-notes"},
                    {"label": "边界观察题", "href": "#boundary-watch"},
                    {"label": "输入来源", "href": "#source-documents"},
                ],
            }
        )
    return groups


def get_topic_page_context(*, view_mode: str = "student") -> dict[str, Any]:
    is_teacher_view = view_mode == "teacher"
    db_questions = list_ascii_char_encoding_questions()
    question_source = "db" if db_questions else "static"
    question_groups = build_ascii_question_groups(db_questions or load_fallback_questions())

    topic_note = (
        "当前页面是教师版 ASCII 编码知识点页：题目主区优先读取 questions 表，数据库为空时退回静态题库，同时保留人工复核要点和边界观察题。"
        if is_teacher_view
        else "当前页面是学生版 ASCII 编码知识点页：题目主区优先读取 questions 表，数据库为空时自动使用静态题库兜底。"
    )

    return {
        "page_title": "ASCII 编码",
        "page_description": "GESP2 ASCII 编码知识点页，围绕字符与整数、字符区间判断、大小写偏移和典型真题展开。",
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
                {"label": "GESP2 · ASCII 编码（教师版）"}
                if is_teacher_view
                else {"label": "GESP2", "href": "/student/cpp/gesp/gesp2"}
            ),
            {"label": "ASCII 编码"},
        ],
        "topic_meta": {
            "eyebrow": "GESP2 / ASCII Encoding",
            "subtitle": "字符与编码值的对应关系、固定偏移、区间判断与构造输出。",
            "hero_note": "本页基于已整理的 md + json 结果接入，questions 表为主维护源，静态题库仅作兜底。",
        },
        "topic_summary_cards": build_summary_cards(question_groups, question_source=question_source),
        "topic_ability_cards": deepcopy(ABILITY_POINT_DEFINITIONS),
        "topic_main_question_groups": question_groups,
        "topic_main_question_source": question_source,
        "topic_main_question_source_text": "数据库" if question_source == "db" else "静态兜底",
        "topic_outline_highlights": OUTLINE_HIGHLIGHTS,
        "topic_source_documents": SOURCE_DOCUMENTS,
        "topic_review_updates": TEACHER_REVIEW_UPDATES,
        "topic_boundary_watch_items": TEACHER_BOUNDARY_WATCH_ITEMS,
        "topic_sidebar_groups": build_sidebar_groups(view_mode=view_mode),
        "topic_view_mode": view_mode,
        "show_teacher_support_sections": is_teacher_view,
        "topic_note": topic_note,
    }
