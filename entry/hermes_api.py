from __future__ import annotations

import hmac
import json
import re
from datetime import date
from functools import wraps
from typing import Any

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Student, StudentOjWeeklyStat


DEFAULT_OJ_SOURCE = "dashima-oj"
OJ_USERNAME_DISPLAY_NAME_OVERRIDES = {
    "steven": "侯曦",
}
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HermesPayloadError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def hermes_json_error(error_code: str, message: str, *, status: int) -> JsonResponse:
    return JsonResponse(
        {
            "ok": False,
            "error_code": error_code,
            "message": message,
        },
        status=status,
    )


def hermes_ingest_required(view_func):
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any):
        configured_token = str(getattr(settings, "HERMES_INGEST_TOKEN", "") or "").strip()
        if not configured_token:
            return hermes_json_error(
                "hermes_ingest_not_configured",
                "HERMES_INGEST_TOKEN is not configured",
                status=503,
            )

        raw_authorization = str(
            request.headers.get("Authorization")
            or request.META.get("HTTP_AUTHORIZATION")
            or ""
        ).strip()
        if not raw_authorization:
            return hermes_json_error(
                "missing_authorization",
                "Authorization header is required",
                status=401,
            )

        scheme, _, supplied_token = raw_authorization.partition(" ")
        if scheme.lower() != "bearer" or not supplied_token.strip():
            return hermes_json_error(
                "invalid_authorization",
                "Authorization must be Bearer token",
                status=401,
            )

        if not hmac.compare_digest(supplied_token.strip(), configured_token):
            return hermes_json_error(
                "invalid_token",
                "Invalid Hermes ingest token",
                status=401,
            )

        return view_func(request, *args, **kwargs)

    return wrapped


def _parse_json_body(request: HttpRequest) -> dict[str, Any]:
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HermesPayloadError("request body must be valid JSON") from exc

    if not isinstance(payload, dict):
        raise HermesPayloadError("request body must be a JSON object")
    return payload


