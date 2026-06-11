from __future__ import annotations

import base64
import io
import json
import mimetypes
import re
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (
    ExamQuestionBankAsset,
    ExamQuestionBankOption,
    ExamQuestionBankPaper,
    ExamQuestionBankImportJob,
    ExamQuestionBankQuestion,
)


OCR_PAGE_PROMPT = """You are recognizing one rendered GESP C++ exam page.
Return faithful Markdown. Preserve question numbers, options, code blocks, formulas, diagrams, and answer/analysis text when visible.
For flowcharts, do not transcribe the text inside the flowchart and do not convert it to Mermaid or pseudo-code. Keep only a short placeholder like `[流程图见图]`; the image crop pipeline will preserve the flowchart as an image asset.
For single-choice options containing code, keep each option label (`A.`, `B.`, `C.`, `D.`) outside the code fence, and close that option's code fence before the next option label, next question title, or next section header.
Never merge later sections such as `2 判断题` or `3 编程题` into the previous choice question, even when the previous option has an empty or malformed code block.
Programming questions have clear titles such as `3.1 编程题 1` and `3.2 编程题 2`; preserve those title lines exactly.
Do not invent missing text.
"""

SOURCE_TYPE_PDF = "pdf"
SOURCE_TYPE_IMAGE = "image"
SOURCE_TYPE_TEXT = "text"
SOURCE_TYPE_MARKDOWN = "markdown"
SOURCE_TYPE_JSON = "json"
SOURCE_TYPE_HTML = "html"
SOURCE_TYPE_DOCX = "docx"

OCR_SOURCE_TYPES = {SOURCE_TYPE_PDF, SOURCE_TYPE_IMAGE}
TEXT_SOURCE_TYPES = {
    SOURCE_TYPE_TEXT,
    SOURCE_TYPE_MARKDOWN,
    SOURCE_TYPE_JSON,
    SOURCE_TYPE_HTML,
    SOURCE_TYPE_DOCX,
}
SUPPORTED_SOURCE_TYPES = OCR_SOURCE_TYPES | TEXT_SOURCE_TYPES
EXAM_CHOICE_KEYS = ("A", "B", "C", "D")
QUESTION_SECTION_SINGLE_CHOICE = ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE
QUESTION_SECTION_TRUE_FALSE = ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE
QUESTION_SECTION_PROGRAMMING = ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING
QUESTION_SECTION_ORDER = (
    QUESTION_SECTION_SINGLE_CHOICE,
    QUESTION_SECTION_TRUE_FALSE,
    QUESTION_SECTION_PROGRAMMING,
)


class ExamPaperImportError(RuntimeError):
    pass


class ExamPaperImportConfirmError(RuntimeError):
    pass


def get_answer_text_from_json(answer_json: dict[str, Any] | None, question_type: str = "") -> str:
    if not isinstance(answer_json, dict):
        return ""
    answer_keys = (
        ("correct_answer", "answer", "value")
        if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE
        else ("answer", "correct_answer", "value")
    )
    for key in answer_keys:
        answer_text = str(answer_json.get(key) or "").strip()
        if answer_text:
            return answer_text
    return ""


def build_default_question_analysis_md(
    *,
    question_type: str,
    stem_md: str,
    answer_json: dict[str, Any] | None,
    options: dict[str, Any] | None = None,
    existing_analysis_md: str = "",
) -> str:
    existing_analysis = str(existing_analysis_md or "").strip()
    if existing_analysis:
        return existing_analysis

    answer_text = get_answer_text_from_json(answer_json, question_type).strip()
    if not answer_text:
        return ""

    normalized_options = {
        str(key).strip().upper(): re.sub(r"\s+", " ", str(value or "")).strip()
        for key, value in (options or {}).items()
        if str(key).strip()
    }
    display_answer = answer_text.upper() if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE else answer_text

    if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE:
        option_text = normalized_options.get(display_answer, "")
        if option_text:
            return f"正确答案：{display_answer}。\n\n解析：本题答案为 {display_answer}，对应选项为“{option_text}”。"
        return f"正确答案：{display_answer}。\n\n解析：本题答案为 {display_answer}。"

    if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE:
        return f"正确答案：{display_answer}。\n\n解析：本题为判断题，应结合题干条件判断表述是否成立。"

    if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING:
        return f"参考答案：\n\n{answer_text}"

    stem_preview = re.sub(r"\s+", " ", str(stem_md or "")).strip()
    if len(stem_preview) > 80:
        stem_preview = stem_preview[:80].rstrip() + "..."
    if stem_preview:
        return f"参考答案：{answer_text}。\n\n解析：请结合题干“{stem_preview}”复核答案。"
    return f"参考答案：{answer_text}。"


class _HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def get_text(self) -> str:
        return "\n".join(line.strip() for line in "".join(self._parts).splitlines() if line.strip())


def get_qwen_api_key() -> str:
    return str(getattr(settings, "QWEN_API_KEY", "") or getattr(settings, "DASHSCOPE_API_KEY", "") or "").strip()


def get_qwen_base_url() -> str:
    return str(getattr(settings, "QWEN_BASE_URL", "") or getattr(settings, "HOMEWORK_LLM_API_URL", "") or "").strip()


def get_qwen_ocr_model() -> str:
    return str(getattr(settings, "QWEN_OCR_MODEL", "") or getattr(settings, "HOMEWORK_LLM_MODEL", "") or "").strip()


def get_exam_qwen_ocr_concurrency() -> int:
    raw_value = getattr(settings, "EXAM_QWEN_OCR_CONCURRENCY", 2)
    try:
        concurrency = int(raw_value)
    except (TypeError, ValueError):
        concurrency = 2
    return max(1, min(concurrency, 8))


def get_media_relative_path(path: Path) -> str:
    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    return path.resolve(strict=False).relative_to(media_root).as_posix()


def read_media_text_file(relative_path: object) -> str:
    relative_path_text = str(relative_path or "").strip()
    if not relative_path_text:
        return ""
    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    file_path = (media_root / relative_path_text).resolve(strict=False)
    try:
        file_path.relative_to(media_root)
    except ValueError:
        return ""
    if not file_path.exists() or not file_path.is_file():
        return ""
    return file_path.read_text(encoding="utf-8", errors="replace")


def detect_exam_import_source_type(filename: str) -> str:
    extension = Path(filename or "").suffix.lower()
    if extension == ".pdf":
        return SOURCE_TYPE_PDF
    if extension in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return SOURCE_TYPE_IMAGE
    if extension == ".txt":
        return SOURCE_TYPE_TEXT
    if extension in {".md", ".markdown"}:
        return SOURCE_TYPE_MARKDOWN
    if extension == ".json":
        return SOURCE_TYPE_JSON
    if extension in {".html", ".htm"}:
        return SOURCE_TYPE_HTML
    if extension == ".docx":
        return SOURCE_TYPE_DOCX
    return ""


def get_supported_exam_import_extensions() -> set[str]:
    return {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".txt", ".md", ".markdown", ".json", ".html", ".htm", ".docx"}


