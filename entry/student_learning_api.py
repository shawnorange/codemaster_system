from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from django.db.models import Count, Prefetch, Q, QuerySet
from django.utils import timezone

from .homework_completion_stats import (
    get_homework_submission_effective_submitted_at,
    get_homework_due_localdate,
    get_latest_completed_submission,
    resolve_homework_completion_period_dates,
    resolve_homework_due_datetime_range,
)
from .models import HomeworkAssignment, HomeworkSubmission, PortalUser, Student


PERIOD_TYPES = ("week", "month", "quarter")
COMPLETION_STATUS_COMPLETED = "completed"
COMPLETION_STATUS_DELAYED_COMPLETED = "delayed_completed"
COMPLETION_STATUS_INCOMPLETE = "incomplete"
MASTERY_STATUS_MASTERED = "已掌握"
MASTERY_STATUS_BASIC = "基本掌握"
MASTERY_STATUS_NOT_MASTERED = "未掌握"
MASTERY_STATUS_NOT_ANSWERED = "未作答"
KNOWLEDGE_POINT_SOURCE_TITLE = "HomeworkAssignment.title"


def normalize_student_learning_anchor_date(raw_anchor_date: str | None) -> date:
    if not str(raw_anchor_date or "").strip():
        return timezone.localdate()
    try:
        return date.fromisoformat(str(raw_anchor_date).strip())
    except ValueError as exc:
        raise ValueError("anchor_date 参数无效，应为 YYYY-MM-DD。") from exc


def resolve_student_learning_periods(anchor_date: date) -> dict[str, dict[str, date]]:
    return {
        period_type: {
            "start": period_start,
            "end": period_end,
        }
        for period_type in PERIOD_TYPES
        for period_start, period_end, _ in [resolve_homework_completion_period_dates(period_type, anchor_date=anchor_date)]
    }


def build_student_learning_overview(
    *,
    students: QuerySet[Student] | Iterable[Student],
    anchor_date: date,
    include_teacher_fields: bool = True,
    teacher: PortalUser | None = None,
    use_assignment_status_completion: bool = True,
) -> dict[str, object]:
    student_list = list(
        students.select_related("teacher_user").order_by("id")
        if isinstance(students, QuerySet)
        else students
    )
    periods = resolve_student_learning_periods(anchor_date)
    student_rows_by_id = {
        student.id: _build_student_learning_row(student=student, include_teacher_fields=include_teacher_fields)
        for student in student_list
    }
    student_ids = list(student_rows_by_id.keys())
    if not student_ids:
        return {
            "anchor_date": anchor_date.isoformat(),
            "periods": _serialize_periods(periods),
            "students": [],
        }

    earliest_start = min(bounds["start"] for bounds in periods.values())
    latest_end = max(bounds["end"] for bounds in periods.values())
    earliest_start_at, latest_end_at = resolve_homework_due_datetime_range(earliest_start, latest_end)
    assignment_queryset = (
        HomeworkAssignment.objects.select_related(
            "student",
            "teacher",
            "summary",
            "source_import_job",
        )
        .filter(
            student_id__in=student_ids,
            is_active=True,
        )
        .exclude(status=HomeworkAssignment.STATUS_CANCELLED)
    )
    if teacher is not None:
        assignment_queryset = assignment_queryset.filter(teacher=teacher)
    assignments = list(
        assignment_queryset
        .filter(
            Q(due_date__isnull=True)
            | Q(due_date__gte=earliest_start_at, due_date__lt=latest_end_at)
        )
        .annotate(
            direct_online_question_count=Count(
                "questions",
                filter=Q(questions__is_active=True),
                distinct=True,
            ),
            source_online_question_count=Count(
                "source_import_job__questions",
                filter=Q(
                    source_import_job__is_active=True,
                    source_import_job__questions__is_active=True,
                ),
                distinct=True,
            ),
        )
        .prefetch_related(
            Prefetch(
                "submissions",
                queryset=HomeworkSubmission.objects.filter(is_active=True).order_by(
                    "-submitted_at",
                    "-created_at",
                    "-id",
                ),
                to_attr="active_submissions",
            )
        )
        .order_by("due_date", "assigned_at", "id")
    )

    for assignment in assignments:
        student_row = student_rows_by_id.get(assignment.student_id)
        if student_row is None:
            continue
        if assignment.due_date is None:
            _increment_excluded_undated_counts(student_row)
            continue
        assignment_due_date = get_homework_due_localdate(assignment.due_date)
        if assignment_due_date is None:
            _increment_excluded_undated_counts(student_row)
            continue

        assignment_snapshot = evaluate_student_learning_assignment(
            assignment,
            use_assignment_status_completion=use_assignment_status_completion,
        )
        knowledge_point_item = _build_knowledge_point_item(assignment_snapshot)
        for period_type, bounds in periods.items():
            if not (bounds["start"] <= assignment_due_date <= bounds["end"]):
                continue
            _apply_assignment_snapshot_to_period(
                period_summary=student_row[period_type],
                assignment_snapshot=assignment_snapshot,
            )
            student_row["knowledge_points_by_period"][period_type].append(dict(knowledge_point_item))
            if period_type == "week":
                _append_week_lesson_feedback(
                    period_summary=student_row[period_type],
                    assignment=assignment,
                )

    for student_row in student_rows_by_id.values():
        _finalize_week_lesson_feedback_summary(student_row.get("week"))

    return {
        "anchor_date": anchor_date.isoformat(),
        "periods": _serialize_periods(periods),
        "students": [student_rows_by_id[student.id] for student in student_list],
    }


