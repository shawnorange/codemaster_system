from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .manual_overrides import build_manual_override_view_data

from .gesp2_catalog import ENUMERATION_METHOD_CONTENT_SLUG
from .gesp4_catalog import ARRAY_2D_CONTENT_SLUG


QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "judgement": "判断题",
    "programming": "编程题",
}

_SOURCE_YEAR_MONTH_PATTERN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
_QUESTION_NO_PATTERN = re.compile(r"第?\s*(\d+)\s*题")


def _parse_source_year_month(source: Any) -> tuple[int | None, int | None]:
    if not source:
        return None, None

    matched = _SOURCE_YEAR_MONTH_PATTERN.search(str(source))
    if not matched:
        return None, None

    return int(matched.group(1)), int(matched.group(2))


def _parse_source_question_no(question_no: Any) -> int | None:
    if question_no is None:
        return None

    matched = _QUESTION_NO_PATTERN.search(str(question_no))
    if matched:
        return int(matched.group(1))

    raw_value = str(question_no).strip()
    if raw_value.isdigit():
        return int(raw_value)
    return None


def _format_source_label(source_year: int | None, source_month: int | None) -> str:
    if source_year is None or source_month is None:
        return ""
    return f"{source_year} 年 {source_month} 月"


def _format_question_no(source_question_no: int | None) -> str:
    if source_question_no is None:
        return ""
    return f"第 {source_question_no} 题"


def _normalize_question_type_label(question_type: str | None) -> str:
    if not question_type:
        return ""
    return QUESTION_TYPE_LABELS.get(question_type, question_type)