def decode_text_bytes(raw_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def extract_html_text(raw_bytes: bytes) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(decode_text_bytes(raw_bytes))
    return extractor.get_text()


def extract_docx_text(raw_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        try:
            xml_bytes = archive.read("word/document.xml")
        except KeyError as exc:
            raise ExamPaperImportError("DOCX 文件中缺少 word/document.xml，无法解析。") from exc
    root = ElementTree.fromstring(xml_bytes)
    parts: list[str] = []
    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            parts.append(node.text)
        elif node.tag.endswith("}tab"):
            parts.append("\t")
        elif node.tag.endswith("}br") or node.tag.endswith("}p"):
            parts.append("\n")
    return "\n".join(line.rstrip() for line in "".join(parts).splitlines() if line.strip())


def json_value_to_markdown(value: Any) -> str:
    if isinstance(value, dict):
        questions = value.get("questions")
        if isinstance(questions, list):
            sections: list[str] = []
            for index, question in enumerate(questions, start=1):
                if not isinstance(question, dict):
                    continue
                question_no = question.get("question_no") or question.get("no") or index
                title = f"## 第 {question_no} 题"
                question_type = question.get("question_type") or question.get("type") or ""
                stem = question.get("stem_md") or question.get("stem") or question.get("question") or question.get("content") or ""
                section_parts = [title]
                if question_type:
                    section_parts.append(f"题型：{question_type}")
                if stem:
                    section_parts.append(str(stem).strip())
                options = question.get("options")
                if isinstance(options, dict):
                    for key in sorted(options):
                        option_text = str(options[key]).strip()
                        if option_text:
                            section_parts.append(f"{key}. {option_text}")
                elif isinstance(options, list):
                    for option in options:
                        if isinstance(option, dict):
                            key = option.get("key") or option.get("option_key") or option.get("label") or ""
                            text = option.get("text") or option.get("option_text_md") or option.get("value") or ""
                            if key or text:
                                section_parts.append(f"{key}. {text}".strip())
                        elif option:
                            section_parts.append(str(option))
                answer = question.get("answer_json") or question.get("correct_answer") or question.get("answer")
                if answer not in (None, "", {}):
                    section_parts.append(f"答案：{json.dumps(answer, ensure_ascii=False) if isinstance(answer, (dict, list)) else answer}")
                analysis = question.get("analysis_md") or question.get("analysis")
                if analysis:
                    section_parts.append(f"解析：{analysis}")
                sections.append("\n\n".join(str(item) for item in section_parts if str(item).strip()))
            if sections:
                return "\n\n".join(sections)
        for key in ("markdown", "markdown_text", "content_md", "content", "text"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key]
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def extract_text_source_markdown(file_path: Path, *, source_type: str) -> str:
    raw_bytes = file_path.read_bytes()
    if source_type == SOURCE_TYPE_MARKDOWN:
        return decode_text_bytes(raw_bytes)
    if source_type == SOURCE_TYPE_TEXT:
        return decode_text_bytes(raw_bytes)
    if source_type == SOURCE_TYPE_HTML:
        return extract_html_text(raw_bytes)
    if source_type == SOURCE_TYPE_DOCX:
        return extract_docx_text(raw_bytes)
    if source_type == SOURCE_TYPE_JSON:
        try:
            return json_value_to_markdown(json.loads(decode_text_bytes(raw_bytes)))
        except json.JSONDecodeError as exc:
            raise ExamPaperImportError("JSON 文件格式不正确，无法解析。") from exc
    raise ExamPaperImportError("当前文件类型不支持本地文本导入。")


MATH_REPLACEMENTS = {
    r"\leq": "≤",
    r"\le": "≤",
    r"\geq": "≥",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\ne": "≠",
    r"\times": "×",
    r"\cdot": "·",
    r"\lt": "<",
    r"\gt": ">",
}


def normalize_markdown_math_for_display(text: str) -> str:
    normalized = text
    for source, replacement in MATH_REPLACEMENTS.items():
        normalized = normalized.replace(source, replacement)
    normalized = re.sub(r"\$([^$\n]+)\$", lambda match: match.group(1).strip(), normalized)
    normalized = normalized.replace(r"\(", "").replace(r"\)", "")
    return normalized


CODE_FENCE_RE = re.compile(r"^\s*```")
CODE_LINE_NUMBER_PIPE_RE = re.compile(r"^(\s*)\d{1,4}\s+\|\s(.*)$")
CODE_LINE_NUMBER_SPACE_RE = re.compile(r"^(\s*)\d{1,4}\s+(?=\S)(.*)$")
PDF_PAGE_FOOTER_RE = re.compile(r"^\s*(?:第\s*)?\d{1,3}\s*页\s*/\s*共\s*\d{1,3}\s*页\s*$")


def unwrap_outer_markdown_fence(markdown_text: str) -> str:
    lines = markdown_text.strip().split("\n")
    if len(lines) >= 2 and lines[0].strip().lower() in {"```markdown", "```md"} and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return markdown_text


def strip_code_block_line_numbers(markdown_text: str) -> str:
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    in_code_block = False
    cleaned_lines: list[str] = []
    for line in lines:
        if CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            cleaned_lines.append(line)
            continue
        if in_code_block:
            match = CODE_LINE_NUMBER_PIPE_RE.match(line)
            if match:
                cleaned_lines.append(f"{match.group(1)}{match.group(2)}")
                continue
            match = CODE_LINE_NUMBER_SPACE_RE.match(line)
            if match:
                cleaned_lines.append(f"{match.group(1)}{match.group(2)}")
                continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def strip_pdf_page_footers(markdown_text: str) -> str:
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line for line in lines if not PDF_PAGE_FOOTER_RE.match(line.strip())).strip()


def format_exam_markdown_for_teacher_edit(markdown_text: object) -> str:
    text = strip_code_block_line_numbers(str(markdown_text or ""))
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    formatted_lines: list[str] = []
    in_code_block = False
    code_lines: list[str] = []

    def flush_code() -> None:
        nonlocal code_lines
        if not code_lines:
            return
        while code_lines and not code_lines[0].strip():
            code_lines.pop(0)
        while code_lines and not code_lines[-1].strip():
            code_lines.pop()
        min_indent: int | None = None
        for code_line in code_lines:
            if not code_line.strip():
                continue
            indent = len(code_line) - len(code_line.lstrip(" "))
            min_indent = indent if min_indent is None else min(min_indent, indent)
        trim = min_indent or 0
        formatted_lines.extend(code_line[trim:].rstrip() for code_line in code_lines)
        code_lines = []

    for line in lines:
        if CODE_FENCE_RE.match(line):
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                in_code_block = True
                code_lines = []
            continue
        if in_code_block:
            code_lines.append(line)
            continue
        formatted_lines.append(line.rstrip())

    if in_code_block:
        flush_code()
    return "\n".join(formatted_lines).strip()


def is_likely_cpp_code_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("#include", "using namespace")):
        return True
    if re.search(r"\b(int|long|double|float|char|bool|string|void|auto)\s+\w+", stripped):
        return True
    if re.search(r"\b(cin|cout|scanf|printf)\b|<<|>>", stripped):
        return True
    if re.search(r"\b(if|else|for|while|return|break|continue)\b", stripped):
        return True
    if stripped in {"{", "}"}:
        return True
    if stripped.endswith((";", "{", "}")):
        return True
    return False


def find_likely_cpp_code_range(lines: list[str]) -> tuple[int, int] | None:
    best_start = -1
    best_end = -1
    current_start: int | None = None
    for index, line in enumerate(lines):
        is_code = is_likely_cpp_code_line(line)
        if is_code and current_start is None:
            current_start = index
        if not is_code and current_start is not None:
            if index - current_start > best_end - best_start:
                best_start = current_start
                best_end = index
            current_start = None
    if current_start is not None and len(lines) - current_start > best_end - best_start:
        best_start = current_start
        best_end = len(lines)
    if best_start < 0 or best_end - best_start < 2:
        return None
    return best_start, best_end