def build_single_student_learning_row(
    *,
    student: Student,
    anchor_date: date,
    include_teacher_fields: bool = True,
    teacher: PortalUser | None = None,
    use_assignment_status_completion: bool = True,
) -> dict[str, object] | None:
    payload = build_student_learning_overview(
        students=[student],
        anchor_date=anchor_date,
        include_teacher_fields=include_teacher_fields,
        teacher=teacher,
        use_assignment_status_completion=use_assignment_status_completion,
    )
    students = list(payload.get("students") or [])
    return students[0] if students else None


def build_student_week_lesson_feedback_payload(
    *,
    student: Student,
    anchor_date: date,
    include_teacher_fields: bool = True,
    teacher: PortalUser | None = None,
    use_assignment_status_completion: bool = True,
) -> dict[str, object]:
    student_row = build_single_student_learning_row(
        student=student,
        anchor_date=anchor_date,
        include_teacher_fields=include_teacher_fields,
        teacher=teacher,
        use_assignment_status_completion=use_assignment_status_completion,
    )
    periods = resolve_student_learning_periods(anchor_date)
    week_summary = dict((student_row or {}).get("week") or _build_empty_period_summary(period_type="week"))
    return {
        "anchor_date": anchor_date.isoformat(),
        "period": "week",
        "periods": _serialize_periods({"week": periods["week"]}),
        "student": {
            "student_id": int(student.id),
            "display_name": str(student.display_name or "").strip(),
            "primary_level_name": str(student.primary_level_name or "").strip(),
        },
        "week": {
            "lesson_feedbacks": list(week_summary.get("lesson_feedbacks") or []),
            "highlights": list(week_summary.get("highlights") or []),
            "areas_for_growth": list(week_summary.get("areas_for_growth") or []),
        },
    }


