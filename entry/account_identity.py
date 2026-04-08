from __future__ import annotations

import re
import zlib


def normalize_phone(phone: str | None) -> str:
    return re.sub(r"\D", "", (phone or "").strip())


def build_student_default_username(display_name: str, parent_phone: str | None = None) -> str:
    normalized_name = (display_name or "").strip()
    normalized_phone = normalize_phone(parent_phone)
    identity_key = f"{normalized_name}|{normalized_phone}"
    suffix = zlib.crc32(identity_key.encode("utf-8")) % 10000
    if normalized_phone:
        return f"student_{normalized_phone}_{suffix:04d}"
    return f"student_{suffix:04d}"


def ensure_unique_username(base_username: str, used_usernames: set[str], *, max_length: int = 64) -> str:
    candidate = base_username[:max_length]
    if candidate not in used_usernames:
        used_usernames.add(candidate)
        return candidate

    index = 2
    while True:
        suffix = f"_{index}"
        trimmed = base_username[: max_length - len(suffix)]
        candidate = f"{trimmed}{suffix}"
        if candidate not in used_usernames:
            used_usernames.add(candidate)
            return candidate
        index += 1