def restore_exam_markdown_code_fences_from_original(edited_text: object, original_text: object) -> str:
    edited = str(edited_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    original = str(original_text or "").replace("\r\n", "\n").replace("\r", "\n")
    original_lines = original.split("\n")
    fence_lines = [line.strip() for line in original_lines if CODE_FENCE_RE.match(line)]
    if not edited or not fence_lines or "```" in edited:
        return edited
    if len(fence_lines) != 2:
        return edited
    opening_fence = fence_lines[0]
    before_lines: list[str] = []
    after_lines: list[str] = []
    in_code_block = False
    seen_code_block = False
    for line in original_lines:
        if CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            seen_code_block = True
            continue
        if not seen_code_block:
            before_lines.append(line)
        elif not in_code_block:
            after_lines.append(line)
    before_text = "\n".join(before_lines).strip()
    after_text = "\n".join(after_lines).strip()
    edited_lines = edited.split("\n")
    start_index = 0
    end_index = len(edited_lines)
    if before_text:
        before_count = len(before_text.split("\n"))
        if "\n".join(edited_lines[:before_count]).strip() == before_text:
            start_index = before_count
    if after_text:
        after_count = len(after_text.split("\n"))
        if "\n".join(edited_lines[end_index - after_count:]).strip() == after_text:
            end_index -= after_count
    code_text = "\n".join(edited_lines[start_index:end_index]).strip()
    if not code_text:
        code_range = find_likely_cpp_code_range(edited_lines)
        if code_range is None:
            return edited
        start_index, end_index = code_range
        code_text = "\n".join(edited_lines[start_index:end_index]).strip()
        before_text = "\n".join(edited_lines[:start_index]).strip()
        after_text = "\n".join(edited_lines[end_index:]).strip()
    elif start_index == 0 and end_index == len(edited_lines):
        code_range = find_likely_cpp_code_range(edited_lines)
        if code_range is not None:
            start_index, end_index = code_range
            code_text = "\n".join(edited_lines[start_index:end_index]).strip()
            before_text = "\n".join(edited_lines[:start_index]).strip()
            after_text = "\n".join(edited_lines[end_index:]).strip()
    rebuilt_parts = []
    if before_text:
        rebuilt_parts.append(before_text)
    rebuilt_parts.append(f"{opening_fence}\n{code_text}\n```")
    if after_text:
        rebuilt_parts.append(after_text)
    return "\n\n".join(part for part in rebuilt_parts if part).strip()


def clean_imported_markdown(markdown_text: str) -> str:
    normalized = markdown_text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    normalized = unwrap_outer_markdown_fence(normalized)
    normalized = strip_pdf_page_footers(normalized)
    normalized = strip_code_block_line_numbers(normalized)
    normalized = normalize_markdown_math_for_display(normalized)
    normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
    return normalized.strip()


CHINESE_QUESTION_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
QUESTION_START_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:第\s*)?([0-9]{1,3}|[一二三四五六七八九十]{1,4})\s*题(?:\s|[：:、.．]|$)"
)
PROGRAMMING_START_RE = re.compile(
    r"^\s*(?:(?:[0-9]+)\.[0-9]+\s*)?编程题\s*([0-9]{1,3}|[一二三四五六七八九十]{1,4})(?:\s|[：:、.．]|$)"
)
OPTION_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\[\s*[xX ]?\s*\]\s*)?(?:[（(]?([A-Da-d])[）)]|([A-Da-d]))[\.．、:：]\s*(.*)$"
)


