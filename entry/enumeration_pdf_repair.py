from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader


TARGET_LEVEL_CODE = "GESP2"
TARGET_CONTENT_SLUG = "enumeration-method"

SOURCE_PDF_RE = re.compile(r"^2级(?P<year>\d{4})年(?P<month>\d{1,2})月\.pdf$")
QUESTION_HEADER_RE = re.compile(r"^第\s*(?P<number>\d{1,2})\s*题\b")
PROGRAM_TITLE_RE = re.compile(r"^(?:[•●]\s*)?试题名称[:：]\s*(?P<title>.+)$")
PROGRAM_NUMERIC_TITLE_RE = re.compile(
    r"^\d+\.\d+\.\d+\s+(?P<title>(?!题目描述|题面描述|输入格式|输出格式|输入样例|输出样例|样例解释|数据范围|参考程序|样例)[^\n]+)$"
)
OPTION_LINE_RE = re.compile(r"^[\s口O〇○QUD]*([A-DＡ-Ｄ])\s*[.．、:：]?\s*(.*)$")
SECTION_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*)?\s*"
    r"(?P<label>试题名称|题目描述|题面描述|输入格式|输出格式|输入样例|输出样例|样例解释|数据范围|参考程序|样例)"
    r"(?:\s*\d+)?\s*(?P<rest>.*)$"
)

OPTION_LABELS = ("A", "B", "C", "D")
CODE_HINT_RE = re.compile(
    r"#include|using namespace|scanf\s*\(|printf\s*\(|cin\b|cout\b|int\b|long long\b|bool\b|char\b|double\b|"
    r"for\s*\(|while\s*\(|if\s*\(|else\b|return\b|break\b|continue\b|assert\s*\(|main\s*\(|\{|\}|//|/\*|\*/|<<|>>|="
)
EXPRESSION_HINT_RE = re.compile(r"[A-Za-z_]\w*|[+\-*/%<>=()]")
WEIRD_CODE_CHAR_RE = re.compile(r"[〈〉「」¢—□○●◆■]")

CODE_REPLACEMENTS = (
    ("〈〈", "<<"),
    ("〉〉", ">>"),
    ("《", "<<"),
    ("》", ">>"),
    ("（", "("),
    ("）", ")"),
    ("｛", "{"),
    ("｝", "}"),
    ("【", "["),
    ("】", "]"),
    ("；", ";"),
    ("：", ":"),
    ("，", ","),
    ("“", '"'),
    ("”", '"'),
    ("‘", "'"),
    ("’", "'"),
    ("！=", "!="),
    ("＝", "="),
    ("＋", "+"),
    ("－", "-"),
    ("＊", "*"),
    ("／", "/"),
    ("％", "%"),
    ("＜", "<"),
    ("＞", ">"),
    ("＆", "&"),
    ("｜", "|"),
    ("boo1", "bool"),
    ("1f", "if"),
    ("end1", "endl"),
    ("retum", "return"),
    ("N>Q", "N>0"),
    ("== true", "== true"),
)

TITLE_ALIASES = {
    "构造每行每列都是等差数列的矩阵": "等差矩阵",
    "统计区间内的美丽数数量": "数数",
}


@dataclass(slots=True)
class TextBlock:
    key: str
    text: str
    start_page: int
    end_page: int


@dataclass(slots=True)
class ParsedPdfDocument:
    pdf_path: Path
    text_pages: list[str]
    ocr_pages: list[str]
    question_blocks_text: dict[int, TextBlock]
    question_blocks_ocr: dict[int, TextBlock]
    program_blocks_text: dict[str, TextBlock]
    program_blocks_ocr: dict[str, TextBlock]


@dataclass(slots=True)
class RepairCandidate:
    question_code: str
    pdf_path: Path
    matched_by: str
    payload_updates: dict[str, Any] = field(default_factory=dict)
    needs_review_reasons: list[str] = field(default_factory=list)
    debug_notes: list[str] = field(default_factory=list)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalize_title_key(value: str) -> str:
    normalized = normalize_text(value)
    normalized = TITLE_ALIASES.get(normalized, normalized)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", normalized).lower()


