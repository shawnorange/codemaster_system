from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
    site_data = load_site_data()
    lectures = site_data.get("lectures", [])
    lecture_map = {lecture["id"]: lecture for lecture in lectures}
    current_lecture = lecture_map.get(lecture_id) if lecture_id else None
    page_mode = "lecture" if lecture_id else "home"

    page_title = current_lecture["title"] if current_lecture else site_data["meta"]["title"]
    page_description = current_lecture["summary"] if current_lecture else site_data["meta"]["subtitle"]
    topic_note = (
        "当前页面已接入真实专题首页内容，讲次详情继续沿用同一路由下的轻量参数模式。"
        if page_mode == "home"
        else f"当前处于讲次内容模式：{current_lecture['title'] if current_lecture else '第1讲'}。"
    )

    return {
        "topic_page_mode": page_mode,
        "topic_site_data": site_data,
        "topic_current_lecture": current_lecture,
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
    }