def parse_question_number_token(value: object) -> int | None:
    token = str(value or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in CHINESE_QUESTION_NUMERALS:
        return CHINESE_QUESTION_NUMERALS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in CHINESE_QUESTION_NUMERALS:
        return 10 + CHINESE_QUESTION_NUMERALS[token[1]]
    if token.endswith("十") and len(token) == 2 and token[0] in CHINESE_QUESTION_NUMERALS:
        return CHINESE_QUESTION_NUMERALS[token[0]] * 10
    if "十" in token:
        left, right = token.split("十", 1)
        tens = CHINESE_QUESTION_NUMERALS.get(left, 1 if not left else 0)
        ones = CHINESE_QUESTION_NUMERALS.get(right, 0) if right else 0
        if tens:
            return tens * 10 + ones
    return None


def normalize_markdown_marker_line(line: str) -> str:
    normalized = str(line or "").strip()
    normalized = re.sub(r"^\s*#{1,4}\s*", "", normalized).strip()
    normalized = re.sub(r"^\*\*(.*?)\*\*(.*)$", r"\1\2", normalized).strip()
    normalized = normalized.strip("*").strip()
    return normalized


def detect_question_section_header(line: str) -> str | None:
    normalized = normalize_markdown_marker_line(line)
    compact = re.sub(r"\s+", "", normalized)
    if not compact:
        return None
    if "单选题" in compact and ("每题" in compact or re.match(r"^\d+单选题", compact)):
        return QUESTION_SECTION_SINGLE_CHOICE
    if "判断题" in compact and ("每题" in compact or re.match(r"^\d+判断题", compact)):
        return QUESTION_SECTION_TRUE_FALSE
    if "编程题" in compact and ("每题" in compact or re.match(r"^\d+编程题", compact)):
        return QUESTION_SECTION_PROGRAMMING
    return None


def detect_question_start(line: str) -> tuple[int, str] | None:
    normalized = normalize_markdown_marker_line(line)
    match = QUESTION_START_RE.match(normalized)
    if not match:
        return None
    question_no = parse_question_number_token(match.group(1))
    if question_no is None:
        return None
    return question_no, normalized


def detect_programming_question_start(line: str) -> tuple[int, str] | None:
    normalized = normalize_markdown_marker_line(line)
    match = PROGRAMMING_START_RE.match(normalized)
    if not match:
        return None
    question_no = parse_question_number_token(match.group(1))
    if question_no is None:
        return None
    return question_no, normalized


def markdown_table_cells(line: str) -> list[str]:
    if "|" not in line:
        return []
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return [cell for cell in cells if cell and not re.fullmatch(r"[-: ]+", cell)]


def normalize_section_answer(section: str, answer: object) -> str:
    normalized_answer = str(answer or "").strip().upper()
    if section == QUESTION_SECTION_SINGLE_CHOICE:
        return normalized_answer if normalized_answer in EXAM_CHOICE_KEYS else ""
    if section == QUESTION_SECTION_TRUE_FALSE:
        true_false_map = {
            "√": "√",
            "✓": "√",
            "对": "√",
            "正确": "√",
            "TRUE": "√",
            "T": "√",
            "×": "×",
            "X": "×",
            "✗": "×",
            "错": "×",
            "错误": "×",
            "FALSE": "×",
            "F": "×",
        }
        return true_false_map.get(normalized_answer, str(answer or "").strip())
    return str(answer or "").strip()


def extract_section_answer_data(markdown_text: str) -> tuple[dict[tuple[str, int], str], dict[str, int]]:
    answer_map: dict[tuple[str, int], str] = {}
    section_counts: dict[str, int] = {}
    current_section = QUESTION_SECTION_SINGLE_CHOICE
    pending_section = current_section
    pending_question_numbers: list[int] = []
    for line in markdown_text.splitlines():
        detected_section = detect_question_section_header(line)
        if detected_section:
            current_section = detected_section
            pending_question_numbers = []
            pending_section = current_section
            continue
        cells = markdown_table_cells(line)
        if not cells:
            continue
        head = cells[0].replace(" ", "")
        if "题号" in head:
            pending_question_numbers = [
                int(cell)
                for cell in cells[1:]
                if re.fullmatch(r"\d{1,3}", cell)
            ]
            pending_section = current_section
            continue
        if "答案" in head and pending_question_numbers:
            for question_no, answer in zip(pending_question_numbers, cells[1:], strict=False):
                normalized_answer = normalize_section_answer(pending_section, answer)
                if normalized_answer:
                    answer_map[(pending_section, question_no)] = normalized_answer
            section_counts[pending_section] = max(
                section_counts.get(pending_section, 0),
                max(pending_question_numbers, default=0),
            )
            pending_question_numbers = []
    return answer_map, section_counts


def extract_answer_key_map(markdown_text: str) -> dict[int, str]:
    section_answer_map, _section_counts = extract_section_answer_data(markdown_text)
    return {
        question_no: answer
        for (section, question_no), answer in section_answer_map.items()
        if section == QUESTION_SECTION_SINGLE_CHOICE
    }


def get_section_base(section: str, section_bases: dict[str, int], section_counts: dict[str, int], max_global_no: int) -> int:
    if section in section_bases:
        return section_bases[section]
    if section == QUESTION_SECTION_TRUE_FALSE:
        section_bases[section] = section_counts.get(QUESTION_SECTION_SINGLE_CHOICE) or max_global_no
        return section_bases[section]
    if section == QUESTION_SECTION_PROGRAMMING:
        section_bases[section] = (
            (section_counts.get(QUESTION_SECTION_SINGLE_CHOICE) or 0)
            + (section_counts.get(QUESTION_SECTION_TRUE_FALSE) or 0)
        ) or max_global_no
        return section_bases[section]
    section_bases[section] = 0
    return 0


def should_drop_non_question_line(line: str) -> bool:
    stripped = line.strip()
    compact = re.sub(r"\s+", "", stripped)
    if not stripped:
        return False
    if detect_question_section_header(line):
        return True
    if "|" in stripped and ("题号" in stripped or "答案" in stripped):
        return True
    if re.fullmatch(r"\|?\s*[-:| ]+\s*\|?", stripped):
        return True
    if re.match(r"^\d+\s*(单选题|判断题|编程题)", compact):
        return True
    if compact in {"单选题", "判断题", "编程题"}:
        return True
    return False


def parse_option_line(line: str) -> tuple[str, str] | None:
    match = OPTION_LINE_RE.match(line)
    if not match:
        return None
    key = (match.group(1) or match.group(2) or "").upper()
    if key not in EXAM_CHOICE_KEYS:
        return None
    return key, str(match.group(3) or "").strip()


def detect_section_question_boundary(line: str, current_section: str) -> tuple[str, tuple[int, str] | str] | None:
    detected_section = detect_question_section_header(line)
    if detected_section:
        return "section", detected_section
    programming_start = detect_programming_question_start(line)
    if programming_start:
        return "programming_question", programming_start
    start = (
        None
        if current_section == QUESTION_SECTION_PROGRAMMING
        else detect_question_start(line)
    )
    if start:
        return "question", start
    return None


def is_strong_question_boundary(line: str, current_section: str) -> bool:
    if detect_question_section_header(line):
        return True
    if detect_question_start(line):
        return True
    if detect_programming_question_start(line):
        return True
    return False


def split_programming_reference_solution(stem_md: str, analysis_md: str = "") -> tuple[str, str]:
    lines = str(stem_md or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    split_index: int | None = None
    in_code_block = False
    for index, line in enumerate(lines):
        if CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        normalized = normalize_markdown_marker_line(line)
        compact = re.sub(r"\s+", "", normalized)
        if "参考程序" in compact or "参考代码" in compact:
            split_index = index
            break
    if split_index is None:
        return str(stem_md or "").strip(), str(analysis_md or "").strip()

    stem_part = "\n".join(lines[:split_index]).strip()
    reference_part = "\n".join(lines[split_index:]).strip()
    existing_analysis = str(analysis_md or "").strip()
    merged_analysis = "\n\n".join(part for part in [existing_analysis, reference_part] if part)
    return stem_part, merged_analysis


def split_ocr_markdown_into_question_blocks(markdown_text: str) -> list[dict[str, Any]]:
    cleaned_markdown = clean_imported_markdown(markdown_text)
    answer_map, section_counts = extract_section_answer_data(cleaned_markdown)
    lines = cleaned_markdown.splitlines()
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_section = QUESTION_SECTION_SINGLE_CHOICE
    section_bases = {QUESTION_SECTION_SINGLE_CHOICE: 0}
    max_global_no = 0
    in_code_block = False

    for line in lines:
        boundary = detect_section_question_boundary(line, current_section)
        if boundary and (not in_code_block or is_strong_question_boundary(line, current_section)):
            in_code_block = False
            if boundary[0] == "section":
                detected_section = str(boundary[1])
                if current is not None:
                    blocks.append(current)
                    current = None
                current_section = detected_section
                get_section_base(current_section, section_bases, section_counts, max_global_no)
                continue
            if boundary[0] == "programming_question":
                if current is not None:
                    blocks.append(current)
                current_section = QUESTION_SECTION_PROGRAMMING
                local_question_no, title_line = boundary[1]  # type: ignore[misc]
                section_base = get_section_base(current_section, section_bases, section_counts, max_global_no)
                question_no = section_base + local_question_no
                max_global_no = max(max_global_no, question_no)
                current = {
                    "section": current_section,
                    "local_question_no": local_question_no,
                    "question_no": question_no,
                    "lines": [title_line],
                }
                continue
            local_question_no, title_line = boundary[1]  # type: ignore[misc]
            if current is not None:
                blocks.append(current)
            section_base = get_section_base(current_section, section_bases, section_counts, max_global_no)
            question_no = section_base + local_question_no
            max_global_no = max(max_global_no, question_no)
            current = {
                "section": current_section,
                "local_question_no": local_question_no,
                "question_no": question_no,
                "lines": [title_line],
            }
            continue
        if CODE_FENCE_RE.match(line):
            if current is not None:
                current["lines"].append(line)
            in_code_block = not in_code_block
            continue
        if in_code_block:
            if current is not None:
                current["lines"].append(line)
            continue
        if should_drop_non_question_line(line):
            continue
        if current is None:
            continue
        current["lines"].append(line)

    if current is not None:
        blocks.append(current)

    parsed_blocks: list[dict[str, Any]] = []
    seen_question_numbers: set[int] = set()
    for block in blocks:
        question_no = int(block["question_no"])
        if question_no in seen_question_numbers:
            continue
        seen_question_numbers.add(question_no)
        section = str(block.get("section") or QUESTION_SECTION_SINGLE_CHOICE)
        local_question_no = int(block.get("local_question_no") or question_no)
        stem_lines: list[str] = []
        option_lines: dict[str, list[str]] = {}
        current_option_key = ""
        in_code_block = False
        for raw_line in block["lines"]:
            option = None if in_code_block else parse_option_line(raw_line)
            if in_code_block and section == QUESTION_SECTION_SINGLE_CHOICE:
                option = parse_option_line(raw_line)
                if option:
                    in_code_block = False
            if CODE_FENCE_RE.match(raw_line):
                if section == QUESTION_SECTION_SINGLE_CHOICE and current_option_key:
                    option_lines.setdefault(current_option_key, []).append(raw_line)
                else:
                    stem_lines.append(raw_line)
                in_code_block = not in_code_block
                continue
            if in_code_block:
                if section == QUESTION_SECTION_SINGLE_CHOICE and current_option_key:
                    option_lines.setdefault(current_option_key, []).append(raw_line)
                else:
                    stem_lines.append(raw_line)
                continue
            if section == QUESTION_SECTION_SINGLE_CHOICE and option:
                current_option_key = option[0]
                option_lines.setdefault(current_option_key, [])
                if option[1]:
                    option_lines[current_option_key].append(option[1])
                continue
            if section == QUESTION_SECTION_SINGLE_CHOICE and current_option_key and raw_line.strip():
                option_lines.setdefault(current_option_key, []).append(raw_line.strip())
                continue
            stem_lines.append(raw_line)
        options = {
            key: "\n".join(part for part in option_lines.get(key, []) if part).strip()
            for key in EXAM_CHOICE_KEYS
        }
        option_count = sum(1 for value in options.values() if value)
        stem_md = "\n".join(line.rstrip() for line in stem_lines).strip()
        if not stem_md:
            continue
        question_type = {
            QUESTION_SECTION_SINGLE_CHOICE: (
                ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE
                if option_count >= 2
                else ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN
            ),
            QUESTION_SECTION_TRUE_FALSE: ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
            QUESTION_SECTION_PROGRAMMING: ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
        }.get(section, ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN)
        analysis_md = ""
        if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING:
            stem_md, analysis_md = split_programming_reference_solution(stem_md)
        answer = answer_map.get((section, local_question_no))
        answer_json = (
            {
                "correct_answer" if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE else "answer": answer,
                "source": "ocr_answer_table",
                "needs_teacher_review": False,
            }
            if answer
            else {
                "source": "ocr_question_split",
                "needs_teacher_review": True,
            }
        )
        parsed_blocks.append(
            {
                "question_no": question_no,
                "local_question_no": local_question_no,
                "question_type": question_type,
                "stem_md": stem_md,
                "options": options,
                "answer_json": answer_json,
                "analysis_md": analysis_md,
                "programming_json": {"source": "ocr_question_split"} if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING else {},
            }
        )
    return parsed_blocks


def build_import_job_page_payloads(import_job: ExamQuestionBankImportJob) -> list[dict[str, Any]]:
    rendered_pages = import_job.rendered_pages_json if isinstance(import_job.rendered_pages_json, list) else []
    raw_ocr_pages = import_job.raw_ocr_json if isinstance(import_job.raw_ocr_json, list) else []
    rendered_by_page_no: dict[int, dict[str, Any]] = {}
    raw_by_page_no: dict[int, dict[str, Any]] = {}
    for page in rendered_pages:
        if isinstance(page, dict) and page.get("page_no"):
            rendered_by_page_no[int(page["page_no"])] = page
    for page in raw_ocr_pages:
        if isinstance(page, dict) and page.get("page_no"):
            raw_by_page_no[int(page["page_no"])] = page

    page_numbers = sorted(set(rendered_by_page_no) | set(raw_by_page_no))
    payloads: list[dict[str, Any]] = []
    for page_no in page_numbers:
        raw_page = raw_by_page_no.get(page_no, {})
        markdown_relative_path = raw_page.get("markdown_relative_path", "")
        raw_markdown = read_media_text_file(markdown_relative_path)
        cleaned_markdown = clean_imported_markdown(raw_markdown)
        if not cleaned_markdown:
            continue
        payloads.append(
            {
                "page_no": page_no,
                "markdown": cleaned_markdown,
                "raw_markdown_relative_path": markdown_relative_path,
                "response_relative_path": raw_page.get("response_relative_path", ""),
                "char_count": raw_page.get("char_count") or len(raw_markdown),
                "rendered_page": rendered_by_page_no.get(page_no, {}),
            }
        )
    return payloads


def is_raw_ocr_page_question(question: ExamQuestionBankQuestion) -> bool:
    if question.question_type != ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN:
        return False
    return not question.options.exists()


def combine_raw_ocr_page_markdown(questions: list[ExamQuestionBankQuestion]) -> str:
    return "\n\n".join(str(question.stem_md or "").strip() for question in questions if str(question.stem_md or "").strip())


def materialize_raw_ocr_paper_questions(paper: ExamQuestionBankPaper) -> int:
    raw_questions = list(paper.questions.order_by("question_no", "id"))
    if not raw_questions or not all(is_raw_ocr_page_question(question) for question in raw_questions):
        return 0

    parsed_questions = split_ocr_markdown_into_question_blocks(combine_raw_ocr_page_markdown(raw_questions))
    if not parsed_questions or len(parsed_questions) <= len(raw_questions):
        return 0

    raw_question_ids = [question.id for question in raw_questions]
    with transaction.atomic():
        locked_paper = ExamQuestionBankPaper.objects.select_for_update().get(id=paper.id)
        locked_raw_questions = list(
            locked_paper.questions.select_for_update().order_by("question_no", "id")
        )
        if not locked_raw_questions or not all(is_raw_ocr_page_question(question) for question in locked_raw_questions):
            return 0
        parsed_questions = split_ocr_markdown_into_question_blocks(combine_raw_ocr_page_markdown(locked_raw_questions))
        if not parsed_questions or len(parsed_questions) <= len(locked_raw_questions):
            return 0

        raw_question_ids = [question.id for question in locked_raw_questions]
        ExamQuestionBankOption.objects.filter(question_id__in=raw_question_ids).delete()
        ExamQuestionBankAsset.objects.filter(question_id__in=raw_question_ids).delete()
        locked_paper.questions.filter(id__in=raw_question_ids).delete()

        created_count = 0
        for parsed_question in parsed_questions:
            question_no = int(parsed_question["question_no"])
            question = ExamQuestionBankQuestion.objects.create(
                paper=locked_paper,
                question_uid=f"{locked_paper.source_pdf_id}-q-{question_no:03d}",
                question_no=question_no,
                question_type=str(parsed_question["question_type"]),
                stem_md=str(parsed_question["stem_md"]),
                answer_json=parsed_question["answer_json"] if isinstance(parsed_question.get("answer_json"), dict) else {},
                analysis_md=str(parsed_question.get("analysis_md") or ""),
                programming_json=parsed_question.get("programming_json") if isinstance(parsed_question.get("programming_json"), dict) else {},
                full_json={
                    "source_pdf_id": locked_paper.source_pdf_id,
                    "materialized_from_raw_question_ids": raw_question_ids,
                    "local_question_no": parsed_question.get("local_question_no"),
                },
            )
            created_count += 1
            options = parsed_question.get("options") if isinstance(parsed_question.get("options"), dict) else {}
            for sort_order, option_key in enumerate(EXAM_CHOICE_KEYS, start=1):
                option_text = str(options.get(option_key) or "").strip()
                if not option_text:
                    continue
                ExamQuestionBankOption.objects.create(
                    question=question,
                    option_key=option_key,
                    option_text_md=option_text,
                    sort_order=sort_order,
                )
        return created_count


def separate_programming_reference_solutions_for_paper(paper: ExamQuestionBankPaper) -> int:
    updated_count = 0
    questions = list(
        paper.questions.filter(
            question_type=ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
        ).order_by("question_no", "id")
    )
    for question in questions:
        stem_md, analysis_md = split_programming_reference_solution(question.stem_md, question.analysis_md)
        if stem_md == (question.stem_md or "").strip() and analysis_md == (question.analysis_md or "").strip():
            continue
        question.stem_md = stem_md
        question.analysis_md = analysis_md
        question.save(update_fields=["stem_md", "analysis_md", "updated_at"])
        updated_count += 1
    return updated_count


def normalize_manual_choice_payloads(
    manual_choice_payloads: dict[int, dict[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    normalized_payloads: dict[int, dict[str, Any]] = {}
    for raw_page_no, raw_payload in (manual_choice_payloads or {}).items():
        try:
            page_no = int(raw_page_no)
        except (TypeError, ValueError):
            continue
        if page_no <= 0 or not isinstance(raw_payload, dict):
            continue
        correct_answer = str(raw_payload.get("correct_answer") or "").strip().upper()
        analysis_md = str(raw_payload.get("analysis_md") or raw_payload.get("analysis") or "").strip()
        raw_options = raw_payload.get("options")
        options: dict[str, str] = {}
        if isinstance(raw_options, dict):
            for key in EXAM_CHOICE_KEYS:
                option_text = str(raw_options.get(key) or raw_options.get(key.lower()) or "").strip()
                options[key] = option_text or f"选项 {key}（见题图）"
        else:
            for key in EXAM_CHOICE_KEYS:
                options[key] = f"选项 {key}（见题图）"
        if correct_answer or analysis_md or any(options.values()):
            normalized_payloads[page_no] = {
                "correct_answer": correct_answer,
                "analysis_md": analysis_md,
                "options": options,
            }
    return normalized_payloads


def confirm_exam_question_bank_import_job(
    import_job: ExamQuestionBankImportJob,
    *,
    manual_choice_payloads: dict[int, dict[str, Any]] | None = None,
) -> tuple[ExamQuestionBankPaper, dict[str, int]]:
    if import_job.status not in {
        ExamQuestionBankImportJob.STATUS_OCR_DONE,
        ExamQuestionBankImportJob.STATUS_IMPORTED,
    }:
        raise ExamPaperImportConfirmError("只有 OCR 已完成的任务才能确认入库。")

    page_payloads = build_import_job_page_payloads(import_job)
    if not page_payloads:
        raise ExamPaperImportConfirmError("当前任务没有可导入的 OCR Markdown。")

    normalized_manual_choices = normalize_manual_choice_payloads(manual_choice_payloads)
    source_type = detect_exam_import_source_type(import_job.source_filename or import_job.source_pdf.name)
    question_payloads: list[dict[str, Any]] = []
    used_question_numbers: set[int] = set()
    use_page_manual_review = bool(normalized_manual_choices) or source_type == SOURCE_TYPE_IMAGE
    if not use_page_manual_review:
        combined_markdown = "\n\n".join(str(payload["markdown"]) for payload in page_payloads if str(payload.get("markdown") or "").strip())
        parsed_questions = split_ocr_markdown_into_question_blocks(combined_markdown)
        if parsed_questions:
            source_page_payload = page_payloads[0]
            for parsed_question in parsed_questions:
                question_no = int(parsed_question["question_no"])
                if question_no in used_question_numbers:
                    continue
                used_question_numbers.add(question_no)
                question_payloads.append(
                    {
                        "source_page_payload": source_page_payload,
                        "question_no": question_no,
                        "question_uid": f"{import_job.source_pdf_id}-q-{question_no:03d}",
                        "question_type": parsed_question["question_type"],
                        "stem_md": parsed_question["stem_md"],
                        "answer_json": parsed_question["answer_json"],
                        "analysis_md": parsed_question["analysis_md"],
                        "options": parsed_question["options"],
                        "programming_json": parsed_question.get("programming_json") if isinstance(parsed_question.get("programming_json"), dict) else {},
                        "attach_page_asset": False,
                        "manual_choice_review": {},
                    }
                )

    if not question_payloads:
        for payload in page_payloads:
            page_no = int(payload["page_no"])
            manual_choice = normalized_manual_choices.get(page_no, {})
            if manual_choice:
                question_payloads.append(
                    {
                        "source_page_payload": payload,
                        "question_no": page_no,
                        "question_uid": f"{import_job.source_pdf_id}-page-{page_no:03d}",
                        "question_type": ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                        "stem_md": str(payload["markdown"]),
                        "answer_json": {
                            "correct_answer": str(manual_choice.get("correct_answer") or "").strip().upper(),
                            "source": "teacher_manual_choice_review",
                            "needs_teacher_review": not bool(str(manual_choice.get("correct_answer") or "").strip()),
                        },
                        "analysis_md": str(manual_choice.get("analysis_md") or ""),
                        "options": manual_choice.get("options") if isinstance(manual_choice.get("options"), dict) else {},
                        "programming_json": {},
                        "attach_page_asset": True,
                        "manual_choice_review": manual_choice,
                    }
                )
                used_question_numbers.add(page_no)
                continue

            parsed_questions = split_ocr_markdown_into_question_blocks(str(payload["markdown"]))
            for parsed_question in parsed_questions:
                question_no = int(parsed_question["question_no"])
                if question_no in used_question_numbers:
                    continue
                used_question_numbers.add(question_no)
                question_payloads.append(
                    {
                        "source_page_payload": payload,
                        "question_no": question_no,
                        "question_uid": f"{import_job.source_pdf_id}-q-{question_no:03d}",
                        "question_type": parsed_question["question_type"],
                        "stem_md": parsed_question["stem_md"],
                        "answer_json": parsed_question["answer_json"],
                        "analysis_md": parsed_question["analysis_md"],
                        "options": parsed_question["options"],
                        "programming_json": parsed_question.get("programming_json") if isinstance(parsed_question.get("programming_json"), dict) else {},
                        "attach_page_asset": False,
                        "manual_choice_review": {},
                    }
                )
            if parsed_questions:
                continue

            fallback_question_no = page_no
            while fallback_question_no in used_question_numbers:
                fallback_question_no += 1
            used_question_numbers.add(fallback_question_no)
            question_payloads.append(
                {
                    "source_page_payload": payload,
                    "question_no": fallback_question_no,
                    "question_uid": f"{import_job.source_pdf_id}-page-{page_no:03d}",
                    "question_type": ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN,
                    "stem_md": str(payload["markdown"]),
                    "answer_json": {
                        "source": "ocr_markdown_page",
                        "needs_teacher_review": True,
                    },
                    "analysis_md": "",
                    "options": {},
                    "programming_json": {},
                    "attach_page_asset": source_type == SOURCE_TYPE_IMAGE,
                    "manual_choice_review": {},
                }
            )

    with transaction.atomic():
        paper, created = ExamQuestionBankPaper.objects.update_or_create(
            source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
            source_pdf_id=import_job.source_pdf_id,
            defaults={
                "level": import_job.level_code,
                "year": import_job.year or timezone.localdate().year,
                "month": import_job.month or timezone.localdate().month,
                "source_file": import_job.source_filename,
                "title": import_job.title,
                "import_batch_uid": f"exam-import-job-{import_job.id}",
                "is_active": True,
            },
        )
        old_question_ids = list(paper.questions.values_list("id", flat=True))
        if old_question_ids:
            ExamQuestionBankOption.objects.filter(question_id__in=old_question_ids).delete()
            ExamQuestionBankAsset.objects.filter(question_id__in=old_question_ids).delete()
            paper.questions.all().delete()

        questions_created = 0
        options_created = 0
        assets_created = 0
        for payload in question_payloads:
            page_payload = payload["source_page_payload"]
            page_no = int(page_payload["page_no"])
            question_no = int(payload["question_no"])
            options = payload["options"] if isinstance(payload.get("options"), dict) else {}
            analysis_md = str(payload.get("analysis_md") or "").strip()
            question = ExamQuestionBankQuestion.objects.create(
                paper=paper,
                question_uid=str(payload["question_uid"]),
                question_no=question_no,
                question_type=str(payload["question_type"]),
                stem_md=str(payload["stem_md"]),
                answer_json=payload["answer_json"] if isinstance(payload["answer_json"], dict) else {},
                analysis_md=analysis_md,
                programming_json=payload.get("programming_json") if isinstance(payload.get("programming_json"), dict) else {},
                full_json={
                    "import_job_id": import_job.id,
                    "page_no": page_no,
                    "source_pdf_id": import_job.source_pdf_id,
                    "raw_markdown_relative_path": page_payload["raw_markdown_relative_path"],
                    "response_relative_path": page_payload["response_relative_path"],
                    "char_count": page_payload["char_count"],
                    "manual_choice_review": payload.get("manual_choice_review") or {},
                },
            )
            questions_created += 1
            if options:
                for sort_order, option_key in enumerate(EXAM_CHOICE_KEYS, start=1):
                    option_text = str(options.get(option_key) or "").strip()
                    if not option_text:
                        continue
                    ExamQuestionBankOption.objects.create(
                        question=question,
                        option_key=option_key,
                        option_text_md=option_text,
                        sort_order=sort_order,
                    )
                    options_created += 1
            rendered_page = (
                page_payload.get("rendered_page")
                if bool(payload.get("attach_page_asset")) and isinstance(page_payload.get("rendered_page"), dict)
                else {}
            )
            relative_path = str(rendered_page.get("relative_path") or "")
            if relative_path:
                ExamQuestionBankAsset.objects.create(
                    question=question,
                    asset_uid=f"{import_job.source_pdf_id}-page-{page_no:03d}",
                    asset_role="content",
                    asset_type="image/png",
                    relative_path=relative_path,
                    public_url="",
                    alt=f"第 {page_no} 页原始截图",
                    width=rendered_page.get("width") or None,
                    height=rendered_page.get("height") or None,
                )
                assets_created += 1

        import_job.status = ExamQuestionBankImportJob.STATUS_IMPORTED
        import_job.status_notes = f"已确认入库，生成试卷快照 #{paper.id}，共 {questions_created} 道题。"
        import_job.error_message = ""
        import_job.save(update_fields=["status", "status_notes", "error_message", "updated_at"])

    return paper, {
        "papers_created": 1 if created else 0,
        "papers_updated": 0 if created else 1,
        "questions_created": questions_created,
        "options_created": options_created,
        "assets_created": assets_created,
    }


def image_data_url(image_path: Path) -> str:
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def extract_qwen_message_text(response_json: dict[str, Any]) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content)


def qwen_ocr_image(*, image_path: Path, prompt: str = OCR_PAGE_PROMPT) -> tuple[str, dict[str, Any]]:
    api_key = get_qwen_api_key()
    base_url = get_qwen_base_url()
    model = get_qwen_ocr_model()
    if not api_key:
        raise ExamPaperImportError("Qwen OCR 未配置 API Key，请配置 QWEN_API_KEY 或 DASHSCOPE_API_KEY。")
    if not base_url or not model:
        raise ExamPaperImportError("Qwen OCR 缺少 QWEN_BASE_URL 或 QWEN_OCR_MODEL。")

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url(image_path)}},
                ],
            }
        ],
        "temperature": float(getattr(settings, "QWEN_TEMPERATURE", 0)),
        "top_p": float(getattr(settings, "QWEN_TOP_P", 0.1)),
    }
    if not bool(getattr(settings, "QWEN_OCR_ENABLE_THINKING", False)):
        payload["extra_body"] = {"enable_thinking": False}

    url = base_url.rstrip("/") + "/chat/completions"
    timeout_seconds = max(int(getattr(settings, "QWEN_TIMEOUT_SECONDS", 180)), 1)
    max_attempts = max(int(getattr(settings, "QWEN_MAX_RETRIES", 3)), 1)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
            if response.status_code == 401:
                raise ExamPaperImportError("Qwen OCR 认证失败：API Key 无效或权限不足。")
            if response.status_code == 404:
                raise ExamPaperImportError("Qwen OCR 模型或地址不可用：可能是模型未开通、地域不匹配或 QWEN_BASE_URL 错误。")
            if response.status_code >= 500:
                raise ExamPaperImportError(f"Qwen OCR 服务暂时不可用：HTTP {response.status_code}。")
            response.raise_for_status()
            response_json = response.json()
            return extract_qwen_message_text(response_json), response_json
        except requests.Timeout as exc:
            last_error = exc
            if attempt >= max_attempts:
                raise ExamPaperImportError("Qwen OCR 请求超时：请调大 QWEN_TIMEOUT_SECONDS 或稍后重试。") from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            response_text = exc.response.text[:500] if exc.response is not None else ""
            raise ExamPaperImportError(f"Qwen OCR 请求失败：HTTP {status_code}，{response_text}") from exc
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= max_attempts:
                raise ExamPaperImportError(f"Qwen OCR 请求失败：{type(exc).__name__}") from exc
        if attempt < max_attempts:
            time.sleep(min(2 * attempt, 8))

    raise ExamPaperImportError(f"Qwen OCR 请求失败：{last_error}") from last_error


