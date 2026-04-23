from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from entry.question_fallbacks import build_array_2d_db_site_data
from entry.question_queries import list_array_2d_questions

from .student_review import build_student_review_data

TOPIC_DIR = Path(__file__).resolve().parent
SITE_DATA_PATH = TOPIC_DIR / "site-data.json"
_SITE_DATA_CACHE: dict[str, Any] | None = None
_SITE_DATA_MTIME_NS: int | None = None


def load_site_data() -> dict[str, Any]:
    global _SITE_DATA_CACHE, _SITE_DATA_MTIME_NS

    current_mtime_ns = SITE_DATA_PATH.stat().st_mtime_ns
    if _SITE_DATA_CACHE is None or _SITE_DATA_MTIME_NS != current_mtime_ns:
        _SITE_DATA_CACHE = json.loads(SITE_DATA_PATH.read_text(encoding="utf-8"))
        _SITE_DATA_MTIME_NS = current_mtime_ns

    return _SITE_DATA_CACHE


def get_topic_page_context(lecture_id: str | None = None) -> dict[str, Any]:
    db_questions = list_array_2d_questions()
    static_site_data = load_site_data()
    site_data = build_array_2d_db_site_data(static_site_data, db_questions)
    topic_main_question_source = "db" if db_questions else "static"
    lectures = site_data.get("lectures", [])
    lecture_map = {lecture["id"]: lecture for lecture in lectures}
    current_lecture = lecture_map.get(lecture_id) if lecture_id else None
    page_mode = "lecture" if lecture_id else "home"

    page_title = current_lecture["title"] if current_lecture else site_data["meta"]["title"]
    page_description = current_lecture["summary"] if current_lecture else site_data["meta"]["subtitle"]
    topic_note = (
        "当前页面已切到数据库优先、静态兜底；专题首页和讲次详情继续沿用同一路由下的轻量参数模式。"
        if page_mode == "home"
        else f"当前处于讲次内容模式：{current_lecture['title'] if current_lecture else '第1讲'}，题目数据采用数据库优先、静态兜底。"
    )

    return {
        "topic_page_mode": page_mode,
        "topic_site_data": site_data,
        "topic_current_lecture": current_lecture,
        "topic_main_question_source": topic_main_question_source,
        "topic_note": topic_note,
        "page_title": page_title,
        "page_description": page_description,
        "breadcrumb_items": [
            {"label": "学生课程页", "href": "/student/courses"},
            {"label": "C++", "href": "/student/cpp"},
            {"label": "GESP", "href": "/student/cpp/gesp"},
            {"label": "GESP4", "href": "/student/cpp/gesp/gesp4"},
            {"label": "二维数组专题"},
        ],
        "topic_slug": "array-2d",
        "topic_site_data_script_id": "gesp4-array-2d-site-data",
        "topic_badge_text": "GESP4 / 二维数组专题",
    }


def get_student_topic_page_context() -> dict[str, Any]:
    site_data = load_site_data()
    db_questions = list_array_2d_questions()
    topic_review_data, topic_main_question_source = build_student_review_data(site_data, db_questions)
    meta = site_data.get("meta", {})

    return {
        "page_title": meta.get("title") or "GESP4 二维数组专题",
        "page_description": meta.get("subtitle") or "二维数组专题学生复盘页",
        "breadcrumb_items": [
            {"label": "学生课程页", "href": "/student/courses"},
            {"label": "C++", "href": "/student/cpp"},
            {"label": "GESP", "href": "/student/cpp/gesp"},
            {"label": "GESP4", "href": "/student/cpp/gesp/gesp4"},
            {"label": "二维数组专题"},
        ],
        "topic_slug": "array-2d",
        "topic_note": (
            "当前页面已切到学生复盘版：主区优先读取 questions 表中 `level_code=GESP4` + `content_slug=array-2d` 的题目，再按知识点组归类展示；数据库为空时才退回静态题库。"
        ),
        "topic_review_data": topic_review_data,
        "topic_main_question_source": topic_main_question_source,
    }
