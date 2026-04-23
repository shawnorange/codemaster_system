from __future__ import annotations

from typing import Any

from django.db.utils import OperationalError, ProgrammingError

from .gesp2_catalog import ASCII_CHAR_ENCODING_CONTENT_SLUG, ENUMERATION_METHOD_CONTENT_SLUG
from .gesp4_catalog import ARRAY_2D_CONTENT_SLUG
from .manual_overrides import build_manual_override_view_data
from .models import Question


QUESTION_PAYLOAD_KEYS = (
    "statement",
    "options",
    "code",
    "answer_label",
    "answer_analysis",
    "input_format",
    "output_format",
    "sample_input",
    "sample_output",
    "difficulty",
    "ability_point",
)


def serialize_question(question: Question) -> dict[str, Any]:
    payload = dict(question.payload or {})
    serialized = {
        "id": question.id,
        "code": question.code,
        "content_slug": question.content_slug,
        "level_code": question.level_code,
        "question_type": question.question_type,
        "source_year": question.source_year,
        "source_month": question.source_month,
        "source_question_no": question.source_question_no,
        "title": question.title,
        "sort_order": question.sort_order,
        "is_active": question.is_active,
        "is_demo": question.is_demo,
        "payload": payload,
        "manual_override": build_manual_override_view_data(payload.get("manual_override")),
    }
    for key in QUESTION_PAYLOAD_KEYS:
        serialized[key] = payload.get(key)
    return serialized


def list_questions(
    *,
    content_slug: str,
    level_code: str | None = None,
    question_type: str | None = None,
    source_year: int | None = None,
    source_month: int | None = None,
    is_demo: bool | None = None,
    is_active: bool | None = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    try:
        queryset = Question.objects.filter(content_slug=content_slug)
        if level_code:
            queryset = queryset.filter(level_code=level_code)
        if question_type:
            queryset = queryset.filter(question_type=question_type)
        if source_year is not None:
            queryset = queryset.filter(source_year=source_year)
        if source_month is not None:
            queryset = queryset.filter(source_month=source_month)
        if is_demo is not None:
            queryset = queryset.filter(is_demo=is_demo)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)
        if limit is not None:
            queryset = queryset[:limit]
        return [serialize_question(question) for question in queryset]
    except (OperationalError, ProgrammingError):
        return []


def list_enumeration_method_questions(
    *,
    question_type: str | None = None,
    source_year: int | None = None,
    source_month: int | None = None,
    is_demo: bool | None = None,
    is_active: bool | None = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return list_questions(
        content_slug=ENUMERATION_METHOD_CONTENT_SLUG,
        level_code="GESP2",
        question_type=question_type,
        source_year=source_year,
        source_month=source_month,
        is_demo=is_demo,
        is_active=is_active,
        limit=limit,
    )


def list_ascii_char_encoding_questions(
    *,
    question_type: str | None = None,
    source_year: int | None = None,
    source_month: int | None = None,
    is_demo: bool | None = None,
    is_active: bool | None = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return list_questions(
        content_slug=ASCII_CHAR_ENCODING_CONTENT_SLUG,
        level_code="GESP2",
        question_type=question_type,
        source_year=source_year,
        source_month=source_month,
        is_demo=is_demo,
        is_active=is_active,
        limit=limit,
    )


def list_array_2d_questions(
    *,
    question_type: str | None = None,
    source_year: int | None = None,
    source_month: int | None = None,
    is_demo: bool | None = None,
    is_active: bool | None = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return list_questions(
        content_slug=ARRAY_2D_CONTENT_SLUG,
        level_code="GESP4",
        question_type=question_type,
        source_year=source_year,
        source_month=source_month,
        is_demo=is_demo,
        is_active=is_active,
        limit=limit,
    )
