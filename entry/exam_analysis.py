from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests
from django.conf import settings

from .exam_paper_import import (
    ExamPaperImportError,
    extract_qwen_message_text,
    get_qwen_api_key,
    get_qwen_base_url,
    get_qwen_ocr_model,
    image_data_url,
)
from .models import ExamQuestion, ExamQuestionAnalysisBlock, ExamQuestionBankQuestion


AI_ANALYSIS_CHALLENGE_MD = "**此解析由 AI 生成，你要挑战吗？**"
AI_ANALYSIS_FALLBACK_MD = (
    "AI 解析暂未生成成功。你可以先根据题目截图、选项和正确答案，尝试写出自己的解题思路。\n\n"
    f"{AI_ANALYSIS_CHALLENGE_MD}"
)


def get_qwen_analysis_model() -> str:
    return str(
        getattr(settings, "QWEN_ANALYSIS_MODEL", "")
        or getattr(settings, "QWEN_OCR_MODEL", "")
        or getattr(settings, "HOMEWORK_LLM_MODEL", "")
        or get_qwen_ocr_model()
    ).strip()


def build_question_analysis_markdown(question: ExamQuestion) -> str:
    blocks = list(question.analysis_blocks.filter(is_visible=True).order_by("sort_order", "id"))
    if blocks:
        return "\n\n".join(block.content_md.strip() for block in blocks if block.content_md.strip())
    return str(question.analysis or "").strip()


def sync_legacy_question_analysis(question: ExamQuestion) -> str:
    analysis_md = build_question_analysis_markdown(question)
    if question.analysis != analysis_md:
        question.analysis = analysis_md
        question.save(update_fields=["analysis", "updated_at"])
    return analysis_md


def _media_image_paths_for_question(question: ExamQuestion) -> list[Path]:
    snapshot = question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}
    raw_paths: list[object] = []
    for key in ("material_image_paths", "question_image_paths", "image_paths"):
        value = snapshot.get(key)
        if isinstance(value, list):
            raw_paths.extend(value)
    if question.image_path:
        raw_paths.append(question.image_path)

    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    image_paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        relative_text = str(raw_path or "").strip()
        if not relative_text or relative_text in seen:
            continue
        candidate = (media_root / relative_text).resolve(strict=False)
        try:
            candidate.relative_to(media_root)
        except ValueError:
            continue
        if candidate.is_file():
            image_paths.append(candidate)
            seen.add(relative_text)
    return image_paths[:6]


def _media_image_paths_for_bank_question(bank_question: ExamQuestionBankQuestion) -> list[Path]:
    raw_paths: list[object] = []
    full_json = bank_question.full_json if isinstance(bank_question.full_json, dict) else {}
    for key in ("material_image_paths", "question_image_paths", "image_paths"):
        value = full_json.get(key)
        if isinstance(value, list):
            raw_paths.extend(value)
    raw_paths.extend(
        bank_question.assets.filter(asset_role="content")
        .exclude(relative_path="")
        .order_by("id")
        .values_list("relative_path", flat=True)
    )

    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    image_paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        relative_text = str(raw_path or "").strip()
        if not relative_text or relative_text in seen:
            continue
        candidate = (media_root / relative_text).resolve(strict=False)
        try:
            candidate.relative_to(media_root)
        except ValueError:
            continue
        if candidate.is_file():
            image_paths.append(candidate)
            seen.add(relative_text)
    return image_paths[:6]


def _get_bank_question_answer_text(bank_question: ExamQuestionBankQuestion) -> str:
    answer_json = bank_question.answer_json if isinstance(bank_question.answer_json, dict) else {}
    for key in ("correct_answer", "answer", "value"):
        if key in answer_json:
            value = answer_json.get(key)
            if isinstance(value, bool):
                return "A" if value else "B"
            return str(value or "").strip()
    return ""