def render_pdf_pages(import_job: ExamQuestionBankImportJob, *, workspace_dir: Path) -> list[dict[str, Any]]:
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError as exc:
        raise ExamPaperImportError("当前环境未安装 pypdfium2，无法渲染 PDF 页面。") from exc

    page_image_dir = workspace_dir / "page_images"
    page_image_dir.mkdir(parents=True, exist_ok=True)
    dpi = max(int(getattr(settings, "EXAM_PDF_RENDER_DPI", 300)), 72)
    scale = dpi / 72
    document = None
    pages: list[dict[str, Any]] = []
    try:
        document = pdfium.PdfDocument(import_job.source_pdf.path)
        for page_index in range(len(document)):
            page = document[page_index]
            bitmap = None
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                image_path = page_image_dir / f"page_{page_index + 1:03d}.png"
                image.save(image_path, format="PNG")
                pages.append(
                    {
                        "page_no": page_index + 1,
                        "relative_path": get_media_relative_path(image_path),
                        "width": int(image.width),
                        "height": int(image.height),
                    }
                )
            finally:
                if bitmap is not None:
                    close_bitmap = getattr(bitmap, "close", None)
                    if callable(close_bitmap):
                        close_bitmap()
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    close_page()
    except Exception as exc:
        if isinstance(exc, ExamPaperImportError):
            raise
        raise ExamPaperImportError(f"PDF 页面渲染失败：{type(exc).__name__}: {exc}") from exc
    finally:
        if document is not None:
            close_document = getattr(document, "close", None)
            if callable(close_document):
                close_document()
    if not pages:
        raise ExamPaperImportError("PDF 没有可渲染页面。")
    return pages


