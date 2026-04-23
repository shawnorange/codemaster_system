from __future__ import annotations

from collections.abc import Iterable


CONTENT_PERMISSION_C1 = "C1"
CONTENT_PERMISSION_C2 = "C2"
CONTENT_PERMISSION_C3 = "C3"
CONTENT_PERMISSION_C4 = "C4"

CONTENT_PERMISSION_ORDER = (
    CONTENT_PERMISSION_C1,
    CONTENT_PERMISSION_C2,
    CONTENT_PERMISSION_C3,
    CONTENT_PERMISSION_C4,
)
CONTENT_PERMISSION_CHOICES = [(code, code) for code in CONTENT_PERMISSION_ORDER]
CONTENT_PERMISSION_RANK_MAP = {
    code: index for index, code in enumerate(CONTENT_PERMISSION_ORDER, start=1)
}

CPP_STAGE_GROUPS = {
    CONTENT_PERMISSION_C1: ("GESP1", "GESP2", "GESP3", "GESP4"),
    CONTENT_PERMISSION_C2: ("GESP5", "GESP6", "GESP7", "GESP8"),
    CONTENT_PERMISSION_C3: ("CSP-J", "CSPJ"),
    CONTENT_PERMISSION_C4: ("CSP-S", "CSPS"),
}
CPP_STAGE_PERMISSION_CODE_MAP = {
    stage_code: permission_code
    for permission_code, stage_codes in CPP_STAGE_GROUPS.items()
    for stage_code in stage_codes
}


def normalize_stage_code(value: str | None) -> str:
    normalized = (value or "").strip().upper()
    normalized = normalized.replace("—", "-").replace("–", "-").replace("_", "-")
    normalized = normalized.replace(" ", "")
    return normalized


def normalize_permission_code(value: str | None) -> str:
    normalized = normalize_stage_code(value)
    return normalized if normalized in CONTENT_PERMISSION_RANK_MAP else ""


def infer_cpp_permission_code(stage_code: str | None) -> str:
    normalized = normalize_stage_code(stage_code)
    if normalized in CONTENT_PERMISSION_RANK_MAP:
        return normalized
    return CPP_STAGE_PERMISSION_CODE_MAP.get(normalized, "")


def infer_content_permission_code(
    course_slug: str,
    *,
    permission_code: str | None = None,
    level_code: str | None = None,
    phase: str | None = None,
) -> str:
    normalized_permission_code = normalize_permission_code(permission_code)
    if normalized_permission_code:
        return normalized_permission_code
    if course_slug == "cpp":
        return infer_cpp_permission_code(level_code) or infer_cpp_permission_code(phase)
    return ""


def get_visible_permission_codes(level_code: str | None) -> set[str]:
    normalized_level_code = normalize_permission_code(level_code)
    if not normalized_level_code:
        return set()

    max_rank = CONTENT_PERMISSION_RANK_MAP[normalized_level_code]
    return {
        code
        for code, rank in CONTENT_PERMISSION_RANK_MAP.items()
        if rank <= max_rank
    }


def get_visible_cpp_stage_codes(level_code: str | None) -> set[str]:
    normalized_level_code = normalize_permission_code(level_code) or infer_cpp_permission_code(level_code)
    if not normalized_level_code:
        return set()

    visible_stage_codes: set[str] = set()
    for permission_code in CONTENT_PERMISSION_ORDER:
        visible_stage_codes.update(CPP_STAGE_GROUPS.get(permission_code, ()))
        if permission_code == normalized_level_code:
            break
    return visible_stage_codes


def permission_code_allows(level_code: str | None, permission_code: str | None) -> bool:
    normalized_level_code = normalize_permission_code(level_code)
    normalized_permission_code = normalize_permission_code(permission_code)
    if not normalized_level_code or not normalized_permission_code:
        return False
    return CONTENT_PERMISSION_RANK_MAP[normalized_permission_code] <= CONTENT_PERMISSION_RANK_MAP[normalized_level_code]


def pick_highest_permission_code(level_codes: Iterable[str | None]) -> str:
    highest_code = ""
    highest_rank = 0
    for level_code in level_codes:
        normalized_level_code = normalize_permission_code(level_code)
        rank = CONTENT_PERMISSION_RANK_MAP.get(normalized_level_code, 0)
        if rank > highest_rank:
            highest_code = normalized_level_code
            highest_rank = rank
    return highest_code
