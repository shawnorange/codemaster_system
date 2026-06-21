from __future__ import annotations

import base64
import certifi
import codecs
import hashlib
import io
import json
import mimetypes
import re
import time
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    HomeworkAssignment,
    HomeworkImportJob,
    HomeworkQuestion,
    HomeworkSubmission,
    HomeworkSubmissionAnswer,
    PortalUser,
    Student,
)


QUESTION_START_RE = re.compile(r"^(?:第\s*\d+\s*题|\d+\s*[\.\)、]|Q\s*\d+\s*[:\.\)])\s*(.*)$", re.IGNORECASE)
VISION_BLOCK_START_RE = re.compile(
    r"^(?:【?\s*第\s*\d+\s*题\s*】?|第\s*\d+\s*题|[（(]\s*\d+\s*[）)]|\d+\s*[\.\)、)]|Q\s*\d+\s*[:：\.\)])\s*(.*)$",
    re.IGNORECASE,
)
OPTION_RE = re.compile(r"^([A-D])\s*[\.\)、:：]\s*(.+)$", re.IGNORECASE)
ANSWER_LABELS = (
    "正确答案",
    "标准答案",
    "参考答案",
    "答案",
    "Correct Answer",
    "Answer",
)
ANALYSIS_LABELS = (
    "答案解析",
    "参考解析",
    "解析",
    "讲解",
    "Explanation",
)
ANSWER_LABEL_PATTERN = "|".join(re.escape(item) for item in sorted(ANSWER_LABELS, key=len, reverse=True))
ANALYSIS_LABEL_PATTERN = "|".join(re.escape(item) for item in sorted(ANALYSIS_LABELS, key=len, reverse=True))
ANSWER_RE = re.compile(
    rf"^(?:【?\s*(?:{ANSWER_LABEL_PATTERN})\s*】?)(?!\s*(?:解析|Explanation))\s*[:：]?\s*(.+)$",
    re.IGNORECASE,
)
ANALYSIS_RE = re.compile(rf"^(?:【?\s*(?:{ANALYSIS_LABEL_PATTERN})\s*】?)\s*[:：]?\s*(.*)$", re.IGNORECASE)
ANSWER_LETTER_RE = re.compile(r"^\s*([A-D])(?:\b|[\.\)、:：]|$)", re.IGNORECASE)
INLINE_ANALYSIS_SPLIT_RE = re.compile(
    rf"(?:[。.;；]\s*|\s+)(?:【?\s*(?:{ANALYSIS_LABEL_PATTERN})\s*】?)\s*[:：]?\s*(.*)$",
    re.IGNORECASE,
)
WHITESPACE_RE = re.compile(r"\s+")
HTTP_ERROR_PREVIEW_LIMIT = 240
TRACE_PREVIEW_LIMIT = 220
JSON_TEXT_ESCAPE_PREFIX = "__cm_json_text_unicode_escape__:"
QUESTION_SOURCE_KNOWLEDGE_MARKER = "question_source_knowledge="


class HomeworkImportParseError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "",
        failure_type: str = "",
        retryable: bool = False,
        user_message: str = "",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.failure_type = failure_type or error_code or "unknown_error"
        self.retryable = retryable
        self.user_message = user_message
        self.http_status = http_status


@dataclass(slots=True)
class HomeworkImportRoute:
    name: str
    source_type: str
    selected_text_source: str
    use_local_text: bool
    use_vision: bool
    use_qwen: bool
    reason: str


@dataclass(slots=True)
class HomeworkImportTrace:
    source_type: str
    router_enabled: bool
    debug_enabled: bool
    duplicate_content_hit: bool = False
    duplicate_content_note: str = ""
    route_name: str = ""
    route_reason: str = ""
    selected_text_source: str = ""
    local_text_attempted: bool = False
    local_text_success: bool = False
    local_text_length: int = 0
    local_text_line_count: int = 0
    local_text_error: str = ""
    local_text: str = ""
    vision_chain_used: bool = False
    vision_attempted: bool = False
    vision_success: bool = False
    vision_text_length: int = 0
    vision_text_line_count: int = 0
    vision_error: str = ""
    vision_text: str = ""
    vision_text_preview: str = ""
    vision_block_split_applied: bool = False
    vision_block_count: int = 0
    vision_block_results: list[str] = field(default_factory=list)
    vision_request_results: list[str] = field(default_factory=list)
    vision_final_failure_code: str = ""
    vision_final_failure_type: str = ""
    vision_all_timeout_abort: bool = False
    vision_qwen_failure_stop: bool = False
    vision_qwen_failure_reason: str = ""
    vision_manual_review_message: str = ""
    user_facing_message: str = ""
    image_sliced: bool = False
    image_slice_count: int = 0
    image_slice_results: list[str] = field(default_factory=list)
    pdf_raster_attempted: bool = False
    pdf_rasterized: bool = False
    pdf_total_pages: int = 0
    pdf_pages_processed: int = 0
    pdf_page_limit_hit: bool = False
    pdf_render_results: list[str] = field(default_factory=list)
    pdf_page_slice_results: list[str] = field(default_factory=list)
    qwen_attempted: bool = False
    qwen_success: bool = False
    qwen_error: str = ""
    qwen_block_attempt_count: int = 0
    qwen_block_success_count: int = 0
    qwen_block_failure_count: int = 0
    qwen_block_results: list[str] = field(default_factory=list)
    fallback_attempted: bool = False
    fallback_used: bool = False
    fallback_reason: str = ""
    candidate_count: int = 0
    failure_step: str = ""
    failure_reason: str = ""
    model_notes: list[str] = field(default_factory=list)
    progress_started_at: float = 0.0
    progress_step: str = ""
    progress_detail: str = ""