def get_image_dimensions(image_path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None, None
    try:
        with Image.open(image_path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def set_import_job_running_state(
    import_job: ExamQuestionBankImportJob,
    *,
    workspace_dir: Path,
    status_notes: str,
) -> None:
    import_job.status = ExamQuestionBankImportJob.STATUS_RENDERING
    import_job.status_notes = status_notes
    import_job.workspace_relative_path = get_media_relative_path(workspace_dir)
    import_job.qwen_model = get_qwen_ocr_model()
    import_job.qwen_base_url = get_qwen_base_url()
    import_job.error_message = ""
    import_job.save(
        update_fields=[
            "status",
            "status_notes",
            "workspace_relative_path",
            "qwen_model",
            "qwen_base_url",
            "error_message",
            "updated_at",
        ]
    )


def process_text_exam_import_job(
    import_job: ExamQuestionBankImportJob,
    *,
    workspace_dir: Path,
    raw_ocr_dir: Path,
    response_dir: Path,
    source_type: str,
) -> ExamQuestionBankImportJob:
    set_import_job_running_state(
        import_job,
        workspace_dir=workspace_dir,
        status_notes="正在本地抽取文本并转换为 Markdown。",
    )
    markdown_text = extract_text_source_markdown(Path(import_job.source_pdf.path), source_type=source_type)
    markdown_path = raw_ocr_dir / "page_001.md"
    response_path = response_dir / "page_001.json"
    markdown_path.write_text(markdown_text, encoding="utf-8")
    response_path.write_text(
        json.dumps(
            {
                "provider": "local_text",
                "source_type": source_type,
                "source_filename": import_job.source_filename,
                "char_count": len(markdown_text),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    import_job.rendered_pages_json = []
    import_job.raw_ocr_json = [
        {
            "page_no": 1,
            "markdown_relative_path": get_media_relative_path(markdown_path),
            "response_relative_path": get_media_relative_path(response_path),
            "char_count": len(markdown_text),
            "provider": "local_text",
            "source_type": source_type,
        }
    ]
    import_job.page_count = 1
    import_job.rendered_page_count = 0
    import_job.ocr_page_count = 1
    import_job.status = ExamQuestionBankImportJob.STATUS_OCR_DONE
    import_job.status_notes = "文本类文件已直接转换为 Markdown，等待老师确认入库。"
    import_job.error_message = ""
    import_job.save(
        update_fields=[
            "rendered_pages_json",
            "raw_ocr_json",
            "page_count",
            "rendered_page_count",
            "ocr_page_count",
            "status",
            "status_notes",
            "error_message",
            "updated_at",
        ]
    )
    return import_job


def process_image_exam_import_job(
    import_job: ExamQuestionBankImportJob,
    *,
    workspace_dir: Path,
    raw_ocr_dir: Path,
    response_dir: Path,
) -> ExamQuestionBankImportJob:
    page_image_dir = workspace_dir / "page_images"
    page_image_dir.mkdir(parents=True, exist_ok=True)
    set_import_job_running_state(
        import_job,
        workspace_dir=workspace_dir,
        status_notes="正在准备图片 OCR。",
    )
    source_path = Path(import_job.source_pdf.path)
    image_suffix = source_path.suffix.lower() or ".png"
    image_path = page_image_dir / f"page_001{image_suffix}"
    shutil.copyfile(source_path, image_path)
    width, height = get_image_dimensions(image_path)
    pages = [
        {
            "page_no": 1,
            "relative_path": get_media_relative_path(image_path),
            "width": width,
            "height": height,
        }
    ]
    import_job.rendered_pages_json = pages
    import_job.page_count = 1
    import_job.rendered_page_count = 1
    import_job.status = ExamQuestionBankImportJob.STATUS_OCR_RUNNING
    import_job.status_notes = "图片已准备完成，正在调用 Qwen OCR。"
    import_job.save(
        update_fields=[
            "rendered_pages_json",
            "page_count",
            "rendered_page_count",
            "status",
            "status_notes",
            "updated_at",
        ]
    )
    text, response_json = qwen_ocr_image(image_path=image_path)
    markdown_path = raw_ocr_dir / "page_001.md"
    response_path = response_dir / "page_001.json"
    markdown_path.write_text(text, encoding="utf-8")
    response_path.write_text(json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8")
    import_job.raw_ocr_json = [
        {
            "page_no": 1,
            "markdown_relative_path": get_media_relative_path(markdown_path),
            "response_relative_path": get_media_relative_path(response_path),
            "char_count": len(text),
            "provider": "qwen",
            "model": get_qwen_ocr_model(),
        }
    ]
    import_job.ocr_page_count = 1
    import_job.status = ExamQuestionBankImportJob.STATUS_OCR_DONE
    import_job.status_notes = "Qwen OCR 已完成，等待结构化解析和老师复核。"
    import_job.error_message = ""
    import_job.save(update_fields=["raw_ocr_json", "ocr_page_count", "status", "status_notes", "error_message", "updated_at"])
    return import_job


def qwen_ocr_pdf_page(
    page: dict[str, Any],
    *,
    raw_ocr_dir: Path,
    response_dir: Path,
) -> dict[str, Any]:
    page_no = int(page["page_no"])
    image_path = Path(settings.MEDIA_ROOT) / str(page["relative_path"])
    text, response_json = qwen_ocr_image(image_path=image_path)
    markdown_path = raw_ocr_dir / f"page_{page_no:03d}.md"
    response_path = response_dir / f"page_{page_no:03d}.json"
    markdown_path.write_text(text, encoding="utf-8")
    response_path.write_text(json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "page_no": page_no,
        "markdown_relative_path": get_media_relative_path(markdown_path),
        "response_relative_path": get_media_relative_path(response_path),
        "char_count": len(text),
        "provider": "qwen",
        "model": get_qwen_ocr_model(),
    }


def update_pdf_ocr_progress(
    import_job: ExamQuestionBankImportJob,
    *,
    completed_results: dict[int, dict[str, Any]],
    total_pages: int,
    concurrency: int,
) -> None:
    ordered_results = [completed_results[page_no] for page_no in sorted(completed_results)]
    suffix = f"（并发 {concurrency} 页）" if concurrency > 1 else ""
    ExamQuestionBankImportJob.objects.filter(id=import_job.id).update(
        raw_ocr_json=ordered_results,
        ocr_page_count=len(ordered_results),
        status_notes=f"Qwen OCR 已完成 {len(ordered_results)}/{total_pages} 页{suffix}。",
        updated_at=timezone.now(),
    )


def process_pdf_exam_import_job(
    import_job: ExamQuestionBankImportJob,
    *,
    workspace_dir: Path,
    raw_ocr_dir: Path,
    response_dir: Path,
) -> ExamQuestionBankImportJob:
    set_import_job_running_state(
        import_job,
        workspace_dir=workspace_dir,
        status_notes="正在把 PDF 渲染成页面截图。",
    )
    pages = render_pdf_pages(import_job, workspace_dir=workspace_dir)
    import_job.rendered_pages_json = pages
    import_job.page_count = len(pages)
    import_job.rendered_page_count = len(pages)
    import_job.status = ExamQuestionBankImportJob.STATUS_OCR_RUNNING
    import_job.status_notes = f"PDF 已渲染 {len(pages)} 页，正在逐页调用 Qwen OCR。"
    import_job.save(
        update_fields=[
            "rendered_pages_json",
            "page_count",
            "rendered_page_count",
            "status",
            "status_notes",
            "updated_at",
        ]
    )

    completed_results: dict[int, dict[str, Any]] = {}
    concurrency = min(get_exam_qwen_ocr_concurrency(), len(pages))
    if concurrency <= 1 or len(pages) == 1:
        for page in pages:
            result = qwen_ocr_pdf_page(page, raw_ocr_dir=raw_ocr_dir, response_dir=response_dir)
            completed_results[int(result["page_no"])] = result
            update_pdf_ocr_progress(
                import_job,
                completed_results=completed_results,
                total_pages=len(pages),
                concurrency=1,
            )
    else:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="exam-pdf-ocr") as executor:
            future_by_page_no = {
                executor.submit(qwen_ocr_pdf_page, page, raw_ocr_dir=raw_ocr_dir, response_dir=response_dir): int(page["page_no"])
                for page in pages
            }
            try:
                for future in as_completed(future_by_page_no):
                    result = future.result()
                    completed_results[int(result["page_no"])] = result
                    update_pdf_ocr_progress(
                        import_job,
                        completed_results=completed_results,
                        total_pages=len(pages),
                        concurrency=concurrency,
                    )
            except Exception:
                for future in future_by_page_no:
                    future.cancel()
                raise

    import_job.refresh_from_db()
    import_job.status = ExamQuestionBankImportJob.STATUS_OCR_DONE
    import_job.status_notes = "Qwen OCR 已完成，等待结构化解析和老师复核。"
    import_job.error_message = ""
    import_job.save(update_fields=["status", "status_notes", "error_message", "updated_at"])
    return import_job


def process_exam_question_bank_import_job(import_job: ExamQuestionBankImportJob) -> ExamQuestionBankImportJob:
    source_type = detect_exam_import_source_type(import_job.source_filename or import_job.source_pdf.name)
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ExamPaperImportError("当前文件类型不支持导入识别。")

    workspace_name = f"{import_job.id}_{import_job.source_pdf_id or 'exam_file'}"
    workspace_dir = Path(settings.MEDIA_ROOT) / "exam_paper_import_workspace" / workspace_name
    raw_ocr_dir = workspace_dir / "raw_ocr"
    response_dir = workspace_dir / "qwen_responses"
    raw_ocr_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)

    if source_type in TEXT_SOURCE_TYPES:
        return process_text_exam_import_job(
            import_job,
            workspace_dir=workspace_dir,
            raw_ocr_dir=raw_ocr_dir,
            response_dir=response_dir,
            source_type=source_type,
        )
    if source_type == SOURCE_TYPE_IMAGE:
        return process_image_exam_import_job(
            import_job,
            workspace_dir=workspace_dir,
            raw_ocr_dir=raw_ocr_dir,
            response_dir=response_dir,
        )
    return process_pdf_exam_import_job(
        import_job,
        workspace_dir=workspace_dir,
        raw_ocr_dir=raw_ocr_dir,
        response_dir=response_dir,
    )


def fail_exam_question_bank_import_job(import_job: ExamQuestionBankImportJob, exc: Exception) -> None:
    ExamQuestionBankImportJob.objects.filter(id=import_job.id).update(
        status=ExamQuestionBankImportJob.STATUS_FAILED,
        error_message=f"{type(exc).__name__}: {exc}",
        status_notes="处理失败，请检查文件内容、Qwen 配置或稍后重试。",
        updated_at=timezone.now(),
    )