def _build_ai_analysis_prompt(question: ExamQuestion) -> str:
    options = question.options_json if isinstance(question.options_json, dict) else {}
    option_lines = "\n".join(
        f"{key}. {value}"
        for key, value in sorted(options.items())
        if str(key or "").strip() and str(value or "").strip()
    )
    return "\n".join(
        [
            "你是少儿编程考试老师。请基于题目截图和结构化信息，为学生生成一段简洁但有帮助的中文解析。",
            "要求：",
            "1. 先说明正确答案为什么对，再指出常见误区。",
            "2. 解析不要过长，控制在适合小学生阅读的长度。",
            "3. 如果涉及 C++ 代码，必须使用 Markdown 代码块，并保留合理缩进。",
            "4. 不要编造题目截图中没有的信息。",
            "5. 最后一行不要写挑战提示，系统会自动追加。",
            "",
            f"题号：第 {question.question_no} 题",
            f"题型：{question.question_type}",
            f"正确答案：{question.correct_answer or '未填写'}",
            f"题干文字：{question.stem or '以题目截图为准'}",
            "选项：",
            option_lines or "无",
        ]
    )


def _build_bank_question_analysis_prompt(bank_question: ExamQuestionBankQuestion) -> str:
    options = [
        f"{option.option_key}. {option.option_text_md}"
        for option in bank_question.options.order_by("sort_order", "option_key")
        if str(option.option_key or "").strip() and str(option.option_text_md or "").strip()
    ]
    return "\n".join(
        [
            "你是少儿编程考试老师。请基于题目截图和结构化信息，为学生生成一段简洁但有帮助的中文解析。",
            "要求：",
            "1. 先说明正确答案为什么对，再指出常见误区。",
            "2. 解析不要过长，控制在适合小学生阅读的长度。",
            "3. 如果涉及 C++ 代码，必须使用 Markdown 代码块，并保留合理缩进。",
            "4. 不要编造题目截图中没有的信息。",
            "5. 最后一行不要写挑战提示，系统会自动追加。",
            "",
            f"题号：第 {bank_question.question_no} 题",
            f"题型：{bank_question.question_type}",
            f"正确答案：{_get_bank_question_answer_text(bank_question) or '未填写'}",
            f"题干文字：{bank_question.stem_md or '以题目截图为准'}",
            "选项：",
            "\n".join(options) or "无",
        ]
    )


def _strip_json_code_fence(text: str) -> str:
    content = text.strip()
    if content.startswith("```"):
        content = content.removeprefix("```").strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()
    return content


def _request_qwen_analysis(*, prompt: str, image_paths: list[Path], append_challenge: bool = True) -> str:
    api_key = get_qwen_api_key()
    base_url = get_qwen_base_url()
    model = get_qwen_analysis_model()
    if not api_key or not base_url or not model:
        raise ExamPaperImportError("Qwen 解析生成未配置 API Key、Base URL 或模型。")

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_path in image_paths:
        content.append({"type": "image_url", "image_url": {"url": image_data_url(image_path)}})
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": float(getattr(settings, "QWEN_ANALYSIS_TEMPERATURE", 0.2)),
        "top_p": float(getattr(settings, "QWEN_ANALYSIS_TOP_P", 0.8)),
    }
    if not bool(getattr(settings, "QWEN_OCR_ENABLE_THINKING", False)):
        payload["extra_body"] = {"enable_thinking": False}

    url = base_url.rstrip("/") + "/chat/completions"
    timeout_seconds = max(int(getattr(settings, "QWEN_TIMEOUT_SECONDS", 180)), 1)
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        raise ExamPaperImportError(f"Qwen 解析生成失败：HTTP {response.status_code}")
    content_md = extract_qwen_message_text(response.json()).strip()
    if not content_md:
        raise ExamPaperImportError("Qwen 解析生成返回为空。")
    if append_challenge and AI_ANALYSIS_CHALLENGE_MD not in content_md:
        content_md = f"{content_md}\n\n{AI_ANALYSIS_CHALLENGE_MD}"
    return content_md


