from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from .models import (
    HomeworkAssignment,
    HomeworkCompletionStat,
    HomeworkCompletionStatMissingAssignment,
    HomeworkSubmission,
    PortalUser,
    Student,
)


HOMEWORK_COMPLETION_STATUSES = {
    HomeworkSubmission.STATUS_SUBMITTED,
    HomeworkSubmission.STATUS_AUTO_CHECKED,
    HomeworkSubmission.STATUS_REVIEWED,
}
HOMEWORK_COMPLETION_STATE_COMPLETED = "completed"
HOMEWORK_COMPLETION_STATE_INCOMPLETE = "incomplete"


def normalize_homework_completion_period_type(period_type: str) -> str:
    normalized = str(period_type or "").strip().lower()
    return normalized if normalized in {"week", "month", "quarter"} else "week"


def resolve_homework_completion_period_dates(
    period_type: str,
    *,
    anchor_date: date | None = None,
) -> tuple[date, date, str]:
    normalized_period = normalize_homework_completion_period_type(period_type)
    current_date = anchor_date or timezone.localdate()

    if normalized_period == "month":
        start_date = current_date.replace(day=1)
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1, day=1) - timedelta(days=1)
        label = "本月"
    elif normalized_period == "quarter":
        quarter_start_month = ((current_date.month - 1) // 3) * 3 + 1
        start_date = current_date.replace(month=quarter_start_month, day=1)
        if quarter_start_month == 10:
            next_period_start = start_date.replace(year=start_date.year + 1, month=1, day=1)
        else:
            next_period_start = start_date.replace(month=quarter_start_month + 3, day=1)
        end_date = next_period_start - timedelta(days=1)
        label = "本季度"
    else:
        start_date = current_date - timedelta(days=current_date.weekday())
        end_date = start_date + timedelta(days=6)
        label = "本周"

    return start_date, end_date, label


def resolve_homework_completion_period_datetimes(
    period_type: str,
    *,
    anchor_date: date | None = None,
) -> tuple[datetime, datetime, date, date, str]:
    start_date, end_date, label = resolve_homework_completion_period_dates(period_type, anchor_date=anchor_date)
    tz = timezone.get_current_timezone()
    start_at = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_at = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
    return start_at, end_at, start_date, end_date, label


def get_homework_submission_effective_submitted_at(submission: HomeworkSubmission) -> datetime:
    return submission.submitted_at or submission.created_at


def get_latest_completed_submission(submissions: list[HomeworkSubmission]) -> HomeworkSubmission | None:
    completed_submissions = [
        submission
        for submission in submissions
        if submission.status in HOMEWORK_COMPLETION_STATUSES
    ]
    if not completed_submissions:
        return None
    return max(
        completed_submissions,
        key=lambda submission: (
            get_homework_submission_effective_submitted_at(submission),
            submission.created_at,
            submission.id,
        ),
    )


def build_homework_completion_stats(
    teacher: PortalUser,
    *,
    student: Student | None = None,
    period_type: str = "week",
    period_start: date | None = None,
    period_end: date | None = None,
    persist: bool = False,
) -> dict[str, object]:
    if teacher.role != PortalUser.ROLE_TEACHER:
        raise ValueError("homework completion stats only support teacher users")

    if student is not None and student.teacher_user_id != teacher.id:
        raise ValueError("student does not belong to the current teacher")

    normalized_period = normalize_homework_completion_period_type(period_type)
    default_period_start, default_period_end, period_label = resolve_homework_completion_period_dates(normalized_period)
    period_start = period_start or default_period_start
    period_end = period_end or default_period_end

    managed_students_queryset = Student.objects.select_related("user", "teacher_user").filter(teacher_user=teacher)
    if student is not None:
        managed_students_queryset = managed_students_queryset.filter(id=student.id)
    managed_students = list(managed_students_queryset.order_by("display_name", "id"))
    managed_student_ids = [item.id for item in managed_students]

    student_stats_by_id: dict[int, dict[str, object]] = {}
    for managed_student in managed_students:
        student_stats_by_id[managed_student.id] = {
            "student": managed_student,
            "student_id": managed_student.id,
            "student_name": managed_student.display_name,
            "teacher_id": teacher.id,
            "teacher_name": teacher.full_name or teacher.username,
            "period_type": normalized_period,
            "period_start": period_start,
            "period_end": period_end,
            "completed_count": 0,
            "incomplete_count": 0,
            "excluded_undated_count": 0,
            "assignment_count": 0,
            "online_assignment_count": 0,
            "online_completed_count": 0,
            "requirement_assignment_count": 0,
            "requirement_completed_count": 0,
            "latest_assigned_at": None,
            "latest_due_date": None,
            "assignments": [],
            "completed_assignments": [],
            "incomplete_assignments": [],
            "incomplete_assignment_ids": [],
        }

    assignment_queryset = (
        HomeworkAssignment.objects.select_related(
            "teacher",
            "student",
            "content",
            "content__course",
            "source_import_job",
        )
        .filter(
            teacher=teacher,
            student__teacher_user=teacher,
            is_active=True,
        )
        .exclude(status=HomeworkAssignment.STATUS_CANCELLED)
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
        .order_by("student__display_name", "due_date", "assigned_at", "id")
    )
    if managed_student_ids:
        assignment_queryset = assignment_queryset.filter(student_id__in=managed_student_ids)
    else:
        assignment_queryset = assignment_queryset.none()

    undated_queryset = assignment_queryset.filter(due_date__isnull=True)
    for row in undated_queryset.values("student_id").annotate(excluded_undated_count=Count("id")):
        student_bucket = student_stats_by_id.get(int(row["student_id"]))
        if student_bucket is None:
            continue
        student_bucket["excluded_undated_count"] = int(row["excluded_undated_count"] or 0)

    period_assignment_queryset = assignment_queryset.filter(
        due_date__isnull=False,
        due_date__range=(period_start, period_end),
    )
    assignment_items: list[dict[str, object]] = []
    for assignment in period_assignment_queryset:
        submissions = [
            submission
            for submission in getattr(assignment, "active_submissions", [])
            if submission.student_id == assignment.student_id
        ]
        latest_completed_submission = get_latest_completed_submission(submissions)
        has_online_questions = assignment.get_effective_online_question_count() > 0

        if has_online_questions:
            is_completed = latest_completed_submission is not None
            completion_reason = (
                "completed_online_submission"
                if is_completed
                else HomeworkCompletionStatMissingAssignment.REASON_ONLINE_MISSING
            )
        else:
            is_completed = (
                assignment.status in {HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED}
                and assignment.completed_at is not None
            )
            completion_reason = (
                "requirement_marked_completed"
                if is_completed
                else HomeworkCompletionStatMissingAssignment.REASON_REQUIREMENT_NOT_MARKED_COMPLETED
            )

        item = {
            "assignment": assignment,
            "assignment_id": assignment.id,
            "student": assignment.student,
            "student_id": assignment.student_id,
            "teacher_id": assignment.teacher_id,
            "has_online_questions": has_online_questions,
            "online_question_count": assignment.get_effective_online_question_count(),
            "completion_state": (
                HOMEWORK_COMPLETION_STATE_COMPLETED if is_completed else HOMEWORK_COMPLETION_STATE_INCOMPLETE
            ),
            "is_completed": is_completed,
            "completion_reason": completion_reason,
            "latest_completed_submission": latest_completed_submission,
            "active_submissions": submissions,
            "active_submission_count": len(submissions),
            "completed_submission_count": sum(
                1 for submission in submissions if submission.status in HOMEWORK_COMPLETION_STATUSES
            ),
            "effective_submitted_at": (
                get_homework_submission_effective_submitted_at(latest_completed_submission)
                if latest_completed_submission is not None
                else None
            ),
            "correct_count": int(getattr(latest_completed_submission, "correct_count", 0) or 0),
            "wrong_count": int(getattr(latest_completed_submission, "wrong_count", 0) or 0),
        }
        assignment_items.append(item)

        student_bucket = student_stats_by_id[assignment.student_id]
        student_bucket["assignment_count"] = int(student_bucket["assignment_count"]) + 1
        latest_assigned_at = student_bucket.get("latest_assigned_at")
        if latest_assigned_at is None or assignment.assigned_at > latest_assigned_at:
            student_bucket["latest_assigned_at"] = assignment.assigned_at
        latest_due_date = student_bucket.get("latest_due_date")
        if latest_due_date is None or assignment.due_date > latest_due_date:
            student_bucket["latest_due_date"] = assignment.due_date
        student_bucket["assignments"].append(item)
        if has_online_questions:
            student_bucket["online_assignment_count"] = int(student_bucket["online_assignment_count"]) + 1
        else:
            student_bucket["requirement_assignment_count"] = int(student_bucket["requirement_assignment_count"]) + 1
        if is_completed:
            student_bucket["completed_count"] = int(student_bucket["completed_count"]) + 1
            student_bucket["completed_assignments"].append(item)
            if has_online_questions:
                student_bucket["online_completed_count"] = int(student_bucket["online_completed_count"]) + 1
            else:
                student_bucket["requirement_completed_count"] = int(student_bucket["requirement_completed_count"]) + 1
        else:
            student_bucket["incomplete_count"] = int(student_bucket["incomplete_count"]) + 1
            student_bucket["incomplete_assignments"].append(item)
            student_bucket["incomplete_assignment_ids"].append(assignment.id)

    student_stats = [
        student_stats_by_id[student_id]
        for student_id in managed_student_ids
    ]
    summary = {
        "completed_count": sum(int(item["completed_count"]) for item in student_stats),
        "incomplete_count": sum(int(item["incomplete_count"]) for item in student_stats),
        "excluded_undated_count": sum(int(item["excluded_undated_count"]) for item in student_stats),
        "assignment_count": sum(int(item["assignment_count"]) for item in student_stats),
    }

    result = {
        "teacher": teacher,
        "teacher_id": teacher.id,
        "student": student,
        "period_type": normalized_period,
        "period_label": period_label,
        "period_start": period_start,
        "period_end": period_end,
        "students": student_stats,
        "student_stats": student_stats,
        "assignments": assignment_items,
        "summary": summary,
    }

    if persist:
        persist_homework_completion_stats(result)
    return result


def persist_homework_completion_stats(result: dict[str, object]) -> list[HomeworkCompletionStat]:
    teacher = result["teacher"]
    period_type = str(result["period_type"])
    period_start = result["period_start"]
    period_end = result["period_end"]
    student_stats = list(result.get("student_stats") or [])
    persisted_stats: list[HomeworkCompletionStat] = []

    with transaction.atomic():
        current_student_ids = [int(item["student_id"]) for item in student_stats]
        HomeworkCompletionStat.objects.filter(
            teacher=teacher,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
        ).exclude(student_id__in=current_student_ids).delete()

        for student_stat in student_stats:
            stat, _ = HomeworkCompletionStat.objects.update_or_create(
                teacher=teacher,
                student=student_stat["student"],
                period_type=period_type,
                period_start=period_start,
                period_end=period_end,
                defaults={
                    "completed_count": int(student_stat["completed_count"] or 0),
                    "incomplete_count": int(student_stat["incomplete_count"] or 0),
                    "excluded_undated_count": int(student_stat["excluded_undated_count"] or 0),
                    "generated_at": timezone.now(),
                },
            )
            incomplete_assignments = list(student_stat.get("incomplete_assignments") or [])
            HomeworkCompletionStatMissingAssignment.objects.filter(stat=stat).exclude(
                assignment_id__in=[int(item["assignment_id"]) for item in incomplete_assignments]
            ).delete()
            for assignment_item in incomplete_assignments:
                HomeworkCompletionStatMissingAssignment.objects.update_or_create(
                    stat=stat,
                    assignment=assignment_item["assignment"],
                    defaults={
                        "reason": str(assignment_item["completion_reason"] or ""),
                    },
                )
            persisted_stats.append(stat)

    return persisted_stats
