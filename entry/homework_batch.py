from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet
from django.utils import timezone

from .homework_online import (
    decode_sql_ascii_json_text,
    encode_sql_ascii_json_text,
    normalize_candidate_editor_rows,
)
from .models import HomeworkAssignment, HomeworkImportJob, HomeworkQuestion, PortalUser
from .student_import import teacher_can_import_students


def get_visible_homework_import_jobs(
    portal_user: PortalUser,
    *,
    course_id: int | None = None,
) -> QuerySet[HomeworkImportJob]:
    queryset = (
        HomeworkImportJob.objects.select_related(
            "teacher",
            "assignment",
            "assignment__content",
            "assignment__content__course",
            "assignment__student",
        )
        .filter(
            is_active=True,
            assignment__is_active=True,
            assignment__content__is_active=True,
        )
        .filter(
            Q(parse_status=HomeworkImportJob.STATUS_CONFIRMED)
            | Q(questions__is_active=True)
        )
        .distinct()
        .order_by("-confirmed_at", "-created_at", "-id")
    )
    if course_id:
        queryset = queryset.filter(assignment__content__course_id=course_id)
    if teacher_can_import_students(portal_user):
        return queryset
    return queryset.filter(teacher=portal_user)


def build_homework_import_job_question_payloads(import_job: HomeworkImportJob) -> list[dict[str, Any]]:
    stored_questions = list(
        HomeworkQuestion.objects.filter(import_job=import_job, is_active=True).order_by("question_no", "id")
    )
    if stored_questions:
        return [
            {
                "question_no": question.question_no,
                "question_type": question.question_type,
                "stem": question.stem,
                "options_json": (
                    deepcopy(decoded_options)
                    if isinstance((decoded_options := decode_sql_ascii_json_text(question.options_json)), dict)
                    else {}
                ),
                "correct_answer": question.correct_answer,
                "analysis": question.analysis,
                "source_snapshot_json": (
                    deepcopy(decoded_snapshot)
                    if isinstance((decoded_snapshot := decode_sql_ascii_json_text(question.source_snapshot_json)), dict)
                    else {}
                ),
            }
            for question in stored_questions
        ]

    candidate_rows = normalize_candidate_editor_rows(import_job.candidates_json)
    selected_rows = [item for item in candidate_rows if item["included"]]
    payload_rows = selected_rows or candidate_rows
    return [
        {
            "question_no": index,
            "question_type": HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            "stem": item["stem"],
            "options_json": deepcopy(item["options"]) if isinstance(item["options"], dict) else {},
            "correct_answer": item["correct_answer"],
            "analysis": item["analysis"],
            "source_snapshot_json": {
                "import_job_id": import_job.id,
                "source_filename": import_job.source_filename,
                "candidate_index": item["index"],
                "notes": item["notes"],
            },
        }
        for index, item in enumerate(payload_rows, start=1)
    ]


def build_homework_import_job_preview_payload(import_job: HomeworkImportJob) -> dict[str, Any]:
    question_payloads = build_homework_import_job_question_payloads(import_job)
    preview_items = []
    for question in question_payloads:
        options = question["options_json"] if isinstance(question["options_json"], dict) else {}
        option_items = [
            f"{key}. {str(options.get(key) or '').strip()}"
            for key in ["A", "B", "C", "D"]
            if str(options.get(key) or "").strip()
        ]
        preview_items.append(
            {
                "question_no": question["question_no"],
                "stem": str(question["stem"] or "").strip(),
                "options_text": " / ".join(option_items) if option_items else "暂无选项信息",
                "correct_answer": str(question["correct_answer"] or "").strip().upper(),
                "analysis": str(question["analysis"] or "").strip(),
            }
        )
    return {
        "import_job_id": import_job.id,
        "source_filename": import_job.source_filename,
        "teacher_name": import_job.teacher.full_name or import_job.teacher.username,
        "teacher_username": import_job.teacher.username,
        "assignment_title": import_job.assignment.title,
        "course_title": import_job.assignment.content.course.title,
        "content_title": import_job.assignment.content.title,
        "source_due_date_text": import_job.assignment.due_date.isoformat(),
        "source_due_date_value": import_job.assignment.due_date.isoformat(),
        "parse_status": import_job.parse_status,
        "question_count": len(preview_items),
        "preview_items": preview_items,
        "empty_message": "暂无可预览题目" if not preview_items else "",
    }


def clone_confirmed_import_job_to_assignment(
    *,
    source_import_job: HomeworkImportJob,
    assignment: HomeworkAssignment,
    teacher: PortalUser,
) -> HomeworkImportJob:
    question_payloads = build_homework_import_job_question_payloads(source_import_job)
    if not question_payloads:
        raise ValidationError("当前导入题目记录里没有可复制的正式题目。")

    decoded_candidates = decode_sql_ascii_json_text(source_import_job.candidates_json)
    cloned_candidates = deepcopy(decoded_candidates) if isinstance(decoded_candidates, list) else []
    cloned_parse_notes = "\n".join(
        [
            line
            for line in [
                str(source_import_job.parse_notes or "").strip(),
                f"当前作业由 {teacher.username} 从 import job #{source_import_job.id} 批量布置复制。",
            ]
            if line
        ]
    )
    cloned_import_job = HomeworkImportJob.objects.create(
        teacher=teacher,
        assignment=assignment,
        source_file=source_import_job.source_file.name,
        source_filename=source_import_job.source_filename,
        source_sha256=source_import_job.source_sha256,
        source_type=source_import_job.source_type,
        parse_status=HomeworkImportJob.STATUS_CONFIRMED,
        candidates_json=encode_sql_ascii_json_text(cloned_candidates),
        parse_notes=cloned_parse_notes,
        confirmed_at=timezone.now(),
        is_active=True,
    )

    created_questions = []
    for index, payload in enumerate(question_payloads, start=1):
        question = HomeworkQuestion(
            assignment=assignment,
            import_job=cloned_import_job,
            question_no=index,
            question_type=payload["question_type"] or HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem=str(payload["stem"] or "").strip(),
            options_json=encode_sql_ascii_json_text(
                deepcopy(payload["options_json"]) if isinstance(payload["options_json"], dict) else {}
            ),
            correct_answer=str(payload["correct_answer"] or "").strip().upper(),
            analysis=str(payload["analysis"] or "").strip(),
            source_snapshot_json=encode_sql_ascii_json_text({
                **(
                    deepcopy(payload["source_snapshot_json"])
                    if isinstance(payload["source_snapshot_json"], dict)
                    else {}
                ),
                "source_import_job_id": source_import_job.id,
                "source_assignment_id": source_import_job.assignment_id,
                "cloned_by_teacher_id": teacher.id,
            }),
            is_active=True,
        )
        question.full_clean()
        question.save()
        created_questions.append(question)

    return cloned_import_job