def _parse_iso_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str) or not ISO_DATE_PATTERN.fullmatch(value):
        raise HermesPayloadError(f"{field_name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HermesPayloadError(f"{field_name} must be YYYY-MM-DD") from exc


def _parse_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HermesPayloadError(f"{field_name} must be a non-negative integer")
    return value


def _normalize_import_items(payload_items: Any) -> list[dict[str, Any]]:
    if not isinstance(payload_items, list):
        raise HermesPayloadError("items must be a list")

    normalized_items = []
    for index, item in enumerate(payload_items):
        if not isinstance(item, dict):
            raise HermesPayloadError(f"items[{index}] must be an object")

        display_name = str(item.get("display_name") or "").strip()
        if not display_name:
            raise HermesPayloadError(f"items[{index}].display_name is required")

        submission_count = _parse_non_negative_int(
            item.get("submission_count"),
            f"items[{index}].submission_count",
        )
        accepted_count = _parse_non_negative_int(
            item.get("accepted_count"),
            f"items[{index}].accepted_count",
        )
        if accepted_count > submission_count:
            raise HermesPayloadError(f"items[{index}].accepted_count cannot exceed submission_count")

        raw_record_count = _parse_non_negative_int(
            item.get("raw_record_count", 0),
            f"items[{index}].raw_record_count",
        )
        oj_username = str(item.get("oj_username") or "").strip()
        normalized_display_name = OJ_USERNAME_DISPLAY_NAME_OVERRIDES.get(oj_username, display_name)

        normalized_items.append(
            {
                "oj_username": oj_username,
                "display_name": normalized_display_name,
                "original_display_name": display_name,
                "submission_count": submission_count,
                "accepted_count": accepted_count,
                "raw_record_count": raw_record_count,
            }
        )
    return normalized_items


def _build_item_reference(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "oj_username": item["oj_username"],
        "display_name": item["display_name"],
        "original_display_name": item["original_display_name"],
        "submission_count": item["submission_count"],
        "accepted_count": item["accepted_count"],
        "raw_record_count": item["raw_record_count"],
    }


@csrf_exempt
@hermes_ingest_required
def api_hermes_oj_weekly_stats_import(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return hermes_json_error(
            "method_not_allowed",
            "Only POST is allowed",
            status=405,
        )

    try:
        payload = _parse_json_body(request)
        source = str(payload.get("source") or DEFAULT_OJ_SOURCE).strip() or DEFAULT_OJ_SOURCE
        week_start = _parse_iso_date(payload.get("week_start"), "week_start")
        week_end = _parse_iso_date(payload.get("week_end"), "week_end")
        if week_end < week_start:
            raise HermesPayloadError("week_end must be greater than or equal to week_start")
        normalized_items = _normalize_import_items(payload.get("items"))
    except HermesPayloadError as exc:
        return hermes_json_error("invalid_payload", exc.message, status=400)

    created_count = 0
    updated_count = 0
    matched_students = 0
    total_submission_count = 0
    total_accepted_count = 0
    unmatched_items = []
    ambiguous_items = []
    synced_at = timezone.now()

    for item in normalized_items:
        matched_student_ids = list(
            Student.objects.filter(display_name=item["display_name"])
            .order_by("id")
            .values_list("id", flat=True)
        )
        if not matched_student_ids:
            unmatched_items.append(_build_item_reference(item))
            continue
        if len(matched_student_ids) > 1:
            ambiguous_item = _build_item_reference(item)
            ambiguous_item["matched_student_ids"] = matched_student_ids
            ambiguous_items.append(ambiguous_item)
            continue

        student_id = matched_student_ids[0]
        _, created = StudentOjWeeklyStat.objects.update_or_create(
            student_id=student_id,
            source=source,
            week_start=week_start,
            defaults={
                "week_end": week_end,
                "oj_username": item["oj_username"],
                "submission_count": item["submission_count"],
                "accepted_count": item["accepted_count"],
                "raw_record_count": item["raw_record_count"],
                "synced_at": synced_at,
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1
        matched_students += 1
        total_submission_count += item["submission_count"]
        total_accepted_count += item["accepted_count"]

    return JsonResponse(
        {
            "ok": True,
            "source": source,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "created_count": created_count,
            "updated_count": updated_count,
            "matched_students": matched_students,
            "unmatched_items": unmatched_items,
            "ambiguous_items": ambiguous_items,
            "total_submission_count": total_submission_count,
            "total_accepted_count": total_accepted_count,
        }
    )


def _serialize_oj_weekly_stat(stat: StudentOjWeeklyStat) -> dict[str, Any]:
    return {
        "student_id": stat.student_id,
        "display_name": stat.student.display_name,
        "oj_username": stat.oj_username,
        "source": stat.source,
        "week_start": stat.week_start.isoformat(),
        "week_end": stat.week_end.isoformat(),
        "submission_count": stat.submission_count,
        "accepted_count": stat.accepted_count,
        "synced_at": timezone.localtime(stat.synced_at).isoformat() if stat.synced_at else None,
    }


@csrf_exempt
@hermes_ingest_required
def api_hermes_oj_weekly_stats(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return hermes_json_error(
            "method_not_allowed",
            "Only GET is allowed",
            status=405,
        )

    queryset = StudentOjWeeklyStat.objects.select_related("student").order_by(
        "week_start",
        "student_id",
        "source",
    )

    source = str(request.GET.get("source") or "").strip()
    if source:
        queryset = queryset.filter(source=source)

    raw_week_start = request.GET.get("week_start")
    if raw_week_start:
        try:
            week_start = _parse_iso_date(raw_week_start, "week_start")
        except HermesPayloadError as exc:
            return hermes_json_error("invalid_query", exc.message, status=400)
        queryset = queryset.filter(week_start=week_start)

    raw_student_id = request.GET.get("student_id")
    if raw_student_id:
        try:
            student_id = int(raw_student_id)
        except (TypeError, ValueError):
            return hermes_json_error("invalid_query", "student_id must be an integer", status=400)
        queryset = queryset.filter(student_id=student_id)

    results = [_serialize_oj_weekly_stat(stat) for stat in queryset]
    return JsonResponse(
        {
            "ok": True,
            "count": len(results),
            "results": results,
        }
    )