@dataclass(slots=True)
class HomeworkVisionInput:
    label: str
    image_bytes: bytes
    mime_type: str


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in {"script", "style"}:
            self._skip_depth += 1
        elif tag in {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "div", "li", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._skip_depth:
            return
        self.parts.append(data)

    def get_text(self) -> str:
        return "".join(self.parts)


def detect_homework_source_type(filename: str) -> str:
    extension = Path(filename or "").suffix.lower()
    if extension == ".pdf":
        return HomeworkImportJob.SOURCE_TYPE_PDF
    if extension in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return HomeworkImportJob.SOURCE_TYPE_IMAGE
    if extension in {".html", ".htm"}:
        return HomeworkImportJob.SOURCE_TYPE_HTML
    if extension == ".txt":
        return HomeworkImportJob.SOURCE_TYPE_TEXT
    if extension == ".docx":
        return HomeworkImportJob.SOURCE_TYPE_DOCX
    if extension == ".xlsx":
        return HomeworkImportJob.SOURCE_TYPE_XLSX
    return ""


def compute_uploaded_file_sha256(uploaded_file: Any) -> str:
    hasher = hashlib.sha256()
    if hasattr(uploaded_file, "chunks"):
        for chunk in uploaded_file.chunks():
            if chunk:
                hasher.update(chunk)
    else:
        raw_bytes = uploaded_file.read()
        if raw_bytes:
            hasher.update(raw_bytes)
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    return hasher.hexdigest()


def normalize_homework_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    normalized = normalized.replace("\xa0", " ")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def normalize_preserved_text(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def trim_outer_blank_lines(value: str) -> str:
    lines = normalize_preserved_text(value).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def normalize_candidate_text(value: object) -> str:
    return WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _needs_sql_ascii_json_escape(value: str) -> bool:
    return value.startswith(JSON_TEXT_ESCAPE_PREFIX) or any(ord(char) > 127 for char in value)


def encode_sql_ascii_json_text(value: object) -> object:
    if isinstance(value, str):
        if not _needs_sql_ascii_json_escape(value):
            return value
        escaped = value.encode("unicode_escape").decode("ascii")
        return f"{JSON_TEXT_ESCAPE_PREFIX}{escaped}"
    if isinstance(value, list):
        return [encode_sql_ascii_json_text(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): encode_sql_ascii_json_text(item)
            for key, item in value.items()
        }
    return value


def decode_sql_ascii_json_text(value: object) -> object:
    if isinstance(value, str):
        if not value.startswith(JSON_TEXT_ESCAPE_PREFIX):
            return value
        escaped = value[len(JSON_TEXT_ESCAPE_PREFIX):]
        try:
            return codecs.decode(escaped, "unicode_escape")
        except UnicodeDecodeError:
            return escaped
    if isinstance(value, list):
        return [decode_sql_ascii_json_text(item) for item in value]
    if isinstance(value, dict):
        return {
            key: decode_sql_ascii_json_text(item)
            for key, item in value.items()
        }
    return value


def count_non_empty_lines(value: str) -> int:
    return sum(1 for line in value.splitlines() if line.strip())


def build_trace_preview(value: str, *, limit: int = TRACE_PREVIEW_LIMIT) -> str:
    preview = normalize_homework_text(value)
    if len(preview) <= limit:
        return preview
    return f"{preview[:limit]}..."


def normalize_choice_options(raw_options: object) -> dict[str, str]:
    if not isinstance(raw_options, dict):
        raw_options = {}
    options = {}
    for key in ["A", "B", "C", "D"]:
        options[key] = normalize_candidate_text(raw_options.get(key) or raw_options.get(key.lower()) or "")
    return options


def normalize_candidate_stem(value: object) -> str:
    normalized = normalize_preserved_text(value)
    kept_lines: list[str] = []
    for raw_line in normalized.splitlines():
        cleaned = normalize_candidate_text(raw_line)
        if not cleaned and kept_lines:
            kept_lines.append("")
            continue
        if ANSWER_RE.match(cleaned) or ANALYSIS_RE.match(cleaned):
            continue
        kept_lines.append(raw_line)
    return trim_outer_blank_lines("\n".join(kept_lines))


def split_answer_and_analysis_fragment(value: object) -> tuple[str, str]:
    cleaned = normalize_candidate_text(value)
    if not cleaned:
        return "", ""
    inline_analysis_match = INLINE_ANALYSIS_SPLIT_RE.search(cleaned)
    if not inline_analysis_match:
        return cleaned.strip(" ：:，,。；;"), ""
    answer_text = cleaned[: inline_analysis_match.start()].strip(" ：:，,。；;")
    analysis_text = normalize_candidate_text(inline_analysis_match.group(1))
    return answer_text, analysis_text


def resolve_candidate_correct_answer(raw_answer: object, options: dict[str, str]) -> str:
    cleaned_answer = normalize_candidate_text(raw_answer)
    labeled_answer_match = ANSWER_RE.match(cleaned_answer)
    if labeled_answer_match:
        cleaned_answer = labeled_answer_match.group(1)
    answer_text, _ = split_answer_and_analysis_fragment(cleaned_answer)
    if not answer_text:
        return ""

    leading_letter_match = ANSWER_LETTER_RE.match(answer_text)
    if leading_letter_match:
        return leading_letter_match.group(1).upper()

    compact_answer = re.sub(r"[\s\.\)、:：,，。；;!！\(\)（）\[\]【】]", "", answer_text).upper()
    if compact_answer in {"A", "B", "C", "D"}:
        return compact_answer

    normalized_answer_text = normalize_candidate_text(answer_text)
    for key in ["A", "B", "C", "D"]:
        option_text = normalize_candidate_text(options.get(key))
        if not option_text:
            continue
        if normalized_answer_text == option_text:
            return key
    return ""


def sanitize_candidate(candidate: dict, *, index: int) -> dict:
    decoded_candidate = decode_sql_ascii_json_text(candidate)
    if isinstance(decoded_candidate, dict):
        candidate = decoded_candidate
    options = normalize_choice_options(candidate.get("options"))
    correct_answer = resolve_candidate_correct_answer(candidate.get("correct_answer"), options)
    _, inline_analysis = split_answer_and_analysis_fragment(candidate.get("correct_answer"))
    notes = normalize_candidate_text(candidate.get("notes"))
    analysis = normalize_candidate_text(candidate.get("analysis")) or inline_analysis
    return {
        "index": index,
        "stem": normalize_candidate_stem(candidate.get("stem")),
        "options": options,
        "correct_answer": correct_answer if correct_answer in {"A", "B", "C", "D"} else "",
        "analysis": analysis,
        "notes": notes,
        "confidence": candidate.get("confidence"),
    }


def _extract_text_from_html(raw_bytes: bytes) -> str:
    extractor = _HTMLTextExtractor()
    decoded = raw_bytes.decode("utf-8", errors="ignore")
    extractor.feed(decoded)
    return normalize_homework_text(extractor.get_text())


def _extract_text_from_txt(raw_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return normalize_preserved_text(raw_bytes.decode(encoding))
        except UnicodeDecodeError:
            continue
    return normalize_preserved_text(raw_bytes.decode("utf-8", errors="ignore"))


def _extract_text_from_docx(raw_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        try:
            xml_bytes = archive.read("word/document.xml")
        except KeyError as exc:
            raise HomeworkImportParseError("DOCX 文件中缺少 word/document.xml，无法解析。") from exc
    root = ElementTree.fromstring(xml_bytes)
    parts = []
    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            parts.append(node.text)
        elif node.tag.endswith("}p"):
            parts.append("\n")
    return normalize_homework_text("".join(parts))


def _load_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml_bytes = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(xml_bytes)
    strings = []
    for item in root.iter():
        if item.tag.endswith("}si"):
            strings.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
    return strings


def _extract_xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return trim_outer_blank_lines("".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t")))

    value = ""
    for node in cell.iter():
        if node.tag.endswith("}v") and node.text:
            value = node.text.strip()
            break
    if not value:
        return ""
    if cell_type == "s":
        try:
            return trim_outer_blank_lines(shared_strings[int(value)])
        except (ValueError, IndexError):
            return ""
    return trim_outer_blank_lines(value)


def _extract_text_from_xlsx(raw_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        shared_strings = _load_xlsx_shared_strings(archive)
        worksheet_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if not worksheet_names:
            raise HomeworkImportParseError("XLSX 文件中没有 worksheet，无法解析。")
        sheet_texts = []
        for worksheet_name in worksheet_names:
            root = ElementTree.fromstring(archive.read(worksheet_name))
            row_texts = []
            for row in root.iter():
                if not row.tag.endswith("}row"):
                    continue
                cell_texts = []
                for cell in row:
                    if not cell.tag.endswith("}c"):
                        continue
                    cell_text = _extract_xlsx_cell_text(cell, shared_strings)
                    if cell_text:
                        cell_texts.append(cell_text)
                if cell_texts:
                    row_texts.append("\t".join(cell_texts))
            if row_texts:
                sheet_texts.append("\n".join(row_texts))
    return normalize_homework_text("\n\n".join(sheet_texts))


def _build_pdf_reader(file_path: str):
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError as exc:
            raise HomeworkImportParseError("当前环境未安装 PDF 文本解析依赖，暂时无法解析 PDF。") from exc
    return PdfReader(file_path)


def _extract_text_from_pdf(file_path: str) -> str:
    reader = _build_pdf_reader(file_path)
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return normalize_homework_text("\n".join(parts))


def extract_local_source_text(import_job: HomeworkImportJob) -> str:
    file_path = import_job.source_file.path
    source_type = import_job.source_type
    if source_type == HomeworkImportJob.SOURCE_TYPE_HTML:
        return _extract_text_from_html(Path(file_path).read_bytes())
    if source_type == HomeworkImportJob.SOURCE_TYPE_TEXT:
        return _extract_text_from_txt(Path(file_path).read_bytes())
    if source_type == HomeworkImportJob.SOURCE_TYPE_DOCX:
        return _extract_text_from_docx(Path(file_path).read_bytes())
    if source_type == HomeworkImportJob.SOURCE_TYPE_XLSX:
        return _extract_text_from_xlsx(Path(file_path).read_bytes())
    if source_type == HomeworkImportJob.SOURCE_TYPE_PDF:
        return _extract_text_from_pdf(file_path)
    raise HomeworkImportParseError("当前文件类型不支持本地文本抽取。")


def extract_source_text(import_job: HomeworkImportJob) -> str:
    return extract_local_source_text(import_job)


def _try_parse_llm_json_payload(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _build_http_error_preview(value: object) -> str:
    preview = normalize_homework_text(str(value or ""))
    if not preview:
        return "空响应"
    if len(preview) <= HTTP_ERROR_PREVIEW_LIMIT:
        return preview
    return f"{preview[:HTTP_ERROR_PREVIEW_LIMIT]}..."


def get_import_error_code(exc: BaseException) -> str:
    return str(getattr(exc, "error_code", "") or "")


def get_import_failure_type(exc: BaseException) -> str:
    failure_type = str(getattr(exc, "failure_type", "") or "").strip()
    if failure_type:
        return failure_type
    return normalize_candidate_text(str(exc)) or "未知错误"


def is_retryable_import_error(exc: BaseException) -> bool:
    return bool(getattr(exc, "retryable", False))


def build_visual_user_message(error_code: str, *, final_failure_type: str = "") -> str:
    if error_code == "pdf_render_unavailable":
        return "当前环境缺少 PDF 光栅化依赖，未生成候选题。"
    if error_code == "pdf_render_failed":
        return "PDF 页面渲染失败，未生成候选题，请稍后重试。"
    if error_code in {"connect_timeout", "read_timeout", "timeout", "vision_all_timeout", "volc_timeout"}:
        return "视觉识别超时，未生成候选题，请稍后重试。"
    if error_code == "ssl_handshake_failed":
        return "视觉识别 SSL 握手失败，未生成候选题，请检查网络或证书配置后重试。"
    if error_code == "http_status":
        return "视觉识别服务返回异常状态，未生成候选题，请稍后重试。"
    if error_code == "empty_response":
        return "视觉识别返回空响应，未生成候选题，请稍后重试。"
    if error_code == "connection_error":
        return "视觉识别连接失败，未生成候选题，请稍后重试。"
    if error_code == "all_segments_failed" and final_failure_type:
        return f"视觉识别失败（{final_failure_type}），未生成候选题，请稍后重试。"
    if final_failure_type:
        return f"视觉识别失败（{final_failure_type}），未生成候选题，请稍后重试。"
    return "视觉识别失败，未生成候选题，请稍后重试。"


def extract_import_job_user_facing_message(import_job: HomeworkImportJob, *, default: str = "") -> str:
    parse_notes = str(import_job.parse_notes or "")
    for raw_line in parse_notes.splitlines():
        line = raw_line.strip()
        if line.startswith("页面提示："):
            return line.split("：", 1)[1].strip()
    return default


def get_homework_ssl_verify() -> bool | str:
    if not bool(getattr(settings, "HOMEWORK_SSL_VERIFY", True)):
        return False
    configured_bundle = normalize_candidate_text(getattr(settings, "HOMEWORK_SSL_CA_BUNDLE", ""))
    return configured_bundle or certifi.where()


def get_homework_llm_model_name() -> str:
    return normalize_candidate_text(getattr(settings, "HOMEWORK_LLM_MODEL", ""))


def get_homework_llm_model_label() -> str:
    return get_homework_llm_model_name() or "qwen"


def get_homework_llm_chat_completions_url() -> str:
    configured_url = normalize_candidate_text(getattr(settings, "HOMEWORK_LLM_API_URL", ""))
    if not configured_url:
        return ""
    normalized_url = configured_url.rstrip("/")
    if normalized_url.endswith("/chat/completions"):
        return normalized_url
    return f"{normalized_url}/chat/completions"


def call_external_json_api(
    *,
    provider_label: str,
    url: str,
    headers: dict[str, str] | None,
    payload: dict[str, Any],
    timeout_seconds: int,
    connect_timeout_seconds: float | None = None,
    read_timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], str]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": normalize_candidate_text(getattr(settings, "HOMEWORK_REQUESTS_USER_AGENT", "")) or "codemaster-homework-import/1.0",
    }
    if headers:
        request_headers.update(headers)
    verify = get_homework_ssl_verify()
    timeout: float | tuple[float, float]
    if connect_timeout_seconds is not None or read_timeout_seconds is not None:
        timeout = (
            float(connect_timeout_seconds if connect_timeout_seconds is not None else timeout_seconds),
            float(read_timeout_seconds if read_timeout_seconds is not None else timeout_seconds),
        )
    else:
        timeout = float(timeout_seconds)

    try:
        with requests.Session() as session:
            response = session.post(
                url,
                headers=request_headers,
                json=payload,
                timeout=timeout,
                verify=verify,
            )
    except requests.exceptions.SSLError as exc:
        message = str(exc)
        certificate_failed = "CERTIFICATE_VERIFY_FAILED" in message
        raise HomeworkImportParseError(
            f"SSL 握手失败：{exc}",
            error_code="ssl_handshake_failed",
            failure_type="SSL 握手失败",
            retryable=not certificate_failed,
        ) from exc
    except requests.exceptions.ConnectTimeout as exc:
        raise HomeworkImportParseError(
            f"connect timeout：{exc}",
            error_code="connect_timeout",
            failure_type="connect timeout",
            retryable=True,
        ) from exc
    except requests.exceptions.ReadTimeout as exc:
        raise HomeworkImportParseError(
            f"read timeout：{exc}",
            error_code="read_timeout",
            failure_type="read timeout",
            retryable=True,
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise HomeworkImportParseError(
            f"timeout：{exc}",
            error_code="timeout",
            failure_type="timeout",
            retryable=True,
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise HomeworkImportParseError(
            f"连接失败：{exc}",
            error_code="connection_error",
            failure_type="连接失败",
            retryable=True,
        ) from exc
    except OSError as exc:
        raise HomeworkImportParseError(
            f"请求连接错误：{exc}",
            error_code="connection_error",
            failure_type="连接失败",
            retryable=True,
        ) from exc

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise HomeworkImportParseError(
            f"HTTP 状态异常（{response.status_code}）：{_build_http_error_preview(response.text)}",
            error_code="http_status",
            failure_type=f"HTTP {response.status_code}",
            retryable=response.status_code in {408, 425, 429, 500, 502, 503, 504},
            http_status=response.status_code,
        ) from exc

    response_text = str(getattr(response, "text", "") or "")
    if not response_text.strip():
        raise HomeworkImportParseError(
            "空响应：服务未返回内容。",
            error_code="empty_response",
            failure_type="空响应",
            retryable=True,
        )

    try:
        raw_response = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise HomeworkImportParseError(
            f"JSON 解析失败：{exc}; 响应片段：{_build_http_error_preview(response_text)}",
            error_code="invalid_json",
            failure_type="JSON 解析失败",
            retryable=False,
        ) from exc

    verify_label = "关闭" if verify is False else "开启"
    return raw_response, f"{provider_label} 请求成功（HTTP {response.status_code}，SSL校验{verify_label}）"


def _extract_response_text_from_responses_payload(payload: dict[str, Any]) -> str:
    output_text = normalize_homework_text(payload.get("output_text", ""))
    if output_text:
        return output_text

    parts: list[str] = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for content in item.get("content", []) or []:
                if not isinstance(content, dict):
                    continue
                content_type = content.get("type")
                if content_type in {"output_text", "text"} and content.get("text"):
                    parts.append(str(content.get("text")))
        elif item.get("type") in {"output_text", "text"} and item.get("text"):
            parts.append(str(item.get("text")))
    return normalize_homework_text("\n".join(parts))


def _guess_mime_type(filename: str, *, default: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or default


def _build_data_url(data: bytes, *, mime_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _format_trace_datetime(value: Any) -> str:
    if not value:
        return ""
    try:
        localized = timezone.localtime(value)
    except Exception:
        return normalize_candidate_text(value)
    return localized.strftime("%Y-%m-%d %H:%M")


def find_recent_duplicate_import_job(import_job: HomeworkImportJob) -> HomeworkImportJob | None:
    if not import_job.source_sha256:
        return None
    duplicate_queryset = HomeworkImportJob.objects.filter(
        is_active=True,
        source_sha256=import_job.source_sha256,
    )
    if import_job.assignment_id:
        duplicate_queryset = duplicate_queryset.filter(assignment_id=import_job.assignment_id)
    else:
        duplicate_queryset = duplicate_queryset.filter(
            assignment__isnull=True,
            teacher_id=import_job.teacher_id,
            content_id=import_job.content_id,
        )
    return (
        duplicate_queryset
        .exclude(id=import_job.id)
        .order_by("-created_at", "-id")
        .first()
    )


def _build_volc_vision_input_items(
    *,
    image_url: str,
    local_text: str,
    image_label: str,
) -> list[dict[str, str]]:
    prompt = (
        "请阅读图片中的作业内容，尽量按原顺序提取题干、选项、答案、解析等文字。"
        "如果能识别出题号、A/B/C/D 选项和答案，请完整保留。"
        "如果无法确定答案，也请把可见文字原样整理出来，不要编造。"
    )
    if image_label:
        prompt = f"{prompt} 当前处理片段：{image_label}。"
    content_items: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
    if local_text:
        preview_limit = max(int(getattr(settings, "HOMEWORK_VISION_LOCAL_TEXT_PREVIEW_LIMIT", 1200)), 200)
        local_text_preview = normalize_homework_text(local_text)
        if len(local_text_preview) > preview_limit:
            local_text_preview = f"{local_text_preview[:preview_limit]}..."
        content_items.append(
            {
                "type": "input_text",
                "text": f"这是系统已抽到的辅助文本，可用于校对，但优先以图像内容为准：\n{local_text_preview}",
            }
        )
    content_items.append({"type": "input_image", "image_url": image_url})
    return content_items


def _call_volc_vision_once(
    *,
    api_key: str,
    api_url: str,
    model: str,
    connect_timeout_seconds: float,
    read_timeout_seconds: float,
    vision_input: HomeworkVisionInput,
    local_text: str,
) -> tuple[str, str]:
    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": _build_volc_vision_input_items(
                    image_url=_build_data_url(vision_input.image_bytes, mime_type=vision_input.mime_type),
                    local_text=local_text,
                    image_label=vision_input.label,
                ),
            }
        ],
    }
    raw_response, request_note = call_external_json_api(
        provider_label="火山视觉",
        url=api_url,
        headers={"Authorization": f"Bearer {api_key}"},
        payload=payload,
        timeout_seconds=int(max(read_timeout_seconds, connect_timeout_seconds)),
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
    )

    extracted_text = _extract_response_text_from_responses_payload(raw_response)
    if not extracted_text:
        raise HomeworkImportParseError(
            f"{vision_input.label or '当前图像'}返回为空，未提取到可用文本。",
            error_code="empty_response",
            failure_type="空响应",
            retryable=True,
        )
    return extracted_text, request_note


def _call_volc_vision_with_retry(
    *,
    api_key: str,
    api_url: str,
    model: str,
    connect_timeout_seconds: float,
    read_timeout_seconds: float,
    max_retries: int,
    retry_backoff_seconds: float,
    vision_input: HomeworkVisionInput,
    local_text: str,
    trace: HomeworkImportTrace | None,
) -> tuple[str, str]:
    last_exc: HomeworkImportParseError | None = None
    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        try:
            extracted_text, request_note = _call_volc_vision_once(
                api_key=api_key,
                api_url=api_url,
                model=model,
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                vision_input=vision_input,
                local_text=local_text,
            )
            if trace is not None:
                trace.vision_request_results.append(f"{vision_input.label}: 第{attempt}次请求成功")
            return extracted_text, f"{request_note}；{vision_input.label} 第{attempt}次请求成功"
        except HomeworkImportParseError as exc:
            last_exc = exc
            failure_type = get_import_failure_type(exc)
            if trace is not None:
                trace.vision_request_results.append(
                    f"{vision_input.label}: 第{attempt}次请求失败（{failure_type}）"
                )
            if attempt < total_attempts and is_retryable_import_error(exc):
                time.sleep(max(retry_backoff_seconds, 0.0) * attempt)
                continue
            break

    if last_exc is None:
        raise HomeworkImportParseError("火山视觉请求失败：未知错误。", error_code="unknown_error", failure_type="未知错误")
    raise last_exc


def _load_pillow_image(image_bytes: bytes):
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise HomeworkImportParseError("当前环境未安装 Pillow，无法处理图片切片。") from exc
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception as exc:
        raise HomeworkImportParseError(f"图片文件无法读取：{exc}") from exc
    return image


def _render_image_to_png_bytes(image: Any) -> bytes:
    render_image = image
    if getattr(render_image, "mode", "") not in {"1", "L", "P", "RGB", "RGBA"}:
        render_image = render_image.convert("RGB")
    buffer = io.BytesIO()
    render_image.save(buffer, format="PNG")
    return buffer.getvalue()


def _slice_image_for_vision(
    image_bytes: bytes,
    *,
    mime_type: str,
    label_prefix: str,
    trace: HomeworkImportTrace | None,
) -> list[HomeworkVisionInput]:
    image = _load_pillow_image(image_bytes)
    width, height = image.size
    threshold = max(int(getattr(settings, "HOMEWORK_IMAGE_SLICE_HEIGHT_THRESHOLD", 2400)), 1)
    overlap = max(int(getattr(settings, "HOMEWORK_IMAGE_SLICE_OVERLAP", 120)), 0)
    if height <= threshold:
        return [
            HomeworkVisionInput(
                label=label_prefix or "整图",
                image_bytes=image_bytes,
                mime_type=mime_type,
            )
        ]

    if trace is not None:
        trace.image_sliced = True

    step = max(threshold - overlap, 1)
    slices: list[HomeworkVisionInput] = []
    slice_index = 0
    top = 0
    while top < height:
        bottom = min(top + threshold, height)
        cropped = image.crop((0, top, width, bottom))
        slice_index += 1
        label = f"第{slice_index}片"
        if label_prefix:
            label = f"{label_prefix}-{label}"
        slices.append(
            HomeworkVisionInput(
                label=label,
                image_bytes=_render_image_to_png_bytes(cropped),
                mime_type="image/png",
            )
        )
        if bottom >= height:
            break
        top += step

    if trace is not None:
        trace.image_slice_count += len(slices)
    return slices


def _rasterize_pdf_pages_to_images(
    file_path: str,
    *,
    trace: HomeworkImportTrace | None,
) -> list[HomeworkVisionInput]:
    if trace is not None:
        trace.pdf_raster_attempted = True
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError as exc:
        if trace is not None:
            trace.vision_final_failure_code = "pdf_render_unavailable"
            trace.vision_final_failure_type = "pdf_render_unavailable"
            trace.pdf_render_results.append("PDF 光栅化依赖：缺失（pypdfium2）")
        raise HomeworkImportParseError(
            "当前环境未安装 pypdfium2，PDF 页面光栅化不可用。",
            error_code="pdf_render_unavailable",
            failure_type="pdf_render_unavailable",
            user_message="当前环境缺少 PDF 光栅化依赖，未生成候选题。",
        ) from exc

    max_pages = max(int(getattr(settings, "HOMEWORK_PDF_RASTER_MAX_PAGES", 6)), 1)
    scale = max(float(getattr(settings, "HOMEWORK_PDF_RASTER_SCALE", 2.0)), 1.0)
    try:
        document = pdfium.PdfDocument(file_path)
    except Exception as exc:
        if trace is not None:
            trace.vision_final_failure_code = "pdf_render_failed"
            trace.vision_final_failure_type = "pdf_render_failed"
            trace.pdf_render_results.append(f"PDF 文档打开失败（pdf_render_failed）：{build_trace_preview(str(exc), limit=120)}")
        raise HomeworkImportParseError(
            f"PDF 文档打开失败：{exc}",
            error_code="pdf_render_failed",
            failure_type="pdf_render_failed",
            user_message="PDF 页面渲染失败，未生成候选题，请稍后重试。",
        ) from exc
    page_images: list[HomeworkVisionInput] = []
    total_pages = len(document)
    processed_pages = min(total_pages, max_pages)

    if trace is not None:
        trace.pdf_rasterized = True
        trace.pdf_total_pages = total_pages
        trace.pdf_pages_processed = processed_pages
        trace.pdf_page_limit_hit = total_pages > processed_pages

    if total_pages <= 0:
        close_document = getattr(document, "close", None)
        if callable(close_document):
            close_document()
        if trace is not None:
            trace.vision_final_failure_code = "pdf_render_failed"
            trace.vision_final_failure_type = "pdf_render_failed"
            trace.pdf_render_results.append("PDF 页面总数为 0，无法渲染（pdf_render_failed）")
        raise HomeworkImportParseError(
            "PDF 文件没有可处理的页面。",
            error_code="pdf_render_failed",
            failure_type="pdf_render_failed",
            user_message="PDF 页面渲染失败，未生成候选题，请稍后重试。",
        )

    try:
        for page_index in range(processed_pages):
            page = document[page_index]
            bitmap = None
            try:
                bitmap = page.render(scale=scale)
                pil_image = bitmap.to_pil()
                if trace is not None:
                    trace.pdf_render_results.append(f"第{page_index + 1}页渲染成功")
                page_images.append(
                    HomeworkVisionInput(
                        label=f"第{page_index + 1}页",
                        image_bytes=_render_image_to_png_bytes(pil_image),
                        mime_type="image/png",
                    )
                )
            except Exception as exc:
                if trace is not None:
                    trace.vision_final_failure_code = "pdf_render_failed"
                    trace.vision_final_failure_type = "pdf_render_failed"
                    trace.pdf_render_results.append(
                        f"第{page_index + 1}页渲染失败（pdf_render_failed）：{build_trace_preview(str(exc), limit=120)}"
                    )
                raise HomeworkImportParseError(
                    f"PDF 第{page_index + 1}页光栅化失败：{exc}",
                    error_code="pdf_render_failed",
                    failure_type="pdf_render_failed",
                    user_message="PDF 页面渲染失败，未生成候选题，请稍后重试。",
                ) from exc
            finally:
                if bitmap is not None:
                    close_bitmap = getattr(bitmap, "close", None)
                    if callable(close_bitmap):
                        close_bitmap()
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    close_page()
    finally:
        close_document = getattr(document, "close", None)
        if callable(close_document):
            close_document()

    return page_images


def _build_vision_inputs(
    import_job: HomeworkImportJob,
    *,
    trace: HomeworkImportTrace | None,
) -> list[HomeworkVisionInput]:
    file_path = import_job.source_file.path
    if import_job.source_type == HomeworkImportJob.SOURCE_TYPE_IMAGE:
        image_bytes = Path(file_path).read_bytes()
        mime_type = _guess_mime_type(import_job.source_filename, default="image/png")
        return _slice_image_for_vision(
            image_bytes,
            mime_type=mime_type,
            label_prefix="",
            trace=trace,
        )

    if import_job.source_type == HomeworkImportJob.SOURCE_TYPE_PDF:
        page_images = _rasterize_pdf_pages_to_images(file_path, trace=trace)
        if not page_images:
            if trace is not None:
                trace.vision_final_failure_code = "pdf_render_failed"
                trace.vision_final_failure_type = "pdf_render_failed"
                trace.pdf_render_results.append("PDF 光栅化后未生成页面图像（pdf_render_failed）")
            raise HomeworkImportParseError(
                "PDF 光栅化后没有得到可识别的页面图像。",
                error_code="pdf_render_failed",
                failure_type="pdf_render_failed",
                user_message="PDF 页面渲染失败，未生成候选题，请稍后重试。",
            )
        vision_inputs: list[HomeworkVisionInput] = []
        for page_image in page_images:
            page_segments = _slice_image_for_vision(
                page_image.image_bytes,
                mime_type=page_image.mime_type,
                label_prefix=page_image.label,
                trace=trace,
            )
            if trace is not None:
                trace.pdf_page_slice_results.append(
                    f"{page_image.label}继续切片：{'是' if len(page_segments) > 1 else '否'}，{len(page_segments)} 片"
                )
            vision_inputs.extend(page_segments)
        return vision_inputs

    raise HomeworkImportParseError("当前文件类型不支持火山视觉路由。")


def extract_text_with_volc_vision(
    import_job: HomeworkImportJob,
    *,
    local_text: str = "",
    trace: HomeworkImportTrace | None = None,
) -> tuple[str, str]:
    provider = normalize_candidate_text(getattr(settings, "HOMEWORK_PARSE_PROVIDER_OCR", "")).lower()
    if provider != "volc_vision":
        raise HomeworkImportParseError(
            f"视觉 provider 配置为 {provider or '未设置'}，当前仅支持 volc_vision。"
        )
    api_key = normalize_candidate_text(getattr(settings, "ARK_API_KEY", ""))
    if not api_key:
        raise HomeworkImportParseError(
            "火山视觉 provider 未配置 ARK_API_KEY。",
            error_code="missing_ark_api_key",
            failure_type="配置缺失",
            user_message="ARK_API_KEY 未配置，无法调用火山视觉识别。",
        )

    model = normalize_candidate_text(getattr(settings, "VOLC_VISION_MODEL", ""))
    api_url = normalize_candidate_text(getattr(settings, "VOLC_VISION_API_URL", ""))
    if not model or not api_url:
        raise HomeworkImportParseError("火山视觉 provider 缺少 VOLC_VISION_MODEL 或 VOLC_VISION_API_URL。")
    legacy_timeout_seconds = float(getattr(settings, "VOLC_VISION_TIMEOUT_SECONDS", 40))
    connect_timeout_seconds = float(
        getattr(
            settings,
            "VOLC_VISION_CONNECT_TIMEOUT_SECONDS",
            min(legacy_timeout_seconds, 10.0),
        )
    )
    read_timeout_seconds = float(
        getattr(
            settings,
            "VOLC_VISION_READ_TIMEOUT_SECONDS",
            max(legacy_timeout_seconds, 60.0),
        )
    )
    max_retries = max(int(getattr(settings, "VOLC_VISION_MAX_RETRIES", 2)), 0)
    retry_backoff_seconds = max(float(getattr(settings, "VOLC_VISION_RETRY_BACKOFF_SECONDS", 0.2)), 0.0)
    vision_inputs = _build_vision_inputs(import_job, trace=trace)
    if not vision_inputs:
        raise HomeworkImportParseError("当前文件没有可发送给火山视觉的图像片段。")

    extracted_texts: list[str] = []
    failure_messages: list[str] = []
    failure_codes: list[str] = []
    failure_types: list[str] = []
    success_count = 0
    timeout_error_codes = {"connect_timeout", "read_timeout", "timeout"}
    for vision_input in vision_inputs:
        try:
            extracted_text, _request_note = _call_volc_vision_with_retry(
                api_key=api_key,
                api_url=api_url,
                model=model,
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
                vision_input=vision_input,
                local_text=local_text,
                trace=trace,
            )
            extracted_texts.append(extracted_text)
            success_count += 1
            if trace is not None and trace.image_sliced:
                trace.image_slice_results.append(f"{vision_input.label}成功")
        except HomeworkImportParseError as exc:
            failure_type = get_import_failure_type(exc)
            failure_code = get_import_error_code(exc)
            failure_codes.append(failure_code)
            failure_types.append(failure_type)
            failure_messages.append(f"{vision_input.label}失败（{failure_type}）")
            if trace is not None and trace.image_sliced:
                trace.image_slice_results.append(f"{vision_input.label}失败（{failure_type}）")

    if not extracted_texts:
        unique_failure_types = list(dict.fromkeys(item for item in failure_types if item))
        all_timeout_abort = bool(failure_codes) and all(code in timeout_error_codes for code in failure_codes)
        if all_timeout_abort:
            final_failure_code = "volc_timeout"
            final_failure_type = "全部片段超时"
        else:
            final_failure_code = "all_segments_failed"
            final_failure_type = "、".join(unique_failure_types) or "未知错误"
        user_message = build_visual_user_message(final_failure_code, final_failure_type=final_failure_type)
        if trace is not None:
            trace.vision_final_failure_code = final_failure_code
            trace.vision_final_failure_type = final_failure_type
            trace.vision_all_timeout_abort = all_timeout_abort
            trace.user_facing_message = user_message
        preview = "；".join(failure_messages[:3]) or final_failure_type
        raise HomeworkImportParseError(
            f"火山视觉所有图像片段均识别失败：{preview}",
            error_code=final_failure_code,
            failure_type=final_failure_type,
            retryable=all_timeout_abort,
            user_message=user_message,
        )

    extracted_text = normalize_homework_text("\n\n".join(text for text in extracted_texts if text))
    note = f"火山视觉逐图识别完成，成功 {success_count}/{len(vision_inputs)} 个视觉片段。"
    if failure_messages:
        unique_failure_types = list(dict.fromkeys(item for item in failure_types if item))
        if trace is not None:
            trace.vision_final_failure_code = "partial_segment_failure"
            trace.vision_final_failure_type = f"部分片段失败：{'、'.join(unique_failure_types)}"
        note = f"{note} 失败片段：{'；'.join(failure_messages[:3])}"
    return extracted_text, note


def _is_visual_block_boundary(line: str) -> bool:
    return bool(VISION_BLOCK_START_RE.match(normalize_candidate_text(line)))


def _merge_short_vision_blocks(blocks: list[str], *, minimum_chars: int) -> list[str]:
    if len(blocks) <= 1:
        return blocks

    merged_blocks: list[str] = []
    for block in blocks:
        if merged_blocks and len(block) < minimum_chars:
            merged_blocks[-1] = normalize_homework_text(f"{merged_blocks[-1]}\n{block}")
            continue
        merged_blocks.append(block)

    if len(merged_blocks) >= 2 and len(merged_blocks[0]) < minimum_chars:
        merged_blocks[1] = normalize_homework_text(f"{merged_blocks[0]}\n{merged_blocks[1]}")
        merged_blocks = merged_blocks[1:]
    return merged_blocks


def split_vision_ocr_text_into_blocks(source_text: str) -> list[str]:
    normalized_text = normalize_homework_text(source_text)
    if not normalized_text:
        return []

    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    if not lines:
        return []

    raw_blocks: list[str] = []
    current_lines: list[str] = []
    for line in lines:
        if _is_visual_block_boundary(line) and current_lines:
            raw_blocks.append(normalize_homework_text("\n".join(current_lines)))
            current_lines = [line]
            continue
        current_lines.append(line)
    if current_lines:
        raw_blocks.append(normalize_homework_text("\n".join(current_lines)))

    raw_blocks = [block for block in raw_blocks if block]
    if len(raw_blocks) <= 1:
        return raw_blocks or [normalized_text]

    minimum_chars = max(int(getattr(settings, "HOMEWORK_VISION_BLOCK_MIN_CHARS", 40)), 1)
    merged_blocks = _merge_short_vision_blocks(raw_blocks, minimum_chars=minimum_chars)
    return [block for block in merged_blocks if block]


def split_qwen_text_into_blocks(source_text: str, *, max_chars: int | None = None) -> list[str]:
    normalized_text = normalize_homework_text(source_text)
    if not normalized_text:
        return []

    limit = max(int(max_chars or getattr(settings, "HOMEWORK_LLM_TEXT_BLOCK_MAX_CHARS", 500)), 500)
    if len(normalized_text) <= limit:
        return [normalized_text]

    preserved_lines = normalize_preserved_text(source_text).splitlines()
    raw_blocks: list[str] = []
    current_lines: list[str] = []
    for raw_line in preserved_lines:
        line = raw_line.rstrip()
        if _is_visual_block_boundary(line) and any(item.strip() for item in current_lines):
            raw_blocks.append(normalize_homework_text("\n".join(current_lines)))
            current_lines = [line]
            continue
        current_lines.append(line)
    if current_lines:
        raw_blocks.append(normalize_homework_text("\n".join(current_lines)))
    question_blocks = [block for block in raw_blocks if block]

    if len(question_blocks) <= 1:
        lines = normalized_text.splitlines()
        chunks: list[str] = []
        current_lines: list[str] = []
        current_length = 0
        for line in lines:
            projected_length = current_length + len(line) + 1
            if current_lines and projected_length > limit:
                chunks.append(normalize_homework_text("\n".join(current_lines)))
                current_lines = [line]
                current_length = len(line)
                continue
            current_lines.append(line)
            current_length = projected_length
        if current_lines:
            chunks.append(normalize_homework_text("\n".join(current_lines)))
        return [chunk for chunk in chunks if chunk]

    chunks: list[str] = []
    current_blocks: list[str] = []
    current_length = 0
    for block in question_blocks:
        projected_length = current_length + len(block) + 2
        if current_blocks and projected_length > limit:
            chunks.append(normalize_homework_text("\n\n".join(current_blocks)))
            current_blocks = [block]
            current_length = len(block)
            continue
        current_blocks.append(block)
        current_length = projected_length
    if current_blocks:
        chunks.append(normalize_homework_text("\n\n".join(current_blocks)))
    return [chunk for chunk in chunks if chunk]


def parse_candidates_with_qwen(
    source_text: str,
    *,
    source_type: str,
    source_origin: str,
    strict_visual_block: bool = False,
    block_label: str = "",
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[dict], str]:
    provider = normalize_candidate_text(getattr(settings, "HOMEWORK_PARSE_PROVIDER_TEXT", "")).lower()
    if provider != "qwen":
        raise HomeworkImportParseError(
            f"文本结构化 provider 配置为 {provider or '未设置'}，当前仅支持 qwen。"
        )
    model = get_homework_llm_model_name()
    api_url = get_homework_llm_chat_completions_url()
    if not model or not api_url:
        model_label = model or "qwen"
        raise HomeworkImportParseError(
            f"{model_label} provider 缺少 HOMEWORK_LLM_MODEL 或 HOMEWORK_LLM_API_URL。",
            error_code="missing_qwen_config",
            failure_type="配置缺失",
            user_message=f"{model_label} 模型或接口地址配置缺失，无法结构化题目。",
        )

    api_key = normalize_candidate_text(getattr(settings, "DASHSCOPE_API_KEY", ""))
    if not api_key:
        raise HomeworkImportParseError(
            f"{model} provider 未配置 DASHSCOPE_API_KEY。",
            error_code="missing_dashscope_api_key",
            failure_type="配置缺失",
            user_message=f"DASHSCOPE_API_KEY 未配置，无法调用 {model} 结构化题目。",
        )

    if strict_visual_block:
        system_prompt = (
            "你是教学系统的题目结构化助手。"
            "当前输入是视觉 OCR 后切分出的单个题块。"
            "只识别单选题，输出 JSON 对象，格式必须为 "
            '{"questions":[{"stem":"","options":{"A":"","B":"","C":"","D":""},"correct_answer":"A","analysis":"","notes":"","confidence":0.0}],"notes":""}。'
            "如果该题块不是完整单选题，或者只是讲义标题、知识点总结、题目说明、例题讲解，请返回空 questions。"
            "只有在存在明确题干且至少两个选项时，才允许输出候选题。"
            "答案标记可能写成：答案、参考答案、正确答案、标准答案、Answer、Correct Answer。"
            "解析标记可能写成：解析、答案解析、参考解析、讲解、Explanation。"
            "不要把答案或解析标签文本并入题干。"
            "如果答案写的是选项内容而不是字母，请根据 A/B/C/D 选项内容映射成对应字母。"
            "如果同一题出现多个答案标记，优先取最后一个最明确的答案标记。"
            "不要输出多余解释。"
        )
        user_prompt = (
            f"源文件类型：{source_type}\n"
            f"文本来源：{source_origin}\n"
            f"题块标识：{block_label or '未命名 block'}\n"
            "请只处理这个题块，不要脑补跨块内容。\n"
            "如果块中只是说明文或不完整题目，就输出空 questions。\n"
            "遇到“参考答案：B”“【参考答案】A”“Correct Answer: C”时，要把对应字母写入 correct_answer。\n"
            "遇到“参考答案：循环结构”这类写法时，如果某个选项内容是“循环结构”，请输出对应选项字母。\n"
            f"{source_text}"
        )
    else:
        system_prompt = (
            "你是教学系统的题目结构化助手。"
            "只识别单选题，输出 JSON 对象，格式必须为 "
            '{"questions":[{"stem":"","options":{"A":"","B":"","C":"","D":""},"correct_answer":"A","analysis":"","notes":"","confidence":0.0}],"notes":""}。'
            "如果文本中不是单选题，就不要输出该题。"
            "答案标记可能写成：答案、参考答案、正确答案、标准答案、Answer、Correct Answer。"
            "解析标记可能写成：解析、答案解析、参考解析、讲解、Explanation。"
            "不要把答案或解析标签文本并入题干。"
            "如果答案写的是选项内容而不是字母，请根据 A/B/C/D 选项内容映射成对应字母。"
            "如果同一题出现多个答案标记，优先取最后一个最明确的答案标记。"
            "不要输出多余解释。"
        )
        user_prompt = (
            f"源文件类型：{source_type}\n"
            f"文本来源：{source_origin}\n"
            "请从下面内容中提取单选题候选，仅保留题干完整、选项明确、答案可判定或可供老师补全的题。\n"
            "支持识别：参考答案、正确答案、标准答案、Answer、Correct Answer、答案解析、参考解析、讲解、Explanation。\n"
            "示例：\n"
            "参考答案：B -> correct_answer 应输出 B。\n"
            "参考答案：循环结构 -> 如果 B 选项是“循环结构”，correct_answer 应输出 B。\n"
            "参考答案：A。解析：这是循环遍历。 -> correct_answer=A，analysis=这是循环遍历。\n"
            f"{source_text}"
        )
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    max_retries = max(int(getattr(settings, "HOMEWORK_LLM_MAX_RETRIES", 2)), 0)
    retry_backoff_seconds = max(float(getattr(settings, "HOMEWORK_LLM_RETRY_BACKOFF_SECONDS", 1.0)), 0.0)
    raw_response: dict[str, Any] | None = None
    request_note = ""
    max_attempts = max_retries + 1
    for attempt_index in range(max_retries + 1):
        try:
            if progress_callback is not None:
                progress_callback(attempt_index + 1, max_attempts)
            raw_response, request_note = call_external_json_api(
                provider_label=model,
                url=api_url,
                headers={"Authorization": f"Bearer {api_key}"},
                payload=payload,
                timeout_seconds=int(getattr(settings, "HOMEWORK_LLM_TIMEOUT_SECONDS", 40)),
            )
            if attempt_index:
                request_note = f"{request_note}；第 {attempt_index + 1} 次尝试成功"
            break
        except HomeworkImportParseError as exc:
            if attempt_index >= max_retries or not is_retryable_import_error(exc):
                raise
            time.sleep(retry_backoff_seconds * (attempt_index + 1))
    if raw_response is None:
        raise HomeworkImportParseError(f"{model} 请求失败。")

    content = (
        raw_response.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    parsed_payload = _try_parse_llm_json_payload(content)
    if not parsed_payload:
        raise HomeworkImportParseError(f"{model} 返回内容无法解析为 JSON。")
    raw_questions = parsed_payload.get("questions")
    if not isinstance(raw_questions, list):
        raise HomeworkImportParseError(f"{model} 返回缺少 questions 数组。")
    candidates = [sanitize_candidate(item, index=index) for index, item in enumerate(raw_questions, start=1)]
    notes = normalize_candidate_text(parsed_payload.get("notes")) or f"{model} 已完成候选题结构化。"
    notes = f"{request_note}；{notes}"
    return candidates, notes


def parse_candidates_with_llm(source_text: str, *, source_type: str) -> tuple[list[dict], str]:
    return parse_candidates_with_qwen(source_text, source_type=source_type, source_origin="legacy")


def candidate_needs_answer_analysis_supplement(candidate: dict) -> bool:
    return (
        normalize_candidate_text(candidate.get("correct_answer")) not in {"A", "B", "C", "D"}
        or not normalize_candidate_text(candidate.get("analysis"))
    )


def supplement_candidate_answers_with_qwen(
    candidates: list[dict],
    *,
    source_text: str,
    source_type: str,
    source_origin: str,
    progress_callback: Callable[[str, str], None] | None = None,
) -> tuple[list[dict], str]:
    missing_candidates = [
        candidate
        for candidate in candidates
        if candidate_needs_answer_analysis_supplement(candidate)
    ]
    if not missing_candidates:
        return candidates, ""

    provider = normalize_candidate_text(getattr(settings, "HOMEWORK_PARSE_PROVIDER_TEXT", "")).lower()
    if provider != "qwen":
        raise HomeworkImportParseError(
            f"答案解析补全 provider 配置为 {provider or '未设置'}，当前仅支持 qwen。"
        )
    model = get_homework_llm_model_name()
    api_url = get_homework_llm_chat_completions_url()
    api_key = normalize_candidate_text(getattr(settings, "DASHSCOPE_API_KEY", ""))
    if not model or not api_url or not api_key:
        raise HomeworkImportParseError(f"{model or 'qwen'} 答案解析补全配置缺失。")

    compact_candidates = [
        {
            "index": candidate.get("index") or index,
            "stem": candidate.get("stem", ""),
            "options": candidate.get("options", {}),
            "known_correct_answer": candidate.get("correct_answer", ""),
            "known_analysis": candidate.get("analysis", ""),
        }
        for index, candidate in enumerate(missing_candidates, start=1)
    ]
    system_prompt = (
        "你是教学系统的单选题答案和解析补全助手。"
        "只根据题干、选项和源文件文本判断正确答案与解析。"
        "输出 JSON 对象，格式必须为 "
        '{"questions":[{"index":1,"correct_answer":"A","analysis":""}],"notes":""}。'
        "correct_answer 只能是 A/B/C/D。"
        "如果已有正确答案或解析，不要改写，只补缺失字段。"
        "如果无法可靠判断某题答案，请不要返回该题。"
    )
    user_prompt = (
        f"源文件类型：{source_type}\n"
        f"文本来源：{source_origin}\n"
        "需要补全的候选题 JSON：\n"
        f"{json.dumps(compact_candidates, ensure_ascii=False)}\n\n"
        "源文件文本节选：\n"
        f"{build_trace_preview(source_text, limit=5000)}"
    )
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    max_retries = max(int(getattr(settings, "HOMEWORK_LLM_MAX_RETRIES", 0)), 0)
    retry_backoff_seconds = max(float(getattr(settings, "HOMEWORK_LLM_RETRY_BACKOFF_SECONDS", 1.0)), 0.0)
    raw_response: dict[str, Any] | None = None
    request_note = ""
    for attempt_index in range(max_retries + 1):
        try:
            if progress_callback is not None:
                progress_callback(
                    "补全答案和解析",
                    (
                        f"正在调用 {model} 判断 {len(missing_candidates)} 道候选题的缺失答案/解析；"
                        f"第 {attempt_index + 1}/{max_retries + 1} 次请求，单次请求超时 "
                        f"{int(getattr(settings, 'HOMEWORK_LLM_TIMEOUT_SECONDS', 40))} 秒。"
                    ),
                )
            raw_response, request_note = call_external_json_api(
                provider_label=model,
                url=api_url,
                headers={"Authorization": f"Bearer {api_key}"},
                payload=payload,
                timeout_seconds=int(getattr(settings, "HOMEWORK_LLM_TIMEOUT_SECONDS", 40)),
            )
            break
        except HomeworkImportParseError as exc:
            if attempt_index >= max_retries or not is_retryable_import_error(exc):
                raise
            time.sleep(retry_backoff_seconds * (attempt_index + 1))
    if raw_response is None:
        raise HomeworkImportParseError(f"{model} 答案解析补全请求失败。")

    content = (
        raw_response.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    parsed_payload = _try_parse_llm_json_payload(content)
    if not parsed_payload or not isinstance(parsed_payload.get("questions"), list):
        raise HomeworkImportParseError(f"{model} 答案解析补全返回内容无法解析。")

    supplements_by_index: dict[int, dict] = {}
    for item in parsed_payload["questions"]:
        if not isinstance(item, dict):
            continue
        try:
            candidate_index = int(str(item.get("index", "")).strip())
        except (TypeError, ValueError):
            continue
        supplements_by_index[candidate_index] = item

    supplemented_count = 0
    supplemented_candidates = [dict(candidate) for candidate in candidates]
    for candidate in supplemented_candidates:
        candidate_index = int(candidate.get("index") or 0)
        supplement = supplements_by_index.get(candidate_index)
        if not supplement:
            continue
        options = candidate.get("options") if isinstance(candidate.get("options"), dict) else {}
        changed = False
        if normalize_candidate_text(candidate.get("correct_answer")) not in {"A", "B", "C", "D"}:
            resolved_answer = resolve_candidate_correct_answer(supplement.get("correct_answer"), options)
            if resolved_answer in {"A", "B", "C", "D"}:
                candidate["correct_answer"] = resolved_answer
                changed = True
        if not normalize_candidate_text(candidate.get("analysis")):
            analysis = normalize_candidate_text(supplement.get("analysis"))
            if analysis:
                candidate["analysis"] = analysis
                changed = True
        if changed:
            supplemented_count += 1

    notes = normalize_candidate_text(parsed_payload.get("notes")) or "答案和解析补全完成。"
    return supplemented_candidates, f"{request_note}；{model} 补全 {supplemented_count}/{len(missing_candidates)} 道候选题；{notes}"


def parse_text_blocks_with_qwen(
    source_text: str,
    *,
    source_type: str,
    source_origin: str,
    trace: HomeworkImportTrace,
    progress_callback: Callable[[str, str], None] | None = None,
) -> tuple[list[dict], str]:
    blocks = split_qwen_text_into_blocks(source_text)
    if not blocks:
        return [], ""

    if len(blocks) == 1:
        trace.qwen_attempted = True
        if progress_callback is not None:
            progress_callback(
                "调用 Qwen 结构化文本",
                f"文本未分块，正在调用 {get_homework_llm_model_label()}；单次最长可能等待 {int(getattr(settings, 'HOMEWORK_LLM_TIMEOUT_SECONDS', 40))} 秒。",
            )
        candidates, qwen_notes = parse_candidates_with_qwen(
            blocks[0],
            source_type=source_type,
            source_origin=source_origin,
            progress_callback=(
                (lambda attempt, max_attempts: progress_callback(
                    "调用 Qwen 结构化文本",
                    (
                        f"文本未分块，正在调用 {get_homework_llm_model_label()}；"
                        f"第 {attempt}/{max_attempts} 次请求，单次请求超时 "
                        f"{int(getattr(settings, 'HOMEWORK_LLM_TIMEOUT_SECONDS', 40))} 秒。"
                    ),
                ))
                if progress_callback is not None
                else None
            ),
        )
        trace.qwen_success = True
        return candidates, qwen_notes

    model_label = get_homework_llm_model_label()
    merged_candidates: list[dict] = []
    failure_summaries: list[str] = []
    trace.qwen_attempted = True
    trace.qwen_block_attempt_count = len(blocks)
    for index, block_text in enumerate(blocks, start=1):
        block_label = f"text_block{index}"
        block_length = len(block_text)
        try:
            if progress_callback is not None:
                timeout_seconds = int(getattr(settings, "HOMEWORK_LLM_TIMEOUT_SECONDS", 40))
                max_attempts = max(int(getattr(settings, "HOMEWORK_LLM_MAX_RETRIES", 2)), 0) + 1
                remaining_blocks = len(blocks) - index + 1
                progress_callback(
                    "调用 Qwen 分块结构化",
                    (
                        f"正在处理第 {index}/{len(blocks)} 块，长度 {block_length}；"
                        f"单次请求超时 {timeout_seconds} 秒，最多尝试 {max_attempts} 次，当前剩余 {remaining_blocks} 块。"
                    ),
                )
            block_candidates, _block_notes = parse_candidates_with_qwen(
                block_text,
                source_type=source_type,
                source_origin=f"{source_origin}_{block_label}",
                progress_callback=(
                    (lambda attempt, max_attempts, *, index=index, block_length=block_length: progress_callback(
                        "调用 Qwen 分块结构化",
                        (
                            f"正在处理第 {index}/{len(blocks)} 块，长度 {block_length}；"
                            f"第 {attempt}/{max_attempts} 次请求，单次请求超时 "
                            f"{int(getattr(settings, 'HOMEWORK_LLM_TIMEOUT_SECONDS', 40))} 秒，"
                            f"当前剩余 {len(blocks) - index + 1} 块。"
                        ),
                    ))
                    if progress_callback is not None
                    else None
                ),
            )
            merged_candidates.extend(block_candidates)
            trace.qwen_block_success_count += 1
            trace.qwen_block_results.append(
                f"{block_label}: 长度 {block_length}，{model_label} 成功，提取 {len(block_candidates)} 题"
            )
        except HomeworkImportParseError as exc:
            error_summary = build_trace_preview(str(exc), limit=120)
            failure_summaries.append(error_summary)
            trace.qwen_block_failure_count += 1
            trace.qwen_block_results.append(
                f"{block_label}: 长度 {block_length}，{model_label} 失败：{error_summary}"
            )

    if trace.qwen_block_failure_count:
        trace.qwen_error = (
            f"分块结构化失败：{trace.qwen_block_failure_count}/{trace.qwen_block_attempt_count} 个 block 调用失败；"
            f"{failure_summaries[0] if failure_summaries else '未知错误'}"
        )
        if merged_candidates:
            trace.qwen_success = True
            return (
                merged_candidates,
                (
                    f"{model_label} 分块结构化部分完成，成功 {trace.qwen_block_success_count}/{trace.qwen_block_attempt_count} 个 block，"
                    f"失败 {trace.qwen_block_failure_count} 个 block；已保留成功识别的 {len(merged_candidates)} 道候选题。"
                ),
            )
        raise HomeworkImportParseError(trace.qwen_error)

    trace.qwen_success = True
    return merged_candidates, f"{model_label} 分块结构化完成，成功 {trace.qwen_block_success_count}/{trace.qwen_block_attempt_count} 个 block。"


def parse_visual_blocks_with_qwen(
    source_text: str,
    *,
    source_type: str,
    trace: HomeworkImportTrace,
    progress_callback: Callable[[str, str], None] | None = None,
) -> list[dict]:
    blocks = split_vision_ocr_text_into_blocks(source_text)
    if not blocks:
        return []

    trace.vision_block_count = len(blocks)
    trace.vision_block_split_applied = len(blocks) >= 2
    trace.vision_text_preview = build_trace_preview(source_text)
    model_label = get_homework_llm_model_label()

    merged_candidates: list[dict] = []
    failure_summaries: list[str] = []
    for index, block_text in enumerate(blocks, start=1):
        block_label = f"block{index}"
        block_length = len(block_text)
        trace.qwen_block_attempt_count += 1
        try:
            if progress_callback is not None:
                timeout_seconds = int(getattr(settings, "HOMEWORK_LLM_TIMEOUT_SECONDS", 40))
                max_attempts = max(int(getattr(settings, "HOMEWORK_LLM_MAX_RETRIES", 2)), 0) + 1
                remaining_blocks = len(blocks) - index + 1
                progress_callback(
                    "调用 Qwen 结构化 OCR 文本",
                    (
                        f"正在处理 OCR 第 {index}/{len(blocks)} 块，长度 {block_length}；"
                        f"单次请求超时 {timeout_seconds} 秒，最多尝试 {max_attempts} 次，当前剩余 {remaining_blocks} 块。"
                    ),
                )
            block_candidates, _ = parse_candidates_with_qwen(
                block_text,
                source_type=source_type,
                source_origin=f"vision_block_{index}",
                strict_visual_block=True,
                block_label=f"{block_label}/{len(blocks)}",
                progress_callback=(
                    (lambda attempt, max_attempts, *, index=index, block_length=block_length: progress_callback(
                        "调用 Qwen 结构化 OCR 文本",
                        (
                            f"正在处理 OCR 第 {index}/{len(blocks)} 块，长度 {block_length}；"
                            f"第 {attempt}/{max_attempts} 次请求，单次请求超时 "
                            f"{int(getattr(settings, 'HOMEWORK_LLM_TIMEOUT_SECONDS', 40))} 秒，"
                            f"当前剩余 {len(blocks) - index + 1} 块。"
                        ),
                    ))
                    if progress_callback is not None
                    else None
                ),
            )
            merged_candidates.extend(block_candidates)
            trace.qwen_block_success_count += 1
            trace.vision_block_results.append(
                f"{block_label}: 长度 {block_length}，{model_label} 成功，提取 {len(block_candidates)} 题"
            )
        except HomeworkImportParseError as exc:
            error_summary = build_trace_preview(str(exc), limit=120)
            failure_summaries.append(error_summary)
            trace.qwen_block_failure_count += 1
            trace.vision_block_results.append(
                f"{block_label}: 长度 {block_length}，{model_label} 失败：{error_summary}"
            )

    trace.qwen_attempted = True
    if trace.qwen_block_failure_count:
        trace.vision_qwen_failure_stop = True
        trace.vision_qwen_failure_reason = f"视觉链路下 {model_label} 失败，已停止 heuristic 自动产题。"
        trace.vision_manual_review_message = "已完成 OCR，但结构化失败，请人工确认。"
        if trace.qwen_block_success_count:
            trace.qwen_success = True
            trace.qwen_error = (
                f"部分失败：{trace.qwen_block_failure_count}/{trace.qwen_block_attempt_count} 个 block 调用失败"
            )
        else:
            trace.qwen_error = failure_summaries[0] if failure_summaries else "视觉 block 结构化失败。"
    else:
        trace.qwen_success = bool(trace.qwen_block_success_count)

    if not merged_candidates and not trace.vision_manual_review_message:
        trace.vision_manual_review_message = "已完成 OCR，但未生成候选题，请人工确认 OCR 文本。"
    return merged_candidates


def _strip_question_start_label_preserving_text(line: str) -> str:
    match = re.match(
        r"^\s*(?:第\s*\d+\s*题|\d+\s*[\.\)、]|Q\s*\d+\s*[:\.\)])\s*(.*)$",
        line,
        re.IGNORECASE,
    )
    return match.group(1) if match else line


def _finalize_candidate_from_block(block_lines: list[str], *, index: int) -> dict | None:
    if not block_lines:
        return None
    stem_lines: list[str] = []
    option_lines: dict[str, str] = {}
    answer = ""
    analysis_lines: list[str] = []
    collecting_analysis = False

    for line in block_lines:
        cleaned = normalize_candidate_text(line)
        if not cleaned:
            if stem_lines and not collecting_analysis:
                stem_lines.append("")
            continue
        option_match = OPTION_RE.match(cleaned)
        if option_match:
            option_lines[option_match.group(1).upper()] = option_match.group(2).strip()
            collecting_analysis = False
            continue
        analysis_match = ANALYSIS_RE.match(cleaned)
        if analysis_match:
            collecting_analysis = True
            if analysis_match.group(1).strip():
                analysis_lines.append(analysis_match.group(1).strip())
            continue
        answer_match = ANSWER_RE.match(cleaned)
        if answer_match:
            answer_text, inline_analysis = split_answer_and_analysis_fragment(answer_match.group(1))
            if answer_text:
                answer = answer_text
            if inline_analysis:
                analysis_lines.append(inline_analysis)
            collecting_analysis = bool(inline_analysis)
            continue
        if collecting_analysis:
            analysis_lines.append(cleaned)
        else:
            stem_lines.append(_strip_question_start_label_preserving_text(line))

    stem = normalize_candidate_stem("\n".join(stem_lines))
    if not stem or len([value for value in option_lines.values() if value]) < 2:
        return None
    options = {key: option_lines.get(key, "") for key in ["A", "B", "C", "D"]}
    resolved_answer = resolve_candidate_correct_answer(answer, options)
    notes = []
    if not resolved_answer:
        notes.append("未稳定识别到正确答案，请老师确认。")
    if any(not value for value in options.values()):
        notes.append("选项未完全识别为 A/B/C/D，请老师补全。")
    return {
        "index": index,
        "stem": stem,
        "options": options,
        "correct_answer": resolved_answer,
        "analysis": normalize_candidate_text(" ".join(analysis_lines)),
        "notes": " ".join(notes).strip(),
        "confidence": None,
    }


def parse_candidates_with_heuristic(source_text: str) -> tuple[list[dict], str]:
    lines = normalize_preserved_text(source_text).split("\n")
    blocks: list[list[str]] = []
    current_block: list[str] = []
    for line in lines:
        if QUESTION_START_RE.match(line.strip()) and current_block:
            blocks.append(current_block)
            current_block = [line]
            continue
        current_block.append(line)
    if current_block:
        blocks.append(current_block)

    candidates = []
    for index, block in enumerate(blocks, start=1):
        candidate = _finalize_candidate_from_block(block, index=index)
        if candidate:
            candidates.append(candidate)
    notes = "已使用本地结构化规则提取单选题候选。"
    return candidates, notes


def determine_import_route(
    import_job: HomeworkImportJob,
    *,
    local_text: str,
) -> HomeworkImportRoute:
    source_type = import_job.source_type
    local_text_length = len(local_text)
    local_text_line_count = count_non_empty_lines(local_text)

    if not bool(getattr(settings, "HOMEWORK_IMPORT_ROUTER_ENABLED", False)):
        if source_type == HomeworkImportJob.SOURCE_TYPE_IMAGE:
            return HomeworkImportRoute(
                name="router_disabled_image_to_volc_vision_to_qwen",
                source_type=source_type,
                selected_text_source="volc_vision",
                use_local_text=False,
                use_vision=True,
                use_qwen=True,
                reason="HOMEWORK_IMPORT_ROUTER_ENABLED=false；图片先抽取文本，再交给 Qwen 结构化。",
            )
        if source_type == HomeworkImportJob.SOURCE_TYPE_TEXT:
            return HomeworkImportRoute(
                name="router_disabled_text_local_text_to_qwen",
                source_type=source_type,
                selected_text_source="local_text",
                use_local_text=True,
                use_vision=False,
                use_qwen=True,
                reason="HOMEWORK_IMPORT_ROUTER_ENABLED=false；TXT 本地抽文本后直接交给 Qwen 结构化。",
            )
        return HomeworkImportRoute(
            name="router_disabled_local_text_to_qwen",
            source_type=source_type,
            selected_text_source="local_text",
            use_local_text=True,
            use_vision=False,
            use_qwen=True,
            reason="HOMEWORK_IMPORT_ROUTER_ENABLED=false，使用本地抽文本后直接进入 Qwen 结构化。",
        )

    if source_type == HomeworkImportJob.SOURCE_TYPE_TEXT:
        return HomeworkImportRoute(
            name="text_local_text_to_qwen",
            source_type=source_type,
            selected_text_source="local_text",
            use_local_text=True,
            use_vision=False,
            use_qwen=True,
            reason="TXT 本地抽文本后直接交给 Qwen 结构化。",
        )

    if source_type in {
        HomeworkImportJob.SOURCE_TYPE_HTML,
        HomeworkImportJob.SOURCE_TYPE_DOCX,
        HomeworkImportJob.SOURCE_TYPE_XLSX,
    }:
        return HomeworkImportRoute(
            name=f"{source_type}_local_text_to_qwen",
            source_type=source_type,
            selected_text_source="local_text",
            use_local_text=True,
            use_vision=False,
            use_qwen=True,
            reason="文本类文档先本地抽文本，再交给 Qwen 结构化。",
        )

    if source_type == HomeworkImportJob.SOURCE_TYPE_PDF:
        if bool(getattr(settings, "HOMEWORK_PDF_FORCE_OCR", False)):
            return HomeworkImportRoute(
                name="pdf_force_ocr_to_volc_vision_to_qwen",
                source_type=source_type,
                selected_text_source="volc_vision",
                use_local_text=True,
                use_vision=True,
                use_qwen=True,
                reason="HOMEWORK_PDF_FORCE_OCR=true，PDF 直接进入火山视觉。",
            )
        if (
            local_text_length >= int(getattr(settings, "HOMEWORK_PDF_MIN_TEXT_LENGTH", 200))
            and local_text_line_count >= int(getattr(settings, "HOMEWORK_PDF_MIN_LINE_COUNT", 8))
        ):
            return HomeworkImportRoute(
                name="pdf_local_text_to_qwen",
                source_type=source_type,
                selected_text_source="local_text",
                use_local_text=True,
                use_vision=False,
                use_qwen=True,
                reason="PDF 文本长度与行数达到阈值，直接使用本地抽文本。",
            )
        return HomeworkImportRoute(
            name="pdf_low_text_to_volc_vision_to_qwen",
            source_type=source_type,
            selected_text_source="volc_vision",
            use_local_text=True,
            use_vision=True,
            use_qwen=True,
            reason="PDF 文本质量不足，先进入火山视觉，再交给 Qwen 结构化。",
        )

    if source_type == HomeworkImportJob.SOURCE_TYPE_IMAGE:
        return HomeworkImportRoute(
            name="image_to_volc_vision_to_qwen",
            source_type=source_type,
            selected_text_source="volc_vision",
            use_local_text=False,
            use_vision=True,
            use_qwen=True,
            reason="图片类文件直接进入火山视觉，再交给 Qwen 结构化。",
        )

    raise HomeworkImportParseError("当前文件类型暂不支持导入识别。")


def extract_source_text_with_router(
    import_job: HomeworkImportJob,
    trace: HomeworkImportTrace,
) -> tuple[str, HomeworkImportRoute]:
    local_text = ""
    if import_job.source_type != HomeworkImportJob.SOURCE_TYPE_IMAGE:
        trace.local_text_attempted = True
        try:
            local_text = extract_local_source_text(import_job)
            trace.local_text_success = bool(local_text)
            trace.local_text = local_text
            trace.local_text_length = len(local_text)
            trace.local_text_line_count = count_non_empty_lines(local_text)
        except HomeworkImportParseError as exc:
            trace.local_text_error = str(exc)

    route = determine_import_route(import_job, local_text=local_text)
    trace.route_name = route.name
    trace.route_reason = route.reason
    trace.selected_text_source = route.selected_text_source
    trace.vision_chain_used = bool(route.use_vision)

    if route.selected_text_source == "local_text":
        return local_text, route

    trace.vision_attempted = True
    try:
        vision_text, vision_note = extract_text_with_volc_vision(import_job, local_text=local_text, trace=trace)
    except HomeworkImportParseError as exc:
        trace.vision_error = str(exc)
        trace.failure_step = "火山视觉"
        trace.failure_reason = str(exc)
        trace.vision_final_failure_code = trace.vision_final_failure_code or get_import_error_code(exc)
        trace.user_facing_message = trace.user_facing_message or normalize_candidate_text(getattr(exc, "user_message", ""))
        if not trace.user_facing_message:
            trace.user_facing_message = build_visual_user_message(
                get_import_error_code(exc),
                final_failure_type=trace.vision_final_failure_type or get_import_failure_type(exc),
            )
        raise

    trace.vision_success = bool(vision_text)
    trace.vision_text = vision_text
    trace.vision_text_length = len(vision_text)
    trace.vision_text_line_count = count_non_empty_lines(vision_text)
    trace.model_notes.append(vision_note)
    return vision_text, route


def _build_trace_lines(import_job: HomeworkImportJob, trace: HomeworkImportTrace) -> list[str]:
    source_type_text = dict(HomeworkImportJob.SOURCE_TYPE_CHOICES).get(import_job.source_type, import_job.source_type)
    model_label = get_homework_llm_model_label()
    lines = [
        f"文件类型：{source_type_text}",
        f"路由：{trace.route_name or '未确定'}",
    ]
    if trace.duplicate_content_hit:
        lines.append(f"重复内容提示：是（{trace.duplicate_content_note or '命中相同内容哈希'}）")
    else:
        lines.append("重复内容提示：否")
    if trace.route_reason:
        lines.append(f"路由原因：{trace.route_reason}")

    if trace.local_text_attempted:
        if trace.local_text_error:
            lines.append(f"本地抽文本：是，失败（{trace.local_text_error}）")
        else:
            lines.append(
                f"本地抽文本：是，长度 {trace.local_text_length}，行数 {trace.local_text_line_count}"
            )
    else:
        lines.append("本地抽文本：否")

    lines.append(f"视觉链路：{'是' if trace.vision_chain_used else '否'}")

    if trace.vision_attempted:
        if trace.vision_error:
            lines.append(f"火山视觉：是，失败（{trace.vision_error}）")
        else:
            lines.append(
                f"火山视觉：是，成功，长度 {trace.vision_text_length}，行数 {trace.vision_text_line_count}"
            )
    else:
        lines.append("火山视觉：否")

    if trace.vision_chain_used:
        lines.append(f"全部片段超时中止：{'是' if trace.vision_all_timeout_abort else '否'}")
        lines.extend(trace.vision_request_results)
        if trace.vision_final_failure_code:
            lines.append(f"视觉最终失败原因：{trace.vision_final_failure_code}")
        if trace.vision_final_failure_type:
            lines.append(f"视觉最终失败类型：{trace.vision_final_failure_type}")
        if trace.vision_block_split_applied:
            lines.append(f"题块切分：是，切出 {trace.vision_block_count} 个 block")
        elif trace.vision_block_count:
            lines.append(f"题块切分：否，按 1 个 block 结构化")
        else:
            lines.append("题块切分：否")
        if trace.vision_text_preview:
            lines.append(f"OCR 文本预览：{trace.vision_text_preview}")
        lines.extend(trace.vision_block_results)

    if trace.image_sliced:
        lines.append(f"图片切片：是，{trace.image_slice_count} 片")
        if trace.image_slice_results:
            lines.append(f"切片结果：{'；'.join(trace.image_slice_results)}")
    else:
        lines.append("图片切片：否")

    if trace.pdf_raster_attempted:
        if trace.pdf_rasterized:
            raster_note = f"PDF 光栅化：是，处理 {trace.pdf_pages_processed}/{trace.pdf_total_pages} 页"
            if trace.pdf_page_limit_hit:
                raster_note = f"{raster_note}（已命中页数上限）"
            lines.append(raster_note)
        else:
            lines.append(f"PDF 光栅化：是，失败（{trace.vision_final_failure_code or 'pdf_render_failed'}）")
        lines.extend(trace.pdf_render_results)
        lines.extend(trace.pdf_page_slice_results)
    else:
        lines.append("PDF 光栅化：否")

    if trace.qwen_attempted:
        if trace.qwen_block_failure_count and trace.qwen_block_success_count:
            lines.append(
                f"{model_label}：是，部分成功（成功 {trace.qwen_block_success_count}/{trace.qwen_block_attempt_count} 个 block，失败 {trace.qwen_block_failure_count} 个 block）"
            )
        elif trace.qwen_error:
            lines.append(f"{model_label}：是，失败（{trace.qwen_error}）")
        else:
            lines.append(f"{model_label}：是，成功")
    else:
        lines.append(f"{model_label}：否")
    lines.extend(trace.qwen_block_results)

    if trace.fallback_used:
        lines.append(f"fallback heuristic：是（{trace.fallback_reason or '已退回本地规则解析'}）")
    else:
        lines.append("fallback heuristic：否")

    if trace.selected_text_source:
        lines.append(f"最终结构化文本来源：{trace.selected_text_source}")

    if trace.vision_qwen_failure_stop:
        lines.append(trace.vision_qwen_failure_reason)
    if trace.vision_manual_review_message:
        lines.append(trace.vision_manual_review_message)
    if trace.user_facing_message:
        lines.append(f"页面提示：{trace.user_facing_message}")

    lines.append(f"最终候选题数量：{trace.candidate_count} 道")

    if trace.model_notes:
        lines.append(f"补充说明：{'；'.join(note for note in trace.model_notes if note)}")

    if trace.failure_reason:
        lines.append(f"失败步骤：{trace.failure_step or '未定位'}")
        lines.append(f"失败原因：{trace.failure_reason}")

    if trace.debug_enabled:
        lines.append(f"调试：router_enabled={trace.router_enabled}")

    return lines


def _finalize_import_job_parse_notes(import_job: HomeworkImportJob, trace: HomeworkImportTrace) -> str:
    return "\n".join(line for line in _build_trace_lines(import_job, trace) if line).strip()


def _format_elapsed_seconds(started_at: float) -> str:
    if not started_at:
        return "0 秒"
    elapsed_seconds = max(int(time.monotonic() - started_at), 0)
    minutes, seconds = divmod(elapsed_seconds, 60)
    if minutes:
        return f"{minutes} 分 {seconds} 秒"
    return f"{seconds} 秒"


def _build_import_progress_notes(import_job: HomeworkImportJob, trace: HomeworkImportTrace) -> str:
    source_type_text = dict(HomeworkImportJob.SOURCE_TYPE_CHOICES).get(import_job.source_type, import_job.source_type)
    lines = [
        f"解析进度：{trace.progress_step or '后台解析中'}",
        f"进度更新时间：{timezone.localtime(timezone.now()).strftime('%H:%M:%S')}",
        f"文件类型：{source_type_text}",
    ]
    if trace.progress_detail:
        lines.append(f"当前步骤：{trace.progress_detail}")
    if trace.route_name:
        lines.append(f"路由：{trace.route_name}")
    if trace.local_text_attempted:
        if trace.local_text_success:
            lines.append(f"本地抽文本：完成，长度 {trace.local_text_length}，行数 {trace.local_text_line_count}")
        elif trace.local_text_error:
            lines.append(f"本地抽文本：失败（{trace.local_text_error}）")
        else:
            lines.append("本地抽文本：进行中")
    if trace.vision_attempted:
        if trace.vision_success:
            lines.append(f"视觉 OCR：完成，长度 {trace.vision_text_length}，行数 {trace.vision_text_line_count}")
        elif trace.vision_error:
            lines.append(f"视觉 OCR：失败（{trace.vision_error}）")
        else:
            lines.append("视觉 OCR：进行中")
    if trace.qwen_attempted:
        block_summary = ""
        if trace.qwen_block_attempt_count:
            block_summary = (
                f"，成功 {trace.qwen_block_success_count}/{trace.qwen_block_attempt_count}"
                f"，失败 {trace.qwen_block_failure_count}"
            )
        lines.append(f"{get_homework_llm_model_label()}：进行中{block_summary}")
    lines.append("提示：模型调用期间可能单次等待到超时上限；页面会自动刷新显示最新阶段。")
    return "\n".join(lines)


def update_import_job_progress(import_job: HomeworkImportJob, trace: HomeworkImportTrace, step: str, detail: str = "") -> None:
    trace.progress_step = step
    trace.progress_detail = detail
    import_job.parse_notes = append_question_source_knowledge_marker_lines(
        _build_import_progress_notes(import_job, trace),
        extract_question_source_knowledge_marker_lines(import_job.parse_notes),
    )
    import_job.save(update_fields=["parse_notes", "updated_at"])


def extract_question_source_knowledge_marker_lines(parse_notes: object) -> list[str]:
    return [
        line.strip()
        for line in str(parse_notes or "").splitlines()
        if line.strip().startswith(QUESTION_SOURCE_KNOWLEDGE_MARKER)
    ]


def append_question_source_knowledge_marker_lines(parse_notes: str, marker_lines: list[str]) -> str:
    unique_lines = []
    seen = set()
    for line in marker_lines:
        normalized = line.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_lines.append(normalized)
    if not unique_lines:
        return parse_notes
    visible_lines = [
        line
        for line in str(parse_notes or "").splitlines()
        if not line.strip().startswith(QUESTION_SOURCE_KNOWLEDGE_MARKER)
    ]
    return "\n".join([*visible_lines, *unique_lines]).strip()


def parse_question_source_knowledge_snapshot(parse_notes: object) -> dict[str, str]:
    marker_lines = extract_question_source_knowledge_marker_lines(parse_notes)
    if not marker_lines:
        return {}
    raw_payload = marker_lines[-1][len(QUESTION_SOURCE_KNOWLEDGE_MARKER):].strip()
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "knowledge_subject": str(payload.get("subject") or "").strip(),
        "level_code": str(payload.get("category_code") or "").strip().upper(),
        "knowledge_level_1": str(payload.get("level_1") or "").strip(),
        "knowledge_level_2": str(payload.get("level_2") or "").strip(),
        "knowledge_level_3": str(payload.get("level_3") or "").strip(),
    }


def parse_homework_import_job(import_job: HomeworkImportJob) -> HomeworkImportJob:
    preserved_metadata_lines = extract_question_source_knowledge_marker_lines(import_job.parse_notes)
    import_job.parse_status = HomeworkImportJob.STATUS_PARSING
    model_label = get_homework_llm_model_label()
    trace = HomeworkImportTrace(
        source_type=import_job.source_type,
        router_enabled=bool(getattr(settings, "HOMEWORK_IMPORT_ROUTER_ENABLED", False)),
        debug_enabled=bool(getattr(settings, "HOMEWORK_IMPORT_DEBUG", False)),
        progress_started_at=time.monotonic(),
    )
    import_job.parse_notes = append_question_source_knowledge_marker_lines(
        _build_import_progress_notes(import_job, trace),
        preserved_metadata_lines,
    )
    import_job.save(update_fields=["parse_status", "parse_notes", "updated_at"])
    duplicate_job = find_recent_duplicate_import_job(import_job)
    if duplicate_job is not None:
        duplicate_status_text = dict(HomeworkImportJob.PARSE_STATUS_CHOICES).get(
            duplicate_job.parse_status,
            duplicate_job.parse_status,
        )
        trace.duplicate_content_hit = True
        trace.duplicate_content_note = (
            f"最近同内容导入任务 #{duplicate_job.id}，"
            f"创建于 {_format_trace_datetime(duplicate_job.created_at)}，"
            f"状态 {duplicate_status_text}。"
        )
    try:
        update_import_job_progress(import_job, trace, "抽取源文件文本", "正在读取上传文件并判断解析路线。")
        source_text, route = extract_source_text_with_router(import_job, trace)
        update_import_job_progress(
            import_job,
            trace,
            "源文件文本抽取完成",
            f"已选择路线 {route.name}，准备结构化候选题。",
        )
        candidates: list[dict] = []
        heuristic_candidates: list[dict] = []
        heuristic_notes = ""

        if route.use_vision:
            if not source_text:
                trace.vision_manual_review_message = "已完成 OCR，但未提取到可结构化文本，请人工确认。"
            else:
                update_import_job_progress(import_job, trace, "结构化 OCR 文本", "正在把 OCR 文本交给模型提取候选题。")
                candidates = parse_visual_blocks_with_qwen(
                    source_text,
                    source_type=import_job.source_type,
                    trace=trace,
                    progress_callback=lambda step, detail="": update_import_job_progress(import_job, trace, step, detail),
                )
        elif not source_text:
            if (
                bool(getattr(settings, "HOMEWORK_IMPORT_ALLOW_FALLBACK_HEURISTIC", True))
                and trace.local_text
            ):
                trace.fallback_attempted = True
                trace.fallback_used = True
                trace.fallback_reason = "结构化文本为空，退回本地 heuristic。"
                candidates, heuristic_notes = parse_candidates_with_heuristic(trace.local_text)
                trace.model_notes.append(heuristic_notes)
            else:
                raise HomeworkImportParseError("文件内容为空，无法识别单选题。")
        else:
            heuristic_candidates, heuristic_notes = parse_candidates_with_heuristic(source_text)
            if not route.use_qwen:
                candidates = heuristic_candidates
                trace.model_notes.append(heuristic_notes)
            else:
                try:
                    update_import_job_progress(import_job, trace, "结构化文本", "正在把抽取文本交给模型提取候选题。")
                    candidates, qwen_notes = parse_text_blocks_with_qwen(
                        source_text,
                        source_type=import_job.source_type,
                        source_origin=trace.selected_text_source or route.selected_text_source,
                        trace=trace,
                        progress_callback=lambda step, detail="": update_import_job_progress(import_job, trace, step, detail),
                    )
                    trace.model_notes.append(qwen_notes)
                    if len(heuristic_candidates) > len(candidates):
                        trace.fallback_attempted = True
                        trace.fallback_used = True
                        trace.fallback_reason = (
                            f"本地结构化规则提取 {len(heuristic_candidates)} 道，"
                            f"{model_label} 提取 {len(candidates)} 道；已使用本地结果补齐。"
                        )
                        trace.model_notes.append(heuristic_notes)
                        candidates = heuristic_candidates
                except HomeworkImportParseError as exc:
                    trace.qwen_error = str(exc)
                    if heuristic_candidates:
                        trace.fallback_attempted = True
                        trace.fallback_used = True
                        trace.fallback_reason = f"{model_label} 不可用或调用失败，已使用本地结构化规则：{exc}"
                        candidates = heuristic_candidates
                        trace.model_notes.append(heuristic_notes)
                    elif bool(getattr(settings, "HOMEWORK_IMPORT_ALLOW_FALLBACK_HEURISTIC", True)):
                        trace.fallback_attempted = True
                        trace.fallback_used = True
                        trace.fallback_reason = f"{model_label} 不可用或调用失败，退回本地 heuristic：{exc}"
                        fallback_text = source_text or trace.local_text
                        candidates, heuristic_notes = parse_candidates_with_heuristic(fallback_text)
                        trace.model_notes.append(heuristic_notes)
                    else:
                        trace.failure_step = model_label
                        trace.failure_reason = str(exc)
                        raise

        candidates = [sanitize_candidate(item, index=index) for index, item in enumerate(candidates, start=1)]
        if (
            candidates
            and bool(getattr(settings, "HOMEWORK_IMPORT_SUPPLEMENT_MISSING_ANSWERS", True))
            and any(candidate_needs_answer_analysis_supplement(candidate) for candidate in candidates)
        ):
            try:
                candidates, supplement_notes = supplement_candidate_answers_with_qwen(
                    candidates,
                    source_text=source_text or trace.local_text,
                    source_type=import_job.source_type,
                    source_origin=trace.selected_text_source or route.selected_text_source,
                    progress_callback=lambda step, detail="": update_import_job_progress(import_job, trace, step, detail),
                )
                if supplement_notes:
                    trace.model_notes.append(supplement_notes)
            except HomeworkImportParseError as exc:
                trace.model_notes.append(f"{model_label} 答案解析补全失败，保留候选题等待老师手动补充：{exc}")
        trace.candidate_count = len(candidates)
        import_job.candidates_json = encode_sql_ascii_json_text(candidates)
        import_job.parse_notes = append_question_source_knowledge_marker_lines(
            _finalize_import_job_parse_notes(import_job, trace),
            preserved_metadata_lines,
        )

        if route.use_vision:
            import_job.parse_status = HomeworkImportJob.STATUS_PARSED
        else:
            if not candidates:
                raise HomeworkImportParseError("没有识别到可确认的单选题候选。")
            import_job.parse_status = HomeworkImportJob.STATUS_PARSED
    except HomeworkImportParseError as exc:
        trace.failure_reason = trace.failure_reason or str(exc)
        trace.user_facing_message = trace.user_facing_message or normalize_candidate_text(getattr(exc, "user_message", ""))
        if trace.vision_chain_used and not trace.user_facing_message:
            trace.user_facing_message = build_visual_user_message(
                get_import_error_code(exc),
                final_failure_type=trace.vision_final_failure_type or get_import_failure_type(exc),
            )
        if not trace.failure_step:
            if trace.qwen_error:
                trace.failure_step = model_label
            elif trace.vision_error:
                trace.failure_step = "火山视觉"
            elif trace.local_text_error:
                trace.failure_step = "本地抽文本"
            else:
                trace.failure_step = "导入解析"
        import_job.candidates_json = []
        import_job.parse_notes = append_question_source_knowledge_marker_lines(
            _finalize_import_job_parse_notes(import_job, trace),
            preserved_metadata_lines,
        )
        import_job.parse_status = HomeworkImportJob.STATUS_FAILED
    import_job.save(update_fields=["candidates_json", "parse_notes", "parse_status", "updated_at"])
    return import_job


def normalize_candidate_editor_rows(candidates_json: object) -> list[dict]:
    candidates_json = decode_sql_ascii_json_text(candidates_json)
    if not isinstance(candidates_json, list):
        return []
    rows = []
    for index, candidate in enumerate(candidates_json, start=1):
        sanitized = sanitize_candidate(candidate if isinstance(candidate, dict) else {}, index=index)
        rows.append(
            {
                **sanitized,
                "row_index": index - 1,
                "included": bool((candidate or {}).get("included", True)) if isinstance(candidate, dict) else True,
            }
        )
    return rows


def normalize_confirmed_candidate_payloads(payloads: list[dict]) -> list[dict]:
    normalized = []
    for index, payload in enumerate(payloads, start=1):
        candidate = sanitize_candidate(payload, index=index)
        included = bool(payload.get("included"))
        candidate["included"] = included
        normalized.append(candidate)
    return normalized


def normalize_and_validate_confirmed_candidate_payloads(payloads: list[dict]) -> tuple[list[dict], list[dict]]:
    if not payloads:
        raise HomeworkImportParseError("候选题提交数据为空，请刷新页面后重试。")

    normalized_payloads = normalize_confirmed_candidate_payloads(payloads)
    selected_candidates = [item for item in normalized_payloads if item["included"]]
    if not selected_candidates:
        raise HomeworkImportParseError("请至少保留一道候选题后再确认导入。")

    for candidate in selected_candidates:
        if not candidate["stem"]:
            raise HomeworkImportParseError("题干不能为空。")
        if any(not candidate["options"][key] for key in ["A", "B", "C", "D"]):
            raise HomeworkImportParseError("A/B/C/D 选项必须全部填写完整。")
        if candidate["correct_answer"] not in {"A", "B", "C", "D"}:
            raise HomeworkImportParseError("正确答案必须是 A/B/C/D 之一。")
    return normalized_payloads, selected_candidates


def build_homework_question_from_candidate(
    *,
    assignment: HomeworkAssignment | None,
    import_job: HomeworkImportJob,
    candidate: dict,
    question_no: int,
    extra_snapshot: dict[str, object] | None = None,
) -> HomeworkQuestion:
    snapshot = {
        "import_job_id": import_job.id,
        "source_filename": import_job.source_filename,
        "candidate_index": candidate["index"],
        "notes": candidate["notes"],
    }
    if extra_snapshot:
        snapshot.update(extra_snapshot)
    return HomeworkQuestion(
        assignment=assignment,
        import_job=import_job,
        question_no=question_no,
        question_type=HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
        stem=candidate["stem"],
        options_json=encode_sql_ascii_json_text(candidate["options"]),
        correct_answer=candidate["correct_answer"],
        analysis=candidate["analysis"],
        source_snapshot_json=encode_sql_ascii_json_text(snapshot),
        is_active=True,
    )


def _format_homework_validation_error(exc: ValidationError) -> str:
    if getattr(exc, "message_dict", None):
        parts = []
        for field_name, messages in exc.message_dict.items():
            rendered_messages = " / ".join(normalize_candidate_text(message) for message in messages if message)
            if rendered_messages:
                parts.append(f"{field_name}: {rendered_messages}")
        if parts:
            return "；".join(parts)
    if getattr(exc, "messages", None):
        rendered_messages = [normalize_candidate_text(message) for message in exc.messages if message]
        if rendered_messages:
            return "；".join(rendered_messages)
    return normalize_candidate_text(str(exc)) or "数据校验失败。"


def confirm_homework_import_job(
    import_job: HomeworkImportJob,
    payloads: list[dict],
    *,
    operator: PortalUser | None = None,
) -> list[HomeworkQuestion]:
    normalized_payloads, _selected_candidates = normalize_and_validate_confirmed_candidate_payloads(payloads)

    try:
        with transaction.atomic():
            locked_import_job = (
                HomeworkImportJob.objects
                .select_for_update()
                .get(id=import_job.id, is_active=True)
            )
            assignment = (
                HomeworkAssignment.objects.select_for_update()
                .get(id=locked_import_job.assignment_id, is_active=True)
            )

            if locked_import_job.parse_status != HomeworkImportJob.STATUS_PARSED:
                raise HomeworkImportParseError("当前导入任务不是待确认状态，请刷新页面后重试。")
            if operator is not None and (
                locked_import_job.teacher_id != operator.id or assignment.teacher_id != operator.id
            ):
                raise HomeworkImportParseError("当前老师没有权限操作这份作业。")
            if assignment.status == HomeworkAssignment.STATUS_CANCELLED:
                raise HomeworkImportParseError("当前作业已取消，不能继续确认在线题目。")
            if assignment.submissions.filter(is_active=True).exists():
                raise HomeworkImportParseError("当前作业已经有学生提交记录，不能再覆盖正式题目。")

            HomeworkQuestion.objects.filter(assignment=assignment, is_active=True).update(
                is_active=False,
                updated_at=timezone.now(),
            )
            created_questions = []
            reviewed_candidates = []
            for candidate in normalized_payloads:
                reviewed_candidate = dict(candidate)
                if candidate["included"]:
                    question = build_homework_question_from_candidate(
                        assignment=assignment,
                        import_job=locked_import_job,
                        candidate=candidate,
                        question_no=len(created_questions) + 1,
                    )
                    try:
                        question.full_clean()
                    except ValidationError as exc:
                        raise HomeworkImportParseError(
                            f"第 {len(created_questions) + 1} 道正式题目校验失败：{_format_homework_validation_error(exc)}"
                        ) from exc
                    question.save()
                    reviewed_candidate["question_no"] = question.question_no
                    created_questions.append(question)
                reviewed_candidates.append(reviewed_candidate)

            assignment.status = HomeworkAssignment.STATUS_ASSIGNED
            assignment.source_import_job = locked_import_job
            assignment.completed_at = None
            assignment.reviewed_at = None
            assignment.teacher_comment = ""
            assignment.save(
                update_fields=[
                    "status",
                    "source_import_job",
                    "completed_at",
                    "reviewed_at",
                    "teacher_comment",
                    "updated_at",
                ]
            )

            locked_import_job.candidates_json = encode_sql_ascii_json_text(reviewed_candidates)
            locked_import_job.parse_status = HomeworkImportJob.STATUS_CONFIRMED
            locked_import_job.confirmed_at = timezone.now()
            locked_import_job.parse_notes = "\n".join(
                [
                    line
                    for line in [
                        locked_import_job.parse_notes.strip(),
                        f"教师已确认并覆盖当前作业正式题目，共写入 {len(created_questions)} 道题。",
                        "作业状态已重置为 assigned，并清空旧完成时间、评阅时间和教师评语。",
                    ]
                    if line
                ]
            )
            locked_import_job.save(update_fields=["candidates_json", "parse_status", "confirmed_at", "parse_notes", "updated_at"])
            import_job.candidates_json = locked_import_job.candidates_json
            import_job.parse_status = locked_import_job.parse_status
            import_job.confirmed_at = locked_import_job.confirmed_at
            import_job.parse_notes = locked_import_job.parse_notes
        return created_questions
    except IntegrityError as exc:
        raise HomeworkImportParseError(f"正式题目写入失败：{exc}") from exc


def confirm_question_source_import_job(
    import_job: HomeworkImportJob,
    payloads: list[dict],
    *,
    operator: PortalUser | None = None,
) -> list[HomeworkQuestion]:
    normalized_payloads, _selected_candidates = normalize_and_validate_confirmed_candidate_payloads(payloads)

    try:
        with transaction.atomic():
            locked_import_job = (
                HomeworkImportJob.objects
                .select_for_update()
                .get(id=import_job.id, is_active=True)
            )
            if locked_import_job.assignment_id is not None:
                raise HomeworkImportParseError("当前导入任务属于学生作业，不能按公共题池导入确认。")
            if locked_import_job.parse_status != HomeworkImportJob.STATUS_PARSED:
                raise HomeworkImportParseError("当前导入任务不是待确认状态，请刷新页面后重试。")
            if operator is not None and locked_import_job.teacher_id != operator.id:
                raise HomeworkImportParseError("当前老师没有权限操作这份公共题池导入记录。")

            HomeworkQuestion.objects.filter(import_job=locked_import_job, is_active=True).update(
                is_active=False,
                updated_at=timezone.now(),
            )
            created_questions = []
            reviewed_candidates = []
            knowledge_snapshot = parse_question_source_knowledge_snapshot(locked_import_job.parse_notes)
            for candidate in normalized_payloads:
                reviewed_candidate = dict(candidate)
                if candidate["included"]:
                    question = build_homework_question_from_candidate(
                        assignment=None,
                        import_job=locked_import_job,
                        candidate=candidate,
                        question_no=len(created_questions) + 1,
                        extra_snapshot={
                            "question_source_import": True,
                            "content_id": locked_import_job.content_id or 0,
                            **knowledge_snapshot,
                        },
                    )
                    try:
                        question.full_clean()
                    except ValidationError as exc:
                        raise HomeworkImportParseError(
                            f"第 {len(created_questions) + 1} 道公共题池题目校验失败：{_format_homework_validation_error(exc)}"
                        ) from exc
                    question.save()
                    reviewed_candidate["question_no"] = question.question_no
                    created_questions.append(question)
                reviewed_candidates.append(reviewed_candidate)

            locked_import_job.candidates_json = encode_sql_ascii_json_text(reviewed_candidates)
            locked_import_job.parse_status = HomeworkImportJob.STATUS_CONFIRMED
            locked_import_job.confirmed_at = timezone.now()
            locked_import_job.parse_notes = "\n".join(
                [
                    line
                    for line in [
                        locked_import_job.parse_notes.strip(),
                        f"教师已确认并写入公共题池题目，共写入 {len(created_questions)} 道题。",
                    ]
                    if line
                ]
            )
            locked_import_job.save(
                update_fields=["candidates_json", "parse_status", "confirmed_at", "parse_notes", "updated_at"]
            )
            import_job.candidates_json = locked_import_job.candidates_json
            import_job.parse_status = locked_import_job.parse_status
            import_job.confirmed_at = locked_import_job.confirmed_at
            import_job.parse_notes = locked_import_job.parse_notes
        return created_questions
    except IntegrityError as exc:
        raise HomeworkImportParseError(f"公共题池题目写入失败：{exc}") from exc


def grade_homework_submission(
    assignment: HomeworkAssignment,
    student: Student,
    *,
    selected_answers: dict[int, str],
) -> HomeworkSubmission:
    if not assignment.is_active or assignment.status == HomeworkAssignment.STATUS_CANCELLED:
        raise HomeworkImportParseError("当前作业已取消，不能继续提交。")
    questions = list(assignment.get_effective_questions_queryset())
    if not questions:
        raise HomeworkImportParseError("当前作业还没有正式题目，暂时不能在线提交。")

    with transaction.atomic():
        submission = HomeworkSubmission.objects.create(
            assignment=assignment,
            student=student,
            status=HomeworkSubmission.STATUS_IN_PROGRESS,
            started_at=timezone.now(),
            is_active=True,
        )

        correct_count = 0
        for question in questions:
            selected_answer = normalize_candidate_text(selected_answers.get(question.id)).upper()[:1]
            is_correct = bool(selected_answer and selected_answer == question.correct_answer)
            if is_correct:
                correct_count += 1
            HomeworkSubmissionAnswer.objects.create(
                submission=submission,
                homework_question=question,
                selected_answer=selected_answer if selected_answer in {"A", "B", "C", "D"} else "",
                is_correct=is_correct,
                correct_answer_snapshot=question.correct_answer,
                analysis_snapshot=question.analysis,
            )

        total_count = len(questions)
        wrong_count = total_count - correct_count
        score = Decimal("0.00")
        if total_count:
            score = (Decimal(correct_count) * Decimal("100")) / Decimal(total_count)
            score = score.quantize(Decimal("0.01"))

        now = timezone.now()
        submission.status = HomeworkSubmission.STATUS_AUTO_CHECKED
        submission.total_count = total_count
        submission.correct_count = correct_count
        submission.wrong_count = wrong_count
        submission.score = score
        if not submission.started_at:
            submission.started_at = now
        submission.submitted_at = now
        submission.checked_at = now
        submission.is_active = True
        submission.save(
            update_fields=[
                "status",
                "total_count",
                "correct_count",
                "wrong_count",
                "score",
                "started_at",
                "submitted_at",
                "checked_at",
                "is_active",
                "updated_at",
            ]
        )

        if assignment.mark_completed():
            assignment.save(update_fields=["status", "completed_at", "updated_at"])

    return submission