def get_student_week_lesson_feedback_assignments(
    *,
    student: Student,
    anchor_date: date,
    teacher: PortalUser | None = None,
) -> list[HomeworkAssignment]:
    week_start, week_end, _ = resolve_homework_completion_period_dates("week", anchor_date=anchor_date)
    week_start_at, week_end_at = resolve_homework_due_datetime_range(week_start, week_end)
    assignment_queryset = (
        HomeworkAssignment.objects.select_related("source_import_job")
        .filter(
            student=student,
            is_active=True,
            due_date__gte=week_start_at,
            due_date__lt=week_end_at,
        )
        .exclude(status=HomeworkAssignment.STATUS_CANCELLED)
        .exclude(
            source_import_job__isnull=True,
            highlights="",
            areas_for_growth="",
        )
        .order_by("-due_date", "-assigned_at", "-id")
    )
    if teacher is not None:
        assignment_queryset = assignment_queryset.filter(teacher=teacher)
    assignments = list(assignment_queryset)
    assignments.sort(key=lambda assignment: assignment.source_import_job_id is not None, reverse=True)
    return assignments


def evaluate_student_learning_assignment(
    assignment: HomeworkAssignment,
    *,
    use_assignment_status_completion: bool = True,
) -> dict[str, object]:
    submissions = [
        submission
        for submission in getattr(assignment, "active_submissions", [])
        if submission.student_id == assignment.student_id
    ]
    has_online_questions = assignment.get_effective_online_question_count() > 0
    latest_completed_submission = get_latest_completed_submission(submissions) if has_online_questions else None
    mastery_status, correct_rate = (
        _build_mastery_snapshot(latest_completed_submission)
        if has_online_questions
        else (None, None)
    )

    due_at = _normalize_assignment_due_datetime(assignment.due_date)
    if use_assignment_status_completion:
        completed_at = _normalize_optional_datetime(assignment.completed_at)
        is_completed = _assignment_status_counts_as_completed(assignment.status)
        is_delayed_completion = bool(
            is_completed
            and completed_at is not None
            and due_at is not None
            and completed_at > due_at
        )
        is_on_time_completion = bool(
            is_completed
            and completed_at is not None
            and due_at is not None
            and completed_at <= due_at
        )
    else:
        completed_at = _resolve_legacy_completion_datetime(
            assignment=assignment,
            has_online_questions=has_online_questions,
            latest_completed_submission=latest_completed_submission,
        )
        is_completed = completed_at is not None
        is_delayed_completion = bool(
            is_completed
            and completed_at is not None
            and due_at is not None
            and completed_at > due_at
        )
        is_on_time_completion = bool(
            is_completed
            and completed_at is not None
            and due_at is not None
            and completed_at <= due_at
        )

    if not is_completed:
        completion_status = COMPLETION_STATUS_INCOMPLETE
    elif is_delayed_completion:
        completion_status = COMPLETION_STATUS_DELAYED_COMPLETED
    else:
        completion_status = COMPLETION_STATUS_COMPLETED

    return {
        "assignment_id": int(assignment.id),
        "assignment_title": str(assignment.title or "").strip(),
        "due_date": due_at,
        "has_online_questions": has_online_questions,
        "assignment_status": str(assignment.status or "").strip(),
        "is_completed": is_completed,
        "is_on_time_completion": is_on_time_completion,
        "is_delayed_completion": is_delayed_completion,
        "completion_status": completion_status,
        "latest_completed_submission": latest_completed_submission,
        "completed_at": completed_at,
        "mastery_status": mastery_status,
        "correct_rate": correct_rate,
    }


def _build_student_learning_row(*, student: Student, include_teacher_fields: bool) -> dict[str, object]:
    teacher = getattr(student, "teacher_user", None)
    row = {
        "student_id": int(student.id),
        "display_name": str(student.display_name or "").strip(),
        "primary_level_name": str(student.primary_level_name or "").strip(),
        "knowledge_points_by_period": {
            period_type: []
            for period_type in PERIOD_TYPES
        },
    }
    if include_teacher_fields:
        row["teacher_id"] = int(teacher.id) if teacher is not None else None
        row["teacher_name"] = str(teacher.full_name or teacher.username).strip() if teacher is not None else ""
    for period_type in PERIOD_TYPES:
        row[period_type] = _build_empty_period_summary(period_type=period_type)
    return row