def clean_code_line(line: str) -> str:
    cleaned = normalize_text(line)
    for before, after in CODE_REPLACEMENTS:
        cleaned = cleaned.replace(before, after)
    cleaned = strip_leading_line_number(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.replace(" return O;", " return 0;")
    cleaned = cleaned.replace("= O;", "= 0;")
    cleaned = cleaned.replace("return 9;", "return 0;")
    return cleaned


def strip_leading_line_number(line: str) -> str:
    stripped = normalize_text(line)
    if re.match(r"^\d+\s+(#include|using namespace|int\b|long long\b|bool\b|char\b|double\b|for\b|while\b|if\b|else\b|return\b|printf\b|scanf\b|cin\b|cout\b|/\*|\*/)", stripped):
        return re.sub(r"^\d+\s+", "", stripped).strip()
    return stripped


def is_line_number(line: str) -> bool:
    normalized = normalize_text(line)
    return bool(normalized) and bool(re.fullmatch(r"\d{1,2}", normalized))


def is_code_like_line(line: str) -> bool:
    cleaned = clean_code_line(line)
    if not cleaned:
        return False
    return bool(CODE_HINT_RE.search(cleaned))


def looks_like_expression(line: str) -> bool:
    cleaned = clean_code_line(line)
    return bool(cleaned) and bool(EXPRESSION_HINT_RE.search(cleaned))


def build_source_pdf_index(source_dir: Path) -> dict[tuple[int, int], Path]:
    pdf_index: dict[tuple[int, int], Path] = {}
    for path in source_dir.glob("*.pdf"):
        match = SOURCE_PDF_RE.match(path.name)
        if not match:
            continue
        pdf_index[(int(match.group("year")), int(match.group("month")))] = path
    return pdf_index


def extract_pdf_text_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    return [normalize_text(page.extract_text() or "") for page in reader.pages]


def extract_pdf_ocr_pages(pdf_path: Path, *, ocr_script_path: Path) -> list[str]:
    completed = subprocess.run(
        ["swift", str(ocr_script_path), "--pdf", str(pdf_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    pages = payload.get("pages") or []
    sorted_pages = sorted(pages, key=lambda item: item.get("page_number", 0))
    return [normalize_text(item.get("text", "")) for item in sorted_pages]


def _collect_lines(pages: list[str]) -> list[tuple[int, str]]:
    records: list[tuple[int, str]] = []
    for page_number, page_text in enumerate(pages, start=1):
        for raw_line in page_text.splitlines():
            cleaned = normalize_text(raw_line)
            if cleaned:
                records.append((page_number, cleaned))
    return records


def _build_blocks(pages: list[str], *, matcher: str) -> dict[Any, TextBlock]:
    line_records = _collect_lines(pages)
    headers: list[tuple[int, Any]] = []

    for index, (_, line) in enumerate(line_records):
        if matcher == "question":
            match = QUESTION_HEADER_RE.match(line)
            if match:
                headers.append((index, int(match.group("number"))))
        else:
            match = PROGRAM_TITLE_RE.match(line)
            if not match:
                match = PROGRAM_NUMERIC_TITLE_RE.match(line)
            if match:
                headers.append((index, normalize_title_key(match.group("title"))))

    blocks: dict[Any, TextBlock] = {}
    for header_index, (start_index, key) in enumerate(headers):
        end_index = headers[header_index + 1][0] if header_index + 1 < len(headers) else len(line_records)
        block_records = line_records[start_index:end_index]
        if not block_records:
            continue
        text = "\n".join(line for _, line in block_records)
        blocks[key] = TextBlock(
            key=str(key),
            text=text,
            start_page=block_records[0][0],
            end_page=block_records[-1][0],
        )

    return blocks


def load_pdf_document(pdf_path: Path, *, ocr_script_path: Path) -> ParsedPdfDocument:
    text_pages = extract_pdf_text_pages(pdf_path)
    ocr_pages = extract_pdf_ocr_pages(pdf_path, ocr_script_path=ocr_script_path)
    return ParsedPdfDocument(
        pdf_path=pdf_path,
        text_pages=text_pages,
        ocr_pages=ocr_pages,
        question_blocks_text=_build_blocks(text_pages, matcher="question"),
        question_blocks_ocr=_build_blocks(ocr_pages, matcher="question"),
        program_blocks_text=_build_blocks(text_pages, matcher="program"),
        program_blocks_ocr=_build_blocks(ocr_pages, matcher="program"),
    )


def extract_program_short_title(title: str, payload: dict[str, Any]) -> str:
    for value in (payload.get("question_no"), payload.get("question_ref"), title):
        text = normalize_text(value)
        match = re.search(r"试题名称[:：]\s*([^\n]+)", text)
        if match:
            return normalize_text(match.group(1))
    return normalize_text(TITLE_ALIASES.get(title, title))


def extract_options(block_text: str) -> list[str]:
    options: dict[str, list[str]] = {}
    current_label: str | None = None

    for raw_line in block_text.splitlines():
        line = normalize_text(raw_line)
        if not line:
            continue
        option_match = OPTION_LINE_RE.match(line)
        if option_match:
            current_label = normalize_option_label(option_match.group(1))
            options[current_label] = []
            initial_text = normalize_option_fragment(option_match.group(2))
            if initial_text:
                options[current_label].append(initial_text)
            continue

        if current_label is None:
            continue
        if QUESTION_HEADER_RE.match(line):
            break
        if is_line_number(line):
            continue
        if OPTION_LINE_RE.match(line):
            continue

        fragment = normalize_option_fragment(line)
        if fragment:
            options[current_label].append(fragment)

    if not all(label in options for label in OPTION_LABELS):
        return []

    return [format_option_value(options[label]) for label in OPTION_LABELS]


def normalize_option_label(label: str) -> str:
    return normalize_text(label).replace("Ａ", "A").replace("Ｂ", "B").replace("Ｃ", "C").replace("Ｄ", "D")


def normalize_option_fragment(fragment: str) -> str:
    cleaned = clean_code_line(fragment)
    cleaned = cleaned.lstrip(".．、:：")
    cleaned = re.sub(r"^\d+\s+(?=[A-Za-z_\u4e00-\u9fff#(])", "", cleaned)
    cleaned = re.sub(r"^22(?=[* ])", "2", cleaned)
    cleaned = re.sub(r"^2\s+2(?=[* ])", "2", cleaned)
    cleaned = cleaned.replace("height - 1 - 1", "height - i - 1")
    cleaned = cleaned.replace("height-1-1", "height-i-1")
    for stop_token in ("判断题", "题号", "答案"):
        if stop_token in cleaned:
            cleaned = cleaned.split(stop_token, 1)[0].strip()
    return cleaned.strip()


def format_option_value(fragments: list[str]) -> str:
    cleaned_fragments = [fragment for fragment in fragments if fragment]
    if not cleaned_fragments:
        return ""
    if len(cleaned_fragments) == 1:
        return cleaned_fragments[0]
    if all(looks_like_expression(fragment) for fragment in cleaned_fragments):
        return "；".join(cleaned_fragments)
    return " ".join(cleaned_fragments)


def extract_best_code(*block_texts: str) -> str:
    best_code = ""
    best_score = 0

    for block_text in block_texts:
        candidate = extract_code_block(block_text)
        score = score_code(candidate)
        if score > best_score:
            best_code = candidate
            best_score = score

    return best_code


def extract_code_block(block_text: str) -> str:
    lines = [normalize_text(raw_line) for raw_line in block_text.splitlines()]
    candidates: list[list[str]] = []
    current: list[str] = []
    active = False

    for line in lines:
        if not line:
            if active and current and current[-1] != "":
                current.append("")
            continue

        if QUESTION_HEADER_RE.match(line) or OPTION_LINE_RE.match(line):
            if current:
                candidates.append(current)
                current = []
            active = False
            if OPTION_LINE_RE.match(line):
                break
            continue

        if is_line_number(line):
            continue

        cleaned = clean_code_line(line)
        if is_code_like_line(cleaned):
            active = True
            current.append(cleaned)
            continue

        if active and (cleaned.startswith("//") or cleaned.startswith("/*") or cleaned.startswith("*") or looks_like_expression(cleaned)):
            current.append(cleaned)
            continue

        if current:
            candidates.append(current)
            current = []
        active = False

    if current:
        candidates.append(current)

    if not candidates:
        return ""

    best_candidate = max(candidates, key=score_code_lines)
    compacted: list[str] = []
    for line in best_candidate:
        stripped = line.rstrip()
        if stripped == "" and (not compacted or compacted[-1] == ""):
            continue
        compacted.append(stripped)
    return "\n".join(compacted).strip()


def score_code(value: str) -> int:
    if not value:
        return 0
    return score_code_lines(value.splitlines())


def score_code_lines(lines: list[str]) -> int:
    score = 0
    for line in lines:
        stripped = clean_code_line(line)
        if not stripped:
            continue
        score += 8 + len(stripped)
        if is_code_like_line(stripped):
            score += 12
        if any(token in stripped for token in ("for", "while", "if", "cout", "cin", "printf", "scanf", "return", "#include")):
            score += 10
        score -= len(WEIRD_CODE_CHAR_RE.findall(stripped)) * 20
    return score


def parse_program_sections(block_text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_section: str | None = None

    for raw_line in block_text.splitlines():
        line = normalize_text(raw_line)
        if not line:
            continue

        match = SECTION_HEADING_RE.match(line)
        if match:
            label = match.group("label")
            current_section = normalize_section_label(label)
            sections.setdefault(current_section, [])
            remainder = normalize_text(match.group("rest"))
            if remainder:
                sections[current_section].append(remainder)
            continue

        if current_section:
            sections[current_section].append(line)

    return {
        key: "\n".join(values).strip()
        for key, values in sections.items()
        if values
    }


def normalize_section_label(label: str) -> str:
    mapping = {
        "试题名称": "title",
        "题目描述": "description",
        "题面描述": "description",
        "输入格式": "input_format",
        "输出格式": "output_format",
        "样例": "sample",
        "输入样例": "sample_input",
        "输出样例": "sample_output",
        "样例解释": "sample_explanation",
        "数据范围": "data_range",
        "参考程序": "reference_program",
    }
    return mapping[label]


def clean_program_text(value: str) -> str:
    lines = []
    for raw_line in value.splitlines():
        line = normalize_text(raw_line)
        if not line or is_line_number(line) or re.fullmatch(r"第\d+页/共\d+页", line):
            continue
        lines.append(line)
    return " ".join(lines).strip()


def clean_program_sample(value: str) -> str:
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = clean_code_line(raw_line)
        if not line or is_line_number(line):
            continue
        lines.append(line)

    while lines and re.fullmatch(r"\d", lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def extract_reference_code_from_page_text(page_text: str) -> str:
    page_text = normalize_text(page_text)
    start_index = page_text.find("#include")
    if start_index < 0:
        return ""

    snippet = page_text[start_index:]
    collected: list[str] = []
    for raw_line in snippet.splitlines():
        line = clean_code_line(raw_line)
        if not line or is_line_number(line):
            continue
        if re.fullmatch(r"第\s*\d+\s*页\s*/\s*共\s*\d+\s*页", line):
            break
        if QUESTION_HEADER_RE.match(line) or PROGRAM_TITLE_RE.match(line) or PROGRAM_NUMERIC_TITLE_RE.match(line):
            break
        if collected or line.startswith("#include"):
            collected.append(line)

    compacted: list[str] = []
    for line in collected:
        if not line:
            continue
        compacted.append(line)
    return "\n".join(compacted).strip()


def quality_score(field_name: str, value: Any) -> int:
    if not value:
        return 0
    if field_name == "options":
        if not isinstance(value, list):
            return 0
        if len(value) != 4:
            return 0
        cleaned_options = [normalize_text(item) for item in value]
        if any(not item for item in cleaned_options):
            return 0
        lengths = [len(item) for item in cleaned_options]
        if max(lengths) > 120:
            return 0
        if max(lengths) > min(lengths) * 4:
            return 0
        suspicious_tokens = ("题号", "答案", "判断题")
        if any(any(token in item for token in suspicious_tokens) for item in cleaned_options):
            return 0
        return 1000 + sum(len(item) for item in cleaned_options)
    if field_name in {"code", "reference_code"}:
        return score_code(normalize_text(value))
    return len(normalize_text(value))


def select_better_value(field_name: str, current_value: Any, candidate_value: Any) -> Any:
    if quality_score(field_name, candidate_value) > quality_score(field_name, current_value):
        return candidate_value
    return current_value


def extract_single_choice_candidate(
    *,
    question_code: str,
    pdf_path: Path,
    block_text: str,
    block_ocr: str,
) -> RepairCandidate:
    candidate = RepairCandidate(question_code=question_code, pdf_path=pdf_path, matched_by="source_question_no")

    options_candidates = [extract_options(block_ocr), extract_options(block_text)]
    best_options = max(options_candidates, key=lambda value: quality_score("options", value), default=[])
    if quality_score("options", best_options):
        candidate.payload_updates["options"] = best_options

    best_code = extract_best_code(block_text, block_ocr)
    if best_code:
        candidate.payload_updates["code"] = best_code

    return candidate


def extract_programming_candidate(
    *,
    question_code: str,
    pdf_path: Path,
    short_title: str,
    block_text: str,
    block_ocr: str,
    page_text: str = "",
) -> RepairCandidate:
    candidate = RepairCandidate(question_code=question_code, pdf_path=pdf_path, matched_by="program_title")
    text_sections = parse_program_sections(block_text)
    ocr_sections = parse_program_sections(block_ocr)

    for field_name in ("input_format", "output_format", "data_range"):
        value = clean_program_text(ocr_sections.get(field_name) or text_sections.get(field_name) or "")
        if value:
            candidate.payload_updates[field_name] = value

    reference_code = extract_best_code(
        extract_reference_code_from_page_text(page_text),
        text_sections.get("reference_program", ""),
        ocr_sections.get("reference_program", ""),
        block_text,
        block_ocr,
    )
    if reference_code:
        candidate.payload_updates["reference_code"] = reference_code
        candidate.payload_updates["code"] = reference_code

    title_key = normalize_title_key(short_title)
    if title_key == normalize_title_key("数数"):
        sample_input, sample_output = extract_counting_samples(ocr_sections, text_sections)
        if sample_input:
            candidate.payload_updates["sample_input"] = sample_input
        if sample_output:
            candidate.payload_updates["sample_output"] = sample_output
    if title_key == normalize_title_key("数数") and "data_range" in candidate.payload_updates:
        data_range = candidate.payload_updates["data_range"]
        data_range = data_range.replace("工", "L")
        data_range = data_range.replace("10°", "10^9")
        candidate.payload_updates["data_range"] = data_range

    return candidate


def extract_counting_samples(
    ocr_sections: dict[str, str],
    text_sections: dict[str, str],
) -> tuple[str, str]:
    raw_input = ocr_sections.get("sample_input") or text_sections.get("sample_input") or ""
    raw_output = ocr_sections.get("sample_output") or text_sections.get("sample_output") or ""
    sample_explanation = clean_program_text(ocr_sections.get("sample_explanation") or text_sections.get("sample_explanation") or "")

    input_numbers = re.findall(r"\d+", raw_input)
    if len(input_numbers) >= 2:
        sample_input = "\n".join(input_numbers[-2:])
    else:
        input_lines = [clean_code_line(line) for line in raw_input.splitlines()]
        input_lines = [line for line in input_lines if line and not is_line_number(line)]
        sample_input = "\n".join(input_lines).strip()

    output_numbers = re.findall(r"\d+", raw_output)
    sample_output = ""
    if output_numbers:
        if len(output_numbers) == 1 and output_numbers[0] == "12":
            sample_output = "2"
        elif len(output_numbers) == 1:
            sample_output = output_numbers[0]
        else:
            sample_output = "\n".join(output_numbers)
    if not sample_output and "2221" in sample_explanation and "2223" in sample_explanation:
        sample_output = "2"

    return sample_input, sample_output
