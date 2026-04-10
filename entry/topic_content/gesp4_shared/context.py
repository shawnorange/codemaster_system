from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TOPIC_ROOT = Path(__file__).resolve().parent.parent
TOPIC_CONFIG = {
    "binary-search": {
        "site_data_path": TOPIC_ROOT / "gesp4_binary_search" / "site-data.json",
        "display_name": "二分查找专题",
    },
    "sorting": {
        "site_data_path": TOPIC_ROOT / "gesp4_sorting" / "site-data.json",
        "display_name": "排序专题",
    },
    "strings": {
        "site_data_path": TOPIC_ROOT / "gesp4_strings" / "site-data.json",
        "display_name": "字符串专题",
    },
}

_SITE_DATA_CACHE: dict[str, dict[str, Any]] = {}
_SITE_DATA_MTIME_NS: dict[str, int] = {}


def load_site_data(topic_slug: str) -> dict[str, Any]:
    config = TOPIC_CONFIG[topic_slug]
    site_data_path = config["site_data_path"]
    current_mtime_ns = site_data_path.stat().st_mtime_ns
    if topic_slug not in _SITE_DATA_CACHE or _SITE_DATA_MTIME_NS.get(topic_slug) != current_mtime_ns:
        _SITE_DATA_CACHE[topic_slug] = json.loads(site_data_path.read_text(encoding="utf-8"))
        _SITE_DATA_MTIME_NS[topic_slug] = current_mtime_ns
    return _SITE_DATA_CACHE[topic_slug]


def get_topic_page_context(
    topic_slug: str,
    *,
    lecture_id: str | None = None,
    breadcrumb_items: list[dict[str, str]] | None = None,
    topic_note: str | None = None,
) -> dict[str, Any]:
    site_data = load_site_data(topic_slug)
    lectures = site_data.get("lectures", [])
    lecture_map = {lecture["id"]: lecture for lecture in lectures}
    current_lecture = lecture_map.get(lecture_id) if lecture_id else None
    page_mode = "lecture" if lecture_id else "home"

    page_title = current_lecture["title"] if current_lecture else site_data["meta"]["title"]
    page_description = current_lecture["summary"] if current_lecture else site_data["meta"]["subtitle"]
    note = topic_note or (
        "当前专题页已接入教师端真实教学页结构，保留专题导读、讲次拆分和教学说明。"
        if page_mode == "home"
        else f"当前处于讲次内容模式：{current_lecture['title'] if current_lecture else '第1讲'}。"
    )

    return {
        "topic_page_mode": page_mode,
        "topic_site_data": site_data,
        "topic_current_lecture": current_lecture,
        "topic_main_question_source": "static",
        "topic_note": note,
        "page_title": page_title,
        "page_description": page_description,
        "breadcrumb_items": breadcrumb_items or [{"label": "教师工作台"}],
        "topic_slug": topic_slug,
        "topic_site_data_script_id": f"gesp4-{topic_slug}-site-data",
        "topic_badge_text": f"GESP4 / {TOPIC_CONFIG[topic_slug]['display_name']}",
    }