def _build_empty_period_summary(*, period_type: str) -> dict[str, object]:
    summary: dict[str, object] = {
        "assignment_count": 0,
        "completed_count": 0,
        "on_time_completed_count": 0,
        "delayed_completed_count": 0,
        "incomplete_count": 0,
        "excluded_undated_count": 0,
    }
    if period_type == "week":
        summary["lesson_feedbacks"] = []
        summary["highlights"] = []
        summary["areas_for_growth"] = []
    return summary


def _serialize_periods(periods: dict[str, dict[str, date]]) -> dict[str, dict[str, str]]:
    return {
        period_type: {
            "start": bounds["start"].isoformat(),
            "end": bounds["end"].isoformat(),
        }
        for period_type, bounds in periods.items()
    }


def _increment_excluded_undated_counts(student_row: dict[str, object]) -> None:
    for period_type in PERIOD_TYPES:
        period_summary = student_row[period_type]
        period_summary["excluded_undated_count"] = int(period_summary["excluded_undated_count"]) + 1


def _apply_assignment_snapshot_to_period(
    *,
    period_summary: dict[str, object],
    assignment_snapshot: dict[str, object],
) -> None:
    period_summary["assignment_count"] = int(period_summary["assignment_count"]) + 1
    if not bool(assignment_snapshot.get("is_completed")):
        period_summary["incomplete_count"] = int(period_summary["incomplete_count"]) + 1
        return

    period_summary["completed_count"] = int(period_summary["completed_count"]) + 1
    if bool(assignment_snapshot.get("is_delayed_completion")):
        period_summary["delayed_completed_count"] = int(period_summary["delayed_completed_count"]) + 1
    elif bool(assignment_snapshot.get("is_on_time_completion")):
        period_summary["on_time_completed_count"] = int(period_summary["on_time_completed_count"]) + 1


def _append_week_lesson_feedback(
    *,
    period_summary: dict[str, object],
    assignment: HomeworkAssignment,
) -> None:
    highlights = str(getattr(assignment, "highlights", "") or "").strip()
    areas_for_growth = str(getattr(assignment, "areas_for_growth", "") or "").strip()
    if assignment.source_import_job_id is None and not highlights and not areas_for_growth:
        return
    assignment_due_at = _normalize_assignment_due_datetime(assignment.due_date)
    assignment_due_date = get_homework_due_localdate(assignment_due_at)
    if assignment_due_date is None:
        return

    lesson_feedbacks = period_summary.get("lesson_feedbacks")
    if not isinstance(lesson_feedbacks, list):
        return
    serialized_due_at = _serialize_due_datetime_value(assignment_due_at)

    lesson_feedbacks.append(
        {
            "assignment_id": int(assignment.id),
            "title": str(assignment.title or "").strip(),
            "due_date": serialized_due_at,
            "source_import_job_id": int(assignment.source_import_job_id) if assignment.source_import_job_id else None,
            "highlights": highlights,
            "areas_for_growth": areas_for_growth,
            "_sort_has_source": 1 if assignment.source_import_job_id else 0,
            "_sort_due_date": serialized_due_at,
            "_sort_assigned_at": assignment.assigned_at.isoformat() if assignment.assigned_at is not None else "",
            "_sort_id": int(assignment.id),
        }
    )

    _append_unique_non_empty_text(period_summary.get("highlights"), highlights)
    _append_unique_non_empty_text(period_summary.get("areas_for_growth"), areas_for_growth)


def _finalize_week_lesson_feedback_summary(period_summary: object) -> None:
    if not isinstance(period_summary, dict):
        return
    lesson_feedbacks = period_summary.get("lesson_feedbacks")
    if not isinstance(lesson_feedbacks, list):
        return

    lesson_feedbacks.sort(
        key=lambda item: (
            int(item.get("_sort_has_source") or 0),
            str(item.get("_sort_due_date") or ""),
            str(item.get("_sort_assigned_at") or ""),
            int(item.get("_sort_id") or 0),
        ),
        reverse=True,
    )
    for item in lesson_feedbacks:
        item.pop("_sort_has_source", None)
        item.pop("_sort_due_date", None)
        item.pop("_sort_assigned_at", None)
        item.pop("_sort_id", None)


