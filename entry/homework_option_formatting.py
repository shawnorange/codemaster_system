from __future__ import annotations

import re
import textwrap
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
    r"#include|using\s+namespace\s+std|std::|int\s+main\s*\(|"
    r"(?:int|long|double|float|bool|char|string|auto)\s+[A-Za-z_]\w*\s*(?:[=;,\[])|"
    r"cout\b|cin\b|for\s*\(|while\s*\(|if\s*\(|return\b",
    re.IGNORECASE,
)
STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
DIAGRAM_SYMBOL_LINE_RE = re.compile(r"^[\s*#@oOxX□■○●△▲◇◆.+\-_/\\|]+$")
DIAGRAM_SYMBOL_RE = re.compile(r"[*#@oOxX□■○●△▲◇◆]")
MATRIX_TOKEN_RE = re.compile(r"^(?:[A-Za-z]|\d{1,3}|[*#@oOxX□■○●△▲◇◆.+\-_/\\|]{1,4})$")
INLINE_DIAGRAM_TOKEN_RE = re.compile(r"^([#*.])\1*$")
INLINE_DIAGRAM_CONTEXT_RE = re.compile(
    r"(?:(?:"
    r"当\s*[A-Za-z_]\w*\s*=\s*\d+\s*时\s*输出|"
    r"样例输出|示例输出|输出结果|运行结果|图形如下|图形为|如下图"
    r")\s*[：:]?|输出\s*[：:])\s*$",
    re.IGNORECASE,
)


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
    raw_source = _normalize_source_text_for_diagrams(value)
    source = normalize_homework_source_text(raw_source)
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

    diagram_blocks = _split_visual_diagram_content(raw_source)
    if diagram_blocks:
        return {
            "text": source,
            "blocks": diagram_blocks,
            "is_code_content": any(block["kind"] == "code" for block in diagram_blocks),
        }

    inline_diagram_blocks = _split_inline_visual_diagram_content(source)
    if inline_diagram_blocks:
        return {
            "text": source,
            "blocks": inline_diagram_blocks,
            "is_code_content": any(block["kind"] == "code" for block in inline_diagram_blocks),
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


def _split_visual_diagram_content(source: str) -> list[dict[str, str]]:
    lines = _normalize_source_text_for_diagrams(source).splitlines()
    if not lines:
        return []

    blocks: list[dict[str, str]] = []
    text_lines: list[str] = []
    has_diagram = False
    index = 0

    while index < len(lines):
        line = lines[index]
        if not _is_visual_diagram_line(line):
            text_lines.append(line)
            index += 1
            continue

        diagram_lines = []
        while index < len(lines) and _is_visual_diagram_line(lines[index]):
            diagram_lines.append(lines[index])
            index += 1

        if len(diagram_lines) < 2:
            text_lines.extend(diagram_lines)
            continue

        _append_non_diagram_segment_blocks(blocks, "\n".join(text_lines))
        text_lines = []

        diagram_text = _normalize_diagram_text(diagram_lines)
        if diagram_text:
            blocks.append({"kind": "diagram", "text": diagram_text})
            has_diagram = True

    _append_non_diagram_segment_blocks(blocks, "\n".join(text_lines))

    return blocks if has_diagram else []


def _split_inline_visual_diagram_content(source: str) -> list[dict[str, str]]:
    normalized = re.sub(r"\s+", " ", normalize_homework_source_text(source)).strip()
    if not normalized:
        return []

    tokens = list(re.finditer(r"\S+", normalized))
    if not tokens:
        return []

    runs: list[tuple[int, int, list[str]]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index].group(0)
        if not _is_inline_diagram_token(token):
            index += 1
            continue

        run_tokens = []
        run_start = tokens[index].start()
        while index < len(tokens) and _is_inline_diagram_token(tokens[index].group(0)):
            run_tokens.append(tokens[index].group(0))
            index += 1
        run_end = tokens[index - 1].end()

        if _can_restore_inline_diagram_run(normalized, run_start, run_end, run_tokens):
            runs.append((run_start, run_end, run_tokens))

    if not runs:
        return []

    blocks: list[dict[str, str]] = []
    cursor = 0
    for start, end, run_tokens in runs:
        _append_non_diagram_segment_blocks(blocks, normalized[cursor:start])
        blocks.append({"kind": "diagram", "text": "\n".join(run_tokens)})
        cursor = end
    _append_non_diagram_segment_blocks(blocks, normalized[cursor:])
    return blocks


def _append_non_diagram_segment_blocks(blocks: list[dict[str, str]], segment: str) -> None:
    normalized = normalize_homework_plain_text(segment)
    if not normalized:
        return

    mixed_blocks = _split_mixed_cpp_content(normalized)
    if mixed_blocks:
        blocks.extend(mixed_blocks)
        return

    is_code_content = is_probably_code_option(normalized)
    blocks.append(
        {
            "kind": "code" if is_code_content else "text",
            "text": format_code_option(normalized) if is_code_content else normalized,
        }
    )


def _is_visual_diagram_line(raw_line: str) -> bool:
    line = raw_line.replace("\u3000", " ").replace("\xa0", " ").rstrip()
    stripped = line.strip()
    if not stripped:
        return False

    if DIAGRAM_SYMBOL_RE.search(stripped) and DIAGRAM_SYMBOL_LINE_RE.fullmatch(line):
        return True

    tokens = stripped.split()
    return len(tokens) >= 2 and all(MATRIX_TOKEN_RE.fullmatch(token) for token in tokens)


def _is_inline_diagram_token(token: str) -> bool:
    return bool(INLINE_DIAGRAM_TOKEN_RE.fullmatch(token))


def _can_restore_inline_diagram_run(source: str, start: int, end: int, tokens: list[str]) -> bool:
    if not _looks_like_inline_diagram_run(tokens):
        return False
    if _is_inside_string_literal(source, start, end):
        return False
    if _is_inline_diagram_code_context(source, start):
        return False
    return _has_inline_diagram_output_context(source, start)


def _looks_like_inline_diagram_run(tokens: list[str]) -> bool:
    if len(tokens) < 3:
        return False
    symbols = {token[0] for token in tokens if token}
    if len(symbols) != 1:
        return False
    lengths = [len(token) for token in tokens]
    if max(lengths) < 2 or len(set(lengths)) < 2:
        return False
    return _is_shape_length_sequence(lengths)


def _is_shape_length_sequence(lengths: list[int]) -> bool:
    diffs = [right - left for left, right in zip(lengths, lengths[1:]) if right != left]
    if not diffs:
        return False
    step_sizes = {abs(diff) for diff in diffs}
    if len(step_sizes) != 1 or step_sizes.pop() not in {1, 2}:
        return False
    signs = [1 if diff > 0 else -1 for diff in diffs]
    sign_changes = sum(1 for left, right in zip(signs, signs[1:]) if left != right)
    return sign_changes <= 1


def _is_inside_string_literal(source: str, start: int, end: int) -> bool:
    return any(match.start() <= start and end <= match.end() for match in STRING_LITERAL_RE.finditer(source))


def _is_inline_diagram_code_context(source: str, start: int) -> bool:
    prefix = source[max(0, start - 120):start]
    statement_start = max(prefix.rfind(";"), prefix.rfind("\n"))
    statement_prefix = prefix[statement_start + 1:].strip()
    if not statement_prefix:
        return False
    return bool(
        re.search(r"(?:=|<<)\s*$", statement_prefix)
        or re.search(r"\b(?:string|char|auto|const|int|long|double|float|bool)\b[^;]*$", statement_prefix)
        or re.search(r"\b(?:cout|printf|puts)\b[^;]*$", statement_prefix)
    )


def _has_inline_diagram_output_context(source: str, start: int) -> bool:
    context = source[max(0, start - 80):start].strip()
    if not context:
        return False
    return bool(INLINE_DIAGRAM_CONTEXT_RE.search(context))


def _normalize_diagram_text(lines: list[str]) -> str:
    normalized = "\n".join(
        line.replace("\u3000", " ").replace("\xa0", " ").rstrip()
        for line in lines
    ).strip("\n")
    return textwrap.dedent(normalized).rstrip()


def is_visual_diagram_line(raw_line: str) -> bool:
    return _is_visual_diagram_line(raw_line)


def normalize_visual_diagram_text(lines: list[str]) -> str:
    return _normalize_diagram_text(lines)


def _normalize_source_text_for_diagrams(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