def _build_bank_question_knowledge_prompt(
    *,
    bank_question: ExamQuestionBankQuestion,
    mapping_markdown: str,
) -> str:
    options = [
        f"{option.option_key}. {option.option_text_md}"
        for option in bank_question.options.order_by("sort_order", "option_key")
        if str(option.option_key or "").strip() and str(option.option_text_md or "").strip()
    ]
    return "\n".join(
        [
            "你是少儿编程考试知识点标注助手。请严格根据下面的知识对照表，为题目选择最匹配的知识点。",
            "只输出 JSON，不要输出 Markdown、解释或多余文字。",
            "JSON 格式必须是：",
            '{"level_1":"一级目录","level_2":"二级目录","level_3":"三级训练点或空字符串"}',
            "要求：",
            "1. level_1 和 level_2 必须来自知识对照表中的同一行。",
            "2. level_3 可以从同一行的三级训练点中提炼，无法确定时填空字符串。",
            "3. 不要编造知识对照表不存在的一级目录和二级目录。",
            "",
            "知识对照表：",
            mapping_markdown,
            "",
            "题目信息：",
            f"题号：第 {bank_question.question_no} 题",
            f"题型：{bank_question.question_type}",
            f"正确答案：{_get_bank_question_answer_text(bank_question) or '未填写'}",
            f"题干文字：{bank_question.stem_md or '以题目截图为准'}",
            "选项：",
            "\n".join(options) or "无",
            "现有解析：",
            str(bank_question.analysis_md or "").strip() or "无",
        ]
    )


def identify_bank_question_knowledge_points(
    *,
    bank_question: ExamQuestionBankQuestion,
    mapping_markdown: str,
) -> dict[str, str]:
    content = _request_qwen_analysis(
        prompt=_build_bank_question_knowledge_prompt(
            bank_question=bank_question,
            mapping_markdown=mapping_markdown,
        ),
        image_paths=_media_image_paths_for_bank_question(bank_question),
        append_challenge=False,
    )
    try:
        parsed = json.loads(_strip_json_code_fence(content))
    except json.JSONDecodeError as exc:
        raise ExamPaperImportError("Qwen 知识点识别返回不是合法 JSON。") from exc
    if not isinstance(parsed, dict):
        raise ExamPaperImportError("Qwen 知识点识别返回格式不正确。")
    return {
        "level_1": str(parsed.get("level_1") or "").strip(),
        "level_2": str(parsed.get("level_2") or "").strip(),
        "level_3": str(parsed.get("level_3") or "").strip(),
    }


def generate_ai_analysis_for_bank_question(bank_question: ExamQuestionBankQuestion) -> str:
    return _request_qwen_analysis(
        prompt=_build_bank_question_analysis_prompt(bank_question),
        image_paths=_media_image_paths_for_bank_question(bank_question),
    )


def generate_ai_analysis_for_question(question: ExamQuestion) -> str:
    return _request_qwen_analysis(
        prompt=_build_ai_analysis_prompt(question),
        image_paths=_media_image_paths_for_question(question),
    )


def ensure_ai_analysis_block_for_question(question: ExamQuestion) -> ExamQuestionAnalysisBlock | None:
    if question.analysis_blocks.filter(source_type=ExamQuestionAnalysisBlock.SOURCE_AI, is_visible=True).exists():
        sync_legacy_question_analysis(question)
        return None
    try:
        content_md = generate_ai_analysis_for_question(question)
    except Exception:
        content_md = AI_ANALYSIS_FALLBACK_MD
    block = ExamQuestionAnalysisBlock.objects.create(
        question=question,
        source_type=ExamQuestionAnalysisBlock.SOURCE_AI,
        content_md=content_md,
        sort_order=10,
    )
    sync_legacy_question_analysis(question)
    return block