def _build_knowledge_point_item(assignment_snapshot: dict[str, object]) -> dict[str, object]:
    correct_rate = assignment_snapshot["correct_rate"]
    return {
        "name": str(assignment_snapshot["assignment_title"]),
        "source": KNOWLEDGE_POINT_SOURCE_TITLE,
        "mastery_status": assignment_snapshot["mastery_status"],
        "correct_rate": correct_rate,
        "correct_rate_text": _format_fraction_as_percent(correct_rate) if correct_rate is not None else "暂无",
        "assignment_id": int(assignment_snapshot["assignment_id"]),
    }


def _append_unique_non_empty_text(target: object, text: str) -> None:
    normalized = str(text or "").strip()
    if not normalized or not isinstance(target, list):
        return
    if normalized not in target:
        target.append(normalized)


def _build_mastery_snapshot(
    latest_completed_submission: HomeworkSubmission | None,
) -> tuple[str | None, float | None]:
    if latest_completed_submission is None:
        return MASTERY_STATUS_NOT_ANSWERED, None

    total_count = max(int(latest_completed_submission.total_count or 0), 0)
    correct_count = max(int(latest_completed_submission.correct_count or 0), 0)
    wrong_count = max(int(latest_completed_submission.wrong_count or 0), 0)
    if total_count <= 0:
        return MASTERY_STATUS_NOT_MASTERED, None

    correct_count = min(correct_count, total_count)
    wrong_count = min(wrong_count, total_count)
    error_rate = round(wrong_count / total_count, 4)
    correct_rate = round(correct_count / total_count, 4)
    if error_rate == 0:
        return MASTERY_STATUS_MASTERED, correct_rate
    if error_rate <= 0.3:
        return MASTERY_STATUS_BASIC, correct_rate
    return MASTERY_STATUS_NOT_MASTERED, correct_rate


def _format_fraction_as_percent(value: object) -> str:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return "暂无"
    return f"{round(max(numeric_value, 0.0) * 100, 1):g}%"


def _normalize_assignment_due_datetime(value: date | datetime | None) -> datetime | None:
    return HomeworkAssignment.normalize_due_date_value(value)


def _serialize_due_datetime_value(value: date | datetime | None) -> str:
    normalized = _normalize_assignment_due_datetime(value)
    if normalized is None:
        return ""
    if timezone.is_aware(normalized):
        return timezone.localtime(normalized).isoformat()
    return normalized.isoformat()


def _normalize_optional_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if timezone.is_aware(value):
        return value
    return timezone.make_aware(value, timezone.get_current_timezone())


def _assignment_status_counts_as_completed(status: object) -> bool:
    normalized = str(status or "").strip()
    if not normalized:
        return False
    return normalized not in {
        HomeworkAssignment.STATUS_ASSIGNED,
        HomeworkAssignment.STATUS_CANCELLED,
    }


def _resolve_legacy_completion_datetime(
    *,
    assignment: HomeworkAssignment,
    has_online_questions: bool,
    latest_completed_submission: HomeworkSubmission | None,
) -> datetime | None:
    if has_online_questions:
        if latest_completed_submission is None:
            return None
        return _normalize_optional_datetime(
            get_homework_submission_effective_submitted_at(latest_completed_submission)
        )
    if assignment.status not in {
        HomeworkAssignment.STATUS_COMPLETED,
        HomeworkAssignment.STATUS_REVIEWED,
    }:
        return None
    return _normalize_optional_datetime(assignment.completed_at)


def _get_local_date_from_datetime(value: datetime | None) -> date | None:
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value).date()
    return value.date()
