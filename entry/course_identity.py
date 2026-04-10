from __future__ import annotations

from collections.abc import Iterable


COURSE_TITLES = {
    "cpp": "C++",
    "uav": "无人机",
    "scratch": "Scratch",
    "pbl": "PBL",
}

COURSE_NAME_SLUG_ALIASES = {
    "c++": "cpp",
    "cpp": "cpp",
    "无人机": "uav",
    "uav": "uav",
    "drone": "uav",
    "scratch": "scratch",
    "pbl": "pbl",
}

CPP_LEVEL_CODE_MAP = {
    "C1": "C1",
    "C2": "C2",
    "C3": "C3",
    "C4": "C4",
    "GESP1": "C1",
    "GESP2": "C2",
    "GESP3": "C3",
    "GESP4": "C4",
}

UAV_LEVEL_CODE_MAP = {
    "S1": "S1",
    "S2": "S2",
    "S3": "S3",
}


def normalize_value(value: str | None) -> str:
    return (value or "").strip()


def get_course_title_from_slug(course_slug: str, *, default: str = "") -> str:
    return COURSE_TITLES.get(course_slug, default or course_slug)


def resolve_course_slug(raw_course_name: str = "", raw_level_code: str = "") -> str | None:
    normalized_course_name = normalize_value(raw_course_name).lower()
    if normalized_course_name in COURSE_NAME_SLUG_ALIASES:
        return COURSE_NAME_SLUG_ALIASES[normalized_course_name]

    normalized_level_code = normalize_value(raw_level_code).upper()
    if normalized_level_code in CPP_LEVEL_CODE_MAP:
        return "cpp"
    if normalized_level_code in UAV_LEVEL_CODE_MAP:
        return "uav"
    return None


def normalize_assignment_level(course_slug: str, raw_level_code: str) -> str | None:
    normalized_level_code = normalize_value(raw_level_code).upper()
    if not normalized_level_code:
        return None
    if course_slug == "cpp":
        return CPP_LEVEL_CODE_MAP.get(normalized_level_code)
    if course_slug == "uav":
        return UAV_LEVEL_CODE_MAP.get(normalized_level_code)
    return normalized_level_code


def format_course_level_label(course_title: str, level_code: str | None) -> str:
    normalized_level_code = normalize_value(level_code)
    if normalized_level_code:
        return f"{course_title} > {normalized_level_code}"
    return course_title


def summarize_course_level_labels(
    labels: Iterable[tuple[str, str | None]],
    *,
    separator: str = "；",
    empty: str = "课程待分配",
) -> str:
    seen: list[str] = []
    for course_title, level_code in labels:
        label = format_course_level_label(course_title, level_code)
        if label not in seen:
            seen.append(label)
    if not seen:
        return empty
    return separator.join(seen)
