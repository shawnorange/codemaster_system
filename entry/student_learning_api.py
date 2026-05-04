from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from django.db.models import Count, Prefetch, Q, QuerySet
from django.utils import timezone

from .homework_completion_stats import (
    get_homework_submission_effective_submitted_at,
    get_latest_completed_submission,
    resolve_homework_completion_period_dates,
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
REQUIREMENT_COMPLETED_STATUSES = {
    HomeworkAssignment.STATUS_COMPLETED,
    HomeworkAssignment.STATUS_REVIEWED,
}


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
            | Q(due_date__range=(earliest_start, latest_end))
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

        assignment_snapshot = evaluate_student_learning_assignment(assignment)
        knowledge_point_item = _build_knowledge_point_item(assignment_snapshot)
        for period_type, bounds in periods.items():
            if not (bounds["start"] <= assignment.due_date <= bounds["end"]):
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
) -> dict[str, object] | None:
    payload = build_student_learning_overview(
        students=[student],
        anchor_date=anchor_date,
        include_teacher_fields=include_teacher_fields,
        teacher=teacher,
    )
    students = list(payload.get("students") or [])
    return students[0] if students else None


def build_student_week_lesson_feedback_payload(
    *,
    student: Student,
    anchor_date: date,
    include_teacher_fields: bool = True,
    teacher: PortalUser | None = None,
) -> dict[str, object]:
    student_row = build_single_student_learning_row(
        student=student,
        anchor_date=anchor_date,
        include_teacher_fields=include_teacher_fields,
        teacher=teacher,
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


def evaluate_student_learning_assignment(assignment: HomeworkAssignment) -> dict[str, object]:
    submissions = [
        submission
        for submission in getattr(assignment, "active_submissions", [])
        if submission.student_id == assignment.student_id
    ]
    has_online_questions = assignment.get_effective_online_question_count() > 0

    if has_online_questions:
        latest_completed_submission = get_latest_completed_submission(submissions)
        completed_at = (
            get_homework_submission_effective_submitted_at(latest_completed_submission)
            if latest_completed_submission is not None
            else None
        )
        completion_date = _get_local_date_from_datetime(completed_at)
        if latest_completed_submission is None:
            completion_status = COMPLETION_STATUS_INCOMPLETE
        elif completion_date is not None and completion_date > assignment.due_date:
            completion_status = COMPLETION_STATUS_DELAYED_COMPLETED
        else:
            completion_status = COMPLETION_STATUS_COMPLETED
        mastery_status, error_rate = _build_mastery_snapshot(latest_completed_submission)
    else:
        latest_completed_submission = None
        completed_at = assignment.completed_at
        completion_date = _get_local_date_from_datetime(completed_at)
        if assignment.status in REQUIREMENT_COMPLETED_STATUSES and completion_date is not None:
            if completion_date > assignment.due_date:
                completion_status = COMPLETION_STATUS_DELAYED_COMPLETED
            else:
                completion_status = COMPLETION_STATUS_COMPLETED
        else:
            completion_status = COMPLETION_STATUS_INCOMPLETE
        mastery_status = None
        error_rate = None

    return {
        "assignment_id": int(assignment.id),
        "assignment_title": str(assignment.title or "").strip(),
        "due_date": assignment.due_date,
        "has_online_questions": has_online_questions,
        "completion_status": completion_status,
        "latest_completed_submission": latest_completed_submission,
        "completed_at": completed_at,
        "mastery_status": mastery_status,
        "error_rate": error_rate,
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
    completion_status = str(assignment_snapshot["completion_status"])
    if completion_status == COMPLETION_STATUS_INCOMPLETE:
        period_summary["incomplete_count"] = int(period_summary["incomplete_count"]) + 1
        return
    period_summary["completed_count"] = int(period_summary["completed_count"]) + 1
    if completion_status == COMPLETION_STATUS_DELAYED_COMPLETED:
        period_summary["delayed_completed_count"] = int(period_summary["delayed_completed_count"]) + 1
    else:
        period_summary["on_time_completed_count"] = int(period_summary["on_time_completed_count"]) + 1


def _append_week_lesson_feedback(
    *,
    period_summary: dict[str, object],
    assignment: HomeworkAssignment,
) -> None:
    if assignment.summary_id is None:
        return

    summary = getattr(assignment, "summary", None)
    summary_id = int(assignment.summary_id)
    highlights = str(getattr(summary, "highlights", "") or "")
    areas_for_growth = str(getattr(summary, "areas_for_growth", "") or "")
    lesson_feedbacks = period_summary.get("lesson_feedbacks")
    feedback_item: dict[str, object] | None = None
    if isinstance(lesson_feedbacks, list):
        feedback_item = next(
            (
                item
                for item in lesson_feedbacks
                if int(item.get("summary_id") or 0) == summary_id
            ),
            None,
        )
        if feedback_item is None:
            feedback_item = {
                "summary_id": summary_id,
                "assignment_ids": [],
                "titles": [],
                "due_dates": [],
                "highlights": highlights,
                "areas_for_growth": areas_for_growth,
            }
            lesson_feedbacks.append(feedback_item)

    if feedback_item is None:
        return

    assignment_ids = feedback_item.get("assignment_ids")
    if isinstance(assignment_ids, list) and int(assignment.id) not in assignment_ids:
        assignment_ids.append(int(assignment.id))
    titles = feedback_item.get("titles")
    normalized_title = str(assignment.title or "").strip()
    if isinstance(titles, list) and normalized_title and normalized_title not in titles:
        titles.append(normalized_title)
    due_dates = feedback_item.get("due_dates")
    due_date_text = assignment.due_date.isoformat()
    if isinstance(due_dates, list) and due_date_text not in due_dates:
        due_dates.append(due_date_text)

    _append_unique_non_empty_text(period_summary.get("highlights"), highlights)
    _append_unique_non_empty_text(period_summary.get("areas_for_growth"), areas_for_growth)


def _build_knowledge_point_item(assignment_snapshot: dict[str, object]) -> dict[str, object]:
    return {
        "name": str(assignment_snapshot["assignment_title"]),
        "source": KNOWLEDGE_POINT_SOURCE_TITLE,
        "mastery_status": assignment_snapshot["mastery_status"],
        "error_rate": assignment_snapshot["error_rate"],
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
    wrong_count = max(int(latest_completed_submission.wrong_count or 0), 0)
    if total_count <= 0:
        return MASTERY_STATUS_NOT_MASTERED, None

    wrong_count = min(wrong_count, total_count)
    error_rate = round(wrong_count / total_count, 4)
    if error_rate == 0:
        return MASTERY_STATUS_MASTERED, error_rate
    if error_rate <= 0.3:
        return MASTERY_STATUS_BASIC, error_rate
    return MASTERY_STATUS_NOT_MASTERED, error_rate


def _get_local_date_from_datetime(value: datetime | None) -> date | None:
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value).date()
    return value.date()
