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
FENCED_CODE_BLOCK_RE = re.compile(r"```(?:\s*(?:cpp|c\+\+))?\s*\n?(.*?)```", re.IGNORECASE | re.DOTALL)
CPP_CODE_START_RE = re.compile(
    r"#include|using\s+namespace\s+std|std::|int\s+main\s*\(|cout\b|cin\b|for\s*\(|while\s*\(|if\s*\(|return\b",
    re.IGNORECASE,
)
STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def normalize_option_text(value: Any) -> str:
    return normalize_homework_plain_text(value)


def normalize_homework_source_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_homework_plain_text(value: Any) -> str:
    lines: list[str] = []
    pending_blank = False

    for raw_line in normalize_homework_source_text(value).split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line:
            if pending_blank and lines:
                lines.append("")
            lines.append(line)
            pending_blank = False
        elif lines:
            pending_blank = True
    return "\n".join(lines).strip()


def is_probably_code_option(value: Any) -> bool:
    text = normalize_homework_plain_text(value)
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


def build_homework_content_display(value: Any) -> dict[str, Any]:
    source = normalize_homework_source_text(value)
    if not source:
        return {
            "text": "",
            "blocks": [],
            "is_code_content": False,
        }

    fenced_blocks = _extract_fenced_code_blocks(source)
    if fenced_blocks:
        return {
            "text": source,
            "blocks": fenced_blocks,
            "is_code_content": any(block["kind"] == "code" for block in fenced_blocks),
        }

    mixed_blocks = _split_mixed_cpp_content(source)
    if mixed_blocks:
        return {
            "text": source,
            "blocks": mixed_blocks,
            "is_code_content": any(block["kind"] == "code" for block in mixed_blocks),
        }

    normalized = normalize_homework_plain_text(source)
    is_code_content = is_probably_code_option(normalized)
    return {
        "text": normalized,
        "blocks": [
            {
                "kind": "code" if is_code_content else "text",
                "text": format_code_option(normalized) if is_code_content else normalized,
            }
        ],
        "is_code_content": is_code_content,
    }


def format_homework_option_display(value: Any) -> dict[str, Any]:
    normalized = normalize_homework_plain_text(value)
    is_code_option = is_probably_code_option(normalized)
    return {
        "text": normalized,
        "display_text": format_code_option(normalized) if is_code_option else normalized,
        "is_code_option": is_code_option,
    }


def format_code_option(value: Any) -> str:
    normalized = normalize_homework_plain_text(value)
    if not normalized:
        return ""
    structured = _insert_structural_breaks(normalized)
    return _indent_code_lines(structured)


def _insert_structural_breaks(source: str) -> str:
    chunks: list[str] = []
    paren_depth = 0
    in_string: str | None = None
    is_escaped = False

    for char in source:
        if in_string:
            chunks.append(char)
            if is_escaped:
                is_escaped = False
            elif char == "\\":
                is_escaped = True
            elif char == in_string:
                in_string = None
            continue

        if char in {'"', "'"}:
            chunks.append(char)
            in_string = char
            continue

        if char == "\n":
            if chunks and chunks[-1] != "\n":
                chunks.append("\n")
            continue

        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(paren_depth - 1, 0)

        if char == "{":
            while chunks and chunks[-1] == " ":
                chunks.pop()
            if chunks and chunks[-1] != "\n":
                chunks.append("\n")
            chunks.append("{")
            chunks.append("\n")
            continue

        if char == "}":
            while chunks and chunks[-1] == " ":
                chunks.pop()
            if chunks and chunks[-1] != "\n":
                chunks.append("\n")
            chunks.append("}")
            chunks.append("\n")
            continue

        if char == ";" and paren_depth == 0:
            chunks.append(";")
            chunks.append("\n")
            continue

        if char in {" ", "\t"}:
            if chunks and chunks[-1] not in {" ", "\n"}:
                chunks.append(" ")
            continue

        chunks.append(char)

    return "".join(chunks)


def _indent_code_lines(source: str) -> str:
    formatted_lines: list[str] = []
    indent = 0

    for raw_line in source.splitlines():
        line = _normalize_code_line(raw_line.strip())
        if not line:
            continue

        leading_close_count = len(line) - len(line.lstrip("}"))
        if leading_close_count:
            indent = max(indent - leading_close_count, 0)

        formatted_lines.append(f"{'    ' * indent}{line}")
        indent = max(indent + line.count("{") - line.count("}") + leading_close_count, 0)

    return "\n".join(formatted_lines)


def _normalize_code_line(line: str) -> str:
    if not line:
        return ""

    collapsed = re.sub(r"\s+", " ", line).strip()
    if collapsed.startswith("#include"):
        return collapsed

    parts: list[str] = []
    cursor = 0
    for match in STRING_LITERAL_RE.finditer(collapsed):
        if match.start() > cursor:
            parts.append(_normalize_code_segment(collapsed[cursor:match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    if cursor < len(collapsed):
        parts.append(_normalize_code_segment(collapsed[cursor:]))
    return _join_code_parts(parts).strip()


def _normalize_code_segment(segment: str) -> str:
    normalized = re.sub(r"\s+", " ", segment).strip()
    if not normalized:
        return ""

    normalized = re.sub(r"\s*(==|!=|<=|>=|<<|>>|&&|\|\|)\s*", r" \1 ", normalized)
    normalized = re.sub(r"(?<![=!<>+\-*/%])\s*=\s*(?![=])", " = ", normalized)
    normalized = re.sub(r"(?<!<)\s*<\s*(?![<=])", " < ", normalized)
    normalized = re.sub(r"(?<!>)\s*>\s*(?![>=])", " > ", normalized)
    normalized = re.sub(r",\s*", ", ", normalized)
    normalized = re.sub(r";\s*", "; ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _join_code_parts(parts: list[str]) -> str:
    joined = ""
    for part in parts:
        if not part:
            continue
        if joined and not joined.endswith((" ", "\n")) and not part.startswith((" ", "\n", ")", "]", "}", ",", ";")):
            joined += " "
        joined += part
    return joined


def _extract_fenced_code_blocks(source: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    cursor = 0
    matched = False

    for match in FENCED_CODE_BLOCK_RE.finditer(source):
        matched = True
        text_before = normalize_homework_plain_text(source[cursor:match.start()])
        if text_before:
            blocks.append({"kind": "text", "text": text_before})

        code_text = format_code_option(match.group(1))
        if code_text:
            blocks.append({"kind": "code", "text": code_text})
        cursor = match.end()

    text_after = normalize_homework_plain_text(source[cursor:])
    if text_after:
        blocks.append({"kind": "text", "text": text_after})

    return blocks if matched else []


def _split_mixed_cpp_content(source: str) -> list[dict[str, str]]:
    normalized = normalize_homework_plain_text(source)
    if not normalized:
        return []

    code_start = CPP_CODE_START_RE.search(normalized)
    if not code_start or code_start.start() <= 0:
        return []

    prefix = normalize_homework_plain_text(normalized[:code_start.start()].rstrip("：: "))
    code = normalize_homework_plain_text(normalized[code_start.start():])
    if not prefix or not code or is_probably_code_option(prefix) or not is_probably_code_option(code):
        return []

    return [
        {"kind": "text", "text": prefix},
        {"kind": "code", "text": format_code_option(code)},
    ]