def _normalize_string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _split_text_blocks(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return parts or [text]


def _normalize_enumeration_question_type(value: Any) -> str:
    label = str(value or "").strip()
    if "编程" in label:
        return "programming"
    if "判断" in label:
        return "judgement"
    return "single_choice"


def _build_enumeration_code(
    *,
    source_year: int | None,
    source_month: int | None,
    source_question_no: int | None,
    fallback_question_id: str,
) -> str:
    if source_year is not None and source_month is not None and source_question_no is not None:
        return f"gesp2-enumeration-{source_year}-{source_month:02d}-q{source_question_no:02d}"
    suffix = fallback_question_id.replace("question-", "q")
    return f"gesp2-enumeration-{suffix}"


def _build_array_2d_code(question: dict[str, Any]) -> str:
    year = int(question.get("year") or 0)
    month = int(question.get("month") or 0)
    question_number = int(question.get("questionNumber") or 0)
    question_type = str(question.get("type") or "single_choice").replace("_", "-")
    return f"gesp4-array-2d-{year}-{month:02d}-{question_type}-q{question_number:02d}"


def build_enumeration_static_import_records(
    site_data: dict[str, Any],
    *,
    demo_question_titles: set[str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    demo_question_titles = demo_question_titles or set()
    seen_codes: set[str] = set()

    for question_index, group in enumerate(site_data.get("ability_groups", []), start=1):
        for item_index, question in enumerate(group.get("questions", []), start=1):
            fallback_question_id = str(question.get("question_id") or f"question-{len(records) + 1:02d}")
            source_year, source_month = _parse_source_year_month(question.get("source"))
            source_question_no = _parse_source_question_no(question.get("question_no"))
            code = _build_enumeration_code(
                source_year=source_year,
                source_month=source_month,
                source_question_no=source_question_no,
                fallback_question_id=fallback_question_id,
            )
            if code in seen_codes:
                code = f"{code}-card-{fallback_question_id.replace('question-', '')}"
            seen_codes.add(code)

            payload = {
                "question_id": fallback_question_id,
                "question_ref": question.get("question_ref"),
                "source": question.get("source"),
                "question_no": question.get("question_no"),
                "question_type_label": question.get("question_type"),
                "prompt": question.get("prompt"),
                "statement": _normalize_string_list(question.get("statement") or question.get("prompt")),
                "options": _normalize_string_list(question.get("options")),
                "code": question.get("code"),
                "answer_label": question.get("answer_label"),
                "answer_analysis": _normalize_string_list(question.get("answer_analysis")),
                "input_format": question.get("input_format"),
                "output_format": question.get("output_format"),
                "sample_input": question.get("sample_input"),
                "sample_output": question.get("sample_output"),
                "difficulty": question.get("difficulty"),
                "ability_point": question.get("ability_point"),
                "classification_reason": question.get("classification_reason"),
                "pitfall": question.get("pitfall"),
                "template_ref": question.get("template_ref"),
                "teacher_prompt": question.get("teacher_prompt"),
                "answer": question.get("answer"),
                "group_id": group.get("id"),
                "group_title": group.get("title"),
            }

            records.append(
                {
                    "code": code,
                    "content_slug": ENUMERATION_METHOD_CONTENT_SLUG,
                    "level_code": "GESP2",
                    "question_type": _normalize_enumeration_question_type(question.get("question_type")),
                    "source_year": source_year,
                    "source_month": source_month,
                    "source_question_no": source_question_no,
                    "title": question.get("title") or "",
                    "payload": payload,
                    "sort_order": (question_index - 1) * 100 + item_index * 10,
                    "is_active": True,
                    "is_demo": (question.get("title") or "") in demo_question_titles,
                }
            )

    return records


def build_array_2d_static_import_records(site_data: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for index, question in enumerate(site_data.get("questions", []), start=1):
        payload = {
            "question_id": question.get("id"),
            "lecture_id": question.get("lectureId"),
            "topic_id": question.get("topicId"),
            "paper": question.get("paper"),
            "paper_label": question.get("paperLabel"),
            "paper_key": question.get("paperKey"),
            "question_number": question.get("questionNumber"),
            "summary": question.get("summary"),
            "statement": _split_text_blocks(question.get("questionText")),
            "question_text": question.get("questionText"),
            "raw_question_text": question.get("rawQuestionText"),
            "answer_label": "参考答案",
            "answer_text": question.get("answerText"),
            "answer_analysis": _split_text_blocks(question.get("analysisText")),
            "analysis_text": question.get("analysisText"),
            "study_guide": question.get("studyGuide"),
            "reference_code": question.get("referenceCode"),
            "classification_reason": question.get("classificationReason"),
            "pitfall": question.get("pitfall"),
            "content_status": question.get("contentStatus"),
            "source_path": question.get("sourcePath"),
        }

        records.append(
            {
                "code": _build_array_2d_code(question),
                "content_slug": ARRAY_2D_CONTENT_SLUG,
                "level_code": "GESP4",
                "question_type": str(question.get("type") or "single_choice"),
                "source_year": question.get("year"),
                "source_month": question.get("month"),
                "source_question_no": question.get("questionNumber"),
                "title": question.get("title") or "",
                "payload": payload,
                "sort_order": index * 10,
                "is_active": True,
                "is_demo": False,
            }
        )

    return records


def merge_enumeration_questions_into_site_data(
    site_data: dict[str, Any],
    db_questions: list[dict[str, Any]],
    *,
    demo_question_titles: set[str] | None = None,
) -> dict[str, Any]:
    merged_site_data = deepcopy(site_data)
    static_records = build_enumeration_static_import_records(
        merged_site_data,
        demo_question_titles=demo_question_titles,
    )
    static_records_by_question_id = {
        record["payload"].get("question_id"): record
        for record in static_records
        if record.get("payload", {}).get("question_id")
    }
    db_questions_by_code = {question["code"]: question for question in db_questions}
    consumed_db_codes: set[str] = set()

    group_index = {group.get("id"): group for group in merged_site_data.get("ability_groups", [])}
    for group in merged_site_data.get("ability_groups", []):
        updated_questions: list[dict[str, Any]] = []
        for fallback_question in group.get("questions", []):
            question_id = fallback_question.get("question_id")
            static_record = static_records_by_question_id.get(question_id)
            if static_record is None:
                updated_questions.append(deepcopy(fallback_question))
                continue

            resolved_record = db_questions_by_code.get(static_record["code"], static_record)
            if resolved_record is not static_record:
                consumed_db_codes.add(resolved_record["code"])
            updated_questions.append(
                _enumeration_record_to_page_question(
                    resolved_record,
                    fallback_question=fallback_question,
                )
            )
        group["questions"] = updated_questions

    for question in db_questions:
        if question["code"] in consumed_db_codes:
            continue

        payload = question.get("payload") or {}
        group = group_index.get(payload.get("group_id"))
        if group is None:
            continue
        group.setdefault("questions", []).append(_enumeration_record_to_page_question(question))

    total_questions = 0
    question_lookup: dict[str, dict[str, Any]] = {}
    for group in merged_site_data.get("ability_groups", []):
        total_questions += len(group.get("questions", []))
        for question in group.get("questions", []):
            title = question.get("title")
            if title:
                question_lookup[title] = deepcopy(question)

    for card in merged_site_data.get("summary_cards", []):
        if card.get("label") == "代表题目":
            card["value"] = f"{total_questions} 道"
            break

    merged_site_data["question_lookup"] = question_lookup
    for module in merged_site_data.get("demo_modules", []):
        related_titles = list(module.get("related_question_titles") or module.get("related_questions") or [])
        module["related_question_titles"] = related_titles
        module["related_questions"] = related_titles
        module["related_question_cards"] = [
            deepcopy(question_lookup[title])
            for title in related_titles
            if title in question_lookup
        ]

    return merged_site_data


def build_enumeration_main_question_groups(
    site_data: dict[str, Any],
    db_questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not db_questions:
        return deepcopy(site_data.get("ability_groups", []))

    static_group_map = {
        group.get("id"): group
        for group in site_data.get("ability_groups", [])
    }
    grouped_questions: dict[str, dict[str, Any]] = {}

    for question in sorted(
        db_questions,
        key=lambda item: (item.get("sort_order", 0), item.get("id") or 0),
    ):
        payload = dict(question.get("payload") or {})
        group_id = str(payload.get("group_id") or "db-ungrouped")
        static_group = static_group_map.get(group_id, {})
        group = grouped_questions.setdefault(
            group_id,
            {
                "id": group_id,
                "title": payload.get("group_title") or static_group.get("title") or "未分组题目",
                "summary": static_group.get("summary") or "当前题组由 questions 表中的题目直接驱动。",
                "goal": static_group.get("goal") or "当前题组使用数据库查询结果。",
                "questions": [],
            },
        )
        group["questions"].append(_enumeration_record_to_page_question(question))

    ordered_groups: list[dict[str, Any]] = []
    for static_group in site_data.get("ability_groups", []):
        group_id = static_group.get("id")
        if group_id in grouped_questions:
            ordered_groups.append(grouped_questions.pop(group_id))

    ordered_groups.extend(grouped_questions.values())
    return ordered_groups


def build_enumeration_question_lookup(
    question_groups: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    question_lookup: dict[str, dict[str, Any]] = {}
    for group in question_groups:
        for question in group.get("questions", []):
            title = question.get("title")
            if title:
                question_lookup[title] = deepcopy(question)
    return question_lookup


def build_enumeration_demo_modules(
    demo_modules: list[dict[str, Any]],
    question_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    copied_modules = deepcopy(demo_modules)
    for module in copied_modules:
        related_titles = list(module.get("related_question_titles") or module.get("related_questions") or [])
        module["related_question_titles"] = related_titles
        module["related_questions"] = related_titles
        module["related_question_cards"] = [
            deepcopy(question_lookup[title])
            for title in related_titles
            if title in question_lookup
        ]
    return copied_modules


def merge_array_2d_questions_into_site_data(
    site_data: dict[str, Any],
    db_questions: list[dict[str, Any]],
) -> dict[str, Any]:
    merged_site_data = deepcopy(site_data)
    static_records = build_array_2d_static_import_records(merged_site_data)
    static_records_by_question_id = {
        record["payload"].get("question_id"): record
        for record in static_records
        if record.get("payload", {}).get("question_id")
    }
    db_questions_by_code = {question["code"]: question for question in db_questions}
    consumed_db_codes: set[str] = set()

    merged_questions: list[dict[str, Any]] = []
    existing_question_ids: set[str] = set()
    for fallback_question in merged_site_data.get("questions", []):
        question_id = fallback_question.get("id")
        static_record = static_records_by_question_id.get(question_id)
        if static_record is None:
            merged_question = deepcopy(fallback_question)
        else:
            resolved_record = db_questions_by_code.get(static_record["code"], static_record)
            if resolved_record is not static_record:
                consumed_db_codes.add(resolved_record["code"])
            merged_question = _array_record_to_page_question(
                resolved_record,
                fallback_question=fallback_question,
            )
        existing_question_ids.add(merged_question["id"])
        merged_questions.append(merged_question)

    for question in db_questions:
        if question["code"] in consumed_db_codes:
            continue

        extra_question = _array_record_to_page_question(question)
        if extra_question["id"] in existing_question_ids:
            continue
        existing_question_ids.add(extra_question["id"])
        merged_questions.append(extra_question)
        _attach_question_to_array_topic(merged_site_data, extra_question)

    merged_site_data["questions"] = merged_questions
    return merged_site_data


def build_array_2d_db_site_data(
    site_data: dict[str, Any],
    db_questions: list[dict[str, Any]],
) -> dict[str, Any]:
    if not db_questions:
        return deepcopy(site_data)

    db_site_data = deepcopy(site_data)
    for lecture in db_site_data.get("lectures", []):
        for section in lecture.get("sections", []):
            for child in section.get("children", []):
                if "questionIds" in child:
                    child["questionIds"] = []

    db_page_questions = [
        _array_record_to_page_question(question)
        for question in sorted(
            db_questions,
            key=lambda item: (item.get("sort_order", 0), item.get("id") or 0),
        )
    ]
    for question in db_page_questions:
        _attach_question_to_array_topic(db_site_data, question)

    db_site_data["questions"] = db_page_questions
    return db_site_data


def _enumeration_record_to_page_question(
    record: dict[str, Any],
    *,
    fallback_question: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    question = deepcopy(fallback_question) if fallback_question else {}
    question_id = payload.get("question_id") or question.get("question_id")

    question.update(
        {
            "id": record.get("id"),
            "code": record.get("code") or question.get("code"),
            "content_slug": record.get("content_slug") or question.get("content_slug"),
            "sort_order": record.get("sort_order", question.get("sort_order", 0)),
            "is_demo": bool(record.get("is_demo")),
            "source": payload.get("source") or question.get("source") or _format_source_label(
                record.get("source_year"),
                record.get("source_month"),
            ),
            "question_type": payload.get("question_type_label")
            or question.get("question_type")
            or _normalize_question_type_label(record.get("question_type")),
            "question_no": payload.get("question_no")
            or question.get("question_no")
            or _format_question_no(record.get("source_question_no")),
            "difficulty": payload.get("difficulty") or question.get("difficulty"),
            "title": record.get("title") or question.get("title") or "",
            "prompt": payload.get("prompt") or question.get("prompt"),
            "ability_point": payload.get("ability_point") or question.get("ability_point"),
            "classification_reason": payload.get("classification_reason") or question.get("classification_reason"),
            "pitfall": payload.get("pitfall") or question.get("pitfall"),
            "template_ref": payload.get("template_ref") or question.get("template_ref"),
            "teacher_prompt": payload.get("teacher_prompt") or question.get("teacher_prompt"),
            "answer": payload.get("answer") or question.get("answer"),
            "statement": _normalize_string_list(payload.get("statement") or question.get("statement") or question.get("prompt")),
            "options": _normalize_string_list(payload.get("options") or question.get("options")),
            "code": payload.get("code") if payload.get("code") is not None else record.get("code") or question.get("code"),
            "answer_label": payload.get("answer_label") or question.get("answer_label"),
            "answer_analysis": _normalize_string_list(payload.get("answer_analysis") or question.get("answer_analysis")),
            "input_format": payload.get("input_format") or question.get("input_format"),
            "output_format": payload.get("output_format") or question.get("output_format"),
            "sample_input": payload.get("sample_input") or question.get("sample_input"),
            "sample_output": payload.get("sample_output") or question.get("sample_output"),
            "question_id": question_id,
            "question_ref": payload.get("question_ref") or question.get("question_ref"),
            "manual_override": build_manual_override_view_data(payload.get("manual_override")),
        }
    )

    if question_id and not question.get("question_ref") and question.get("source") and question.get("question_no"):
        question["question_ref"] = f"{question['source']} {question['question_no']}"

    return question


def _array_record_to_page_question(
    record: dict[str, Any],
    *,
    fallback_question: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    question = deepcopy(fallback_question) if fallback_question else {}

    question_id = payload.get("question_id") or question.get("id") or record.get("code")
    question_text = payload.get("question_text") or question.get("questionText") or ""
    answer_text = payload.get("answer_text") or question.get("answerText") or payload.get("answer_label") or ""

    question.update(
        {
            "id": question_id,
            "lectureId": payload.get("lecture_id") or question.get("lectureId"),
            "topicId": payload.get("topic_id") or question.get("topicId"),
            "paper": payload.get("paper") or question.get("paper"),
            "paperLabel": payload.get("paper_label")
            or question.get("paperLabel")
            or _format_source_label(record.get("source_year"), record.get("source_month")),
            "paperKey": payload.get("paper_key")
            or question.get("paperKey")
            or (
                f"{record['source_year']}-{int(record['source_month']):02d}"
                if record.get("source_year") is not None and record.get("source_month") is not None
                else ""
            ),
            "year": record.get("source_year") or question.get("year"),
            "month": record.get("source_month") or question.get("month"),
            "type": record.get("question_type") or question.get("type") or "single_choice",
            "questionNumber": payload.get("question_number")
            or record.get("source_question_no")
            or question.get("questionNumber"),
            "title": record.get("title") or question.get("title") or "",
            "summary": payload.get("summary") or question.get("summary") or "",
            "classificationReason": payload.get("classification_reason")
            or question.get("classificationReason")
            or "",
            "pitfall": payload.get("pitfall") or question.get("pitfall") or "",
            "questionText": question_text,
            "rawQuestionText": payload.get("raw_question_text") or question.get("rawQuestionText") or question_text,
            "answerText": answer_text,
            "analysisText": payload.get("analysis_text")
            or question.get("analysisText")
            or "\n\n".join(_normalize_string_list(payload.get("answer_analysis"))),
            "studyGuide": payload.get("study_guide") or question.get("studyGuide"),
            "referenceCode": payload.get("reference_code") or question.get("referenceCode"),
            "contentStatus": payload.get("content_status") or question.get("contentStatus") or "manual",
            "sourcePath": payload.get("source_path") or question.get("sourcePath"),
        }
    )

    return question


def _attach_question_to_array_topic(site_data: dict[str, Any], question: dict[str, Any]) -> None:
    lecture_id = question.get("lectureId")
    topic_id = question.get("topicId")
    if not lecture_id or not topic_id:
        return

    for lecture in site_data.get("lectures", []):
        if lecture.get("id") != lecture_id:
            continue
        for section in lecture.get("sections", []):
            for child in section.get("children", []):
                if child.get("id") != topic_id:
                    continue
                question_ids = child.setdefault("questionIds", [])
                if question["id"] not in question_ids:
                    question_ids.append(question["id"])
                return
