from __future__ import annotations

import re
from typing import Any


CONTROL_FLOW_RE = re.compile(r"\b(?:for|while|if)\s*\(")
FUNCTION_DEF_RE = re.compile(
    r"\b(?:void|int|long|double|float|bool|char|string|auto)\s+[A-Za-z_]\w*\s*\([^)]*\)\s*\{?"
)
CODE_TOKEN_RE = re.compile(
    r"#include|using\s+namespace|std::|cout\b|cin\b|printf\s*\(|scanf\s*\(|return\b|break\b|continue\b"
)
IDENTIFIER_OPERATOR_RE = re.compile(r"\b[A-Za-z_]\w*\b.*(?:==|!=|<=|>=|<<|>>|[=+\-*/%<>]).*\b[A-Za-z_0-9]\w*\b")


def normalize_option_text(value: Any) -> str:
    lines: list[str] = []
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def is_probably_code_option(value: Any) -> bool:
    text = normalize_option_text(value)
    if not text:
        return False

    score = 0
    if CONTROL_FLOW_RE.search(text):
        score += 3
    if FUNCTION_DEF_RE.search(text):
        score += 3
    if CODE_TOKEN_RE.search(text):
        score += 2
    if "{" in text or "}" in text:
        score += 2
    semicolon_count = text.count(";")
    if semicolon_count >= 2:
        score += 2
    elif semicolon_count == 1:
        score += 1
    if IDENTIFIER_OPERATOR_RE.search(text):
        score += 1
    if "\n" in text and any(line.endswith(("{", ";", "}")) for line in text.splitlines()):
        score += 1
    return score >= 3


def format_homework_option_display(value: Any) -> dict[str, Any]:
    normalized = normalize_option_text(value)
    is_code_option = is_probably_code_option(normalized)
    return {
        "text": normalized,
        "display_text": format_code_option(normalized) if is_code_option else normalized,
        "is_code_option": is_code_option,
    }


def format_code_option(value: Any) -> str:
    normalized = normalize_option_text(value)
    if not normalized:
        return ""
    structured = _insert_structural_breaks(normalized)
    return _indent_code_lines(structured)


def _insert_structural_breaks(source: str) -> str:
    chunks: list[str] = []
    paren_depth = 0

    for char in source:
        if char == "\n":
            if chunks and chunks[-1] != "\n":
                chunks.append("\n")
            continue

        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(paren_depth - 1, 0)

        if char == "{":
            if chunks and chunks[-1] not in {" ", "\n"}:
                chunks.append(" ")
            chunks.append("{")
            chunks.append("\n")
            continue

        if char == "}":
            if chunks and chunks[-1] != "\n":
                chunks.append("\n")
            chunks.append("}")
            chunks.append("\n")
            continue

        if char == ";" and paren_depth == 0:
            chunks.append(";")
            chunks.append("\n")
            continue

        chunks.append(char)

    return "".join(chunks)


def _indent_code_lines(source: str) -> str:
    formatted_lines: list[str] = []
    indent = 0

    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        leading_close_count = len(line) - len(line.lstrip("}"))
        if leading_close_count:
            indent = max(indent - leading_close_count, 0)

        formatted_lines.append(f"{'    ' * indent}{line}")
        indent = max(indent + line.count("{") - line.count("}") + leading_close_count, 0)

    return "\n".join(formatted_lines)
