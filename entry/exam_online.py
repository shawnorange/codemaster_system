from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import secrets
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from .homework_online import normalize_candidate_text
from .exam_analysis import build_question_analysis_markdown
from .models import (
    Course,
    ExamPaper,
    ExamProctorEvent,
    ExamQuestion,
    ExamQuestionBankItem,
    ExamRun,
    ExamSession,
    ExamSubmissionAnswer,
    PortalUser,
    Student,
    TeacherStudentAssignment,
)


EXAM_CHOICE_KEYS = ("A", "B", "C", "D")
EXAM_ACCESS_CODE_LENGTH = 6
SWITCH_EVENT_TYPES = {
    ExamProctorEvent.EVENT_VISIBILITY_HIDDEN,
    ExamProctorEvent.EVENT_SCREEN_SHARE_STOPPED,
}
FINISHED_EXAM_SESSION_STATUSES = {
    ExamSession.STATUS_SUBMITTED,
    ExamSession.STATUS_AUTO_CHECKED,
    ExamSession.STATUS_EXPIRED,
    ExamSession.STATUS_INVALIDATED,
}
EXAM_PRACTICE_SESSION_TYPES = {
    ExamSession.SESSION_TYPE_FULL_PRACTICE,
    ExamSession.SESSION_TYPE_WRONG_PRACTICE,
}
RECALCULABLE_EXAM_SESSION_STATUSES = {
    ExamSession.STATUS_SUBMITTED,
    ExamSession.STATUS_AUTO_CHECKED,
}


class ExamError(Exception):
    def __init__(self, message: str, *, target_question_id: int | None = None) -> None:
        super().__init__(message)
        self.target_question_id = target_question_id


def normalize_exam_answer(value: object) -> str:
    answer = normalize_candidate_text(value).upper()[:1]
    return answer if answer in EXAM_CHOICE_KEYS else ""


def normalize_exam_access_code(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:EXAM_ACCESS_CODE_LENGTH]


def normalize_exam_mode(value: object) -> str:
    mode = normalize_candidate_text(value)
    if mode in {ExamPaper.MODE_TIMED, ExamPaper.MODE_DEADLINE}:
        return mode
    return ExamPaper.MODE_DEADLINE


def normalize_exam_options(value: object) -> dict[str, str]:
    raw_options = value if isinstance(value, dict) else {}
    return {
        key: normalize_candidate_text(raw_options.get(key) or raw_options.get(key.lower()) or "")
        for key in EXAM_CHOICE_KEYS
    }


def normalize_exam_question_payload(payload: dict[str, Any], *, index: int) -> dict[str, Any]:
    options = normalize_exam_options(payload.get("options") or payload.get("options_json"))
    score = Decimal(str(payload.get("score") or "1"))
    if score <= 0:
        raise ExamError(f"第 {index} 题分值必须大于 0。")
    return {
        "question_no": index,
        "stem": str(payload.get("stem") or "").strip(),
        "options": options,
        "correct_answer": normalize_exam_answer(payload.get("correct_answer")),
        "analysis": str(payload.get("analysis") or "").strip(),
        "score": score.quantize(Decimal("0.01")),
        "wrong_point_label": normalize_candidate_text(payload.get("wrong_point_label")),
        "image_path": str(payload.get("image_path") or "").strip(),
        "source_snapshot_json": deepcopy(payload.get("source_snapshot_json") if isinstance(payload.get("source_snapshot_json"), dict) else {}),
    }


def normalize_and_validate_exam_question_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [
        normalize_exam_question_payload(payload, index=index)
        for index, payload in enumerate(payloads, start=1)
    ]
    if not normalized:
        raise ExamError("请至少添加一道考试题。")

    for question in normalized:
        if not question["stem"]:
            raise ExamError(f"第 {question['question_no']} 题题干不能为空。")
        if any(not question["options"][key] for key in EXAM_CHOICE_KEYS):
            raise ExamError(f"第 {question['question_no']} 题 A/B/C/D 选项必须填写完整。")
        if question["correct_answer"] not in EXAM_CHOICE_KEYS:
            raise ExamError(f"第 {question['question_no']} 题正确答案必须是 A/B/C/D。")
    return normalized


def is_auto_gradable_exam_question(question: ExamQuestion) -> bool:
    options = question.options_json if isinstance(question.options_json, dict) else {}
    correct_answer = normalize_exam_answer(question.correct_answer)
    return question.question_type in {
        ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
        getattr(ExamQuestion, "QUESTION_TYPE_TRUE_FALSE", "true_false"),
    } and bool(options) and correct_answer in options


def is_exam_practice_session(session: ExamSession) -> bool:
    return session.session_type in EXAM_PRACTICE_SESSION_TYPES


def get_exam_session_questions(session: ExamSession) -> list[ExamQuestion]:
    questions = list(
        session.paper.questions.filter(is_active=True)
        .prefetch_related("analysis_blocks")
        .order_by("question_no", "id")
    )
    scope = session.question_scope_json if isinstance(session.question_scope_json, dict) else {}
    question_ids = {
        int(question_id)
        for question_id in (scope.get("question_ids") or [])
        if str(question_id).strip().isdigit()
    }
    if not question_ids:
        return questions
    return [question for question in questions if question.id in question_ids]


def get_first_finished_exam_session_for_wrong_practice(session: ExamSession) -> ExamSession:
    return (
        ExamSession.objects.filter(
            paper_id=session.paper_id,
            student_id=session.student_id,
            session_type=ExamSession.SESSION_TYPE_EXAM,
            status__in=FINISHED_EXAM_SESSION_STATUSES,
            is_active=True,
        )
        .order_by("attempt_no", "created_at", "id")
        .first()
        or session
    )


def get_wrong_question_ids_from_first_exam_session(session: ExamSession) -> list[int]:
    source_session = get_first_finished_exam_session_for_wrong_practice(session)
    source_questions = list(source_session.paper.questions.filter(is_active=True).order_by("question_no", "id"))
    wrong_question_ids = set(
        source_session.answers.filter(is_correct=False, question__is_active=True).values_list("question_id", flat=True)
    )
    return [question.id for question in source_questions if question.id in wrong_question_ids]


def count_student_explanation_chars(value: object) -> int:
    return len("".join(str(value or "").strip().split()))


def build_exam_question_payloads_from_bank_items(
    *,
    teacher: PortalUser,
    course_id: int,
    question_bank_item_ids: list[int],
) -> list[dict[str, Any]]:
    normalized_ids = []
    seen_ids = set()
    for item_id in question_bank_item_ids:
        if item_id and item_id not in seen_ids:
            seen_ids.add(item_id)
            normalized_ids.append(item_id)
    if not normalized_ids:
        raise ExamError("请选择题库题目。")

    teacher_course_ids = {
        assignment.course_id
        for assignment in TeacherStudentAssignment.objects.filter(teacher=teacher, is_active=True)
    }
    if course_id not in teacher_course_ids:
        raise ExamError("请选择当前老师负责范围内的课程。")

    bank_items = list(
        ExamQuestionBankItem.objects.select_related("course", "content")
        .filter(id__in=normalized_ids, course_id=course_id, is_active=True)
        .order_by("id")
    )
    bank_item_by_id = {item.id: item for item in bank_items}
    if len(bank_item_by_id) != len(normalized_ids):
        raise ExamError("所选题库题目不在当前课程范围内，或已被停用。")

    payloads = []
    for item_id in normalized_ids:
        item = bank_item_by_id[item_id]
        payloads.append(
            {
                "stem": item.stem,
                "options": deepcopy(item.options_json if isinstance(item.options_json, dict) else {}),
                "correct_answer": item.correct_answer,
                "analysis": item.analysis,
                "score": item.score,
                "wrong_point_label": item.knowledge_point,
                "image_path": item.image_path,
                "source_snapshot_json": {
                    "created_from": "exam_question_bank_item",
                    "question_bank_item_id": item.id,
                    "source": item.source,
                    "source_label": item.source_label,
                    "source_url": item.source_url,
                    "level_code": item.level_code,
                    "knowledge_point": item.knowledge_point,
                    "course_id": item.course_id,
                    "course_title": item.course.title if item.course_id and item.course else "",
                    "content_id": item.content_id,
                    "content_title": item.content.title if item.content_id and item.content else "",
                    "source_snapshot_json": deepcopy(item.source_snapshot_json),
                },
            }
        )
    return payloads


def get_teacher_student_exam_scope(teacher: PortalUser, student: Student) -> list[TeacherStudentAssignment]:
    return list(
        TeacherStudentAssignment.objects.select_related("course")
        .filter(teacher=teacher, student=student, is_active=True)
        .order_by("course_id", "level_code", "id")
    )


def resolve_exam_course(
    *,
    teacher: PortalUser,
    student: Student,
    course_id: int | None,
) -> Course:
    assignments = get_teacher_student_exam_scope(teacher, student)
    if not assignments:
        raise ExamError("当前学生不在你的负责范围内。")
    if course_id:
        for assignment in assignments:
            if assignment.course_id == course_id:
                return assignment.course
        raise ExamError("请选择当前老师负责范围内的课程。")
    return assignments[0].course


def get_exam_window_end(paper: ExamPaper):
    if paper.mode == ExamPaper.MODE_TIMED and paper.start_at:
        return paper.start_at + timedelta(minutes=max(int(paper.duration_minutes or 0), 1))
    return paper.end_at


def normalize_exam_schedule(
    *,
    mode: str,
    start_at,
    end_at,
    duration_minutes: int,
) -> dict[str, object]:
    normalized_mode = normalize_exam_mode(mode)
    normalized_duration = max(int(duration_minutes or 0), 1)
    now = timezone.now()

    if normalized_mode == ExamPaper.MODE_TIMED:
        if start_at is None:
            raise ExamError("定时考试需要设置开始时间。")
        normalized_end_at = start_at + timedelta(minutes=normalized_duration)
        if normalized_end_at <= now:
            raise ExamError("定时考试结束时间必须晚于当前时间。")
        return {
            "mode": normalized_mode,
            "start_at": start_at,
            "end_at": normalized_end_at,
            "duration_minutes": normalized_duration,
        }

    if end_at is None:
        raise ExamError("DL 模式需要设置截止时间。")
    if end_at <= now:
        raise ExamError("DL 截止时间必须晚于当前时间。")
    return {
        "mode": normalized_mode,
        "start_at": None,
        "end_at": end_at,
        "duration_minutes": normalized_duration,
    }


def build_exam_entry_state(session: ExamSession, *, now=None) -> dict[str, object]:
    current_time = now or timezone.now()
    paper = session.paper
    if session.status not in {ExamSession.STATUS_ASSIGNED, ExamSession.STATUS_IN_PROGRESS}:
        return {"can_start": False, "message": "当前考试已经结束。", "window_end": get_exam_window_end(paper)}
    if paper.status != ExamPaper.STATUS_PUBLISHED or not paper.is_active:
        return {"can_start": False, "message": "当前考试尚未发布。", "window_end": get_exam_window_end(paper)}

    window_end = get_exam_window_end(paper)
    if paper.mode == ExamPaper.MODE_TIMED:
        if paper.start_at is None:
            return {"can_start": False, "message": "当前定时考试没有设置开始时间。", "window_end": window_end}
        if current_time < paper.start_at:
            return {"can_start": False, "message": "当前考试还未到开始时间。", "window_end": window_end}
        if window_end and current_time > window_end:
            return {"can_start": False, "message": "当前考试已超过固定时长。", "window_end": window_end}
        return {"can_start": True, "message": "当前处于定时考试窗口内。", "window_end": window_end}

    if window_end is None:
        return {"can_start": False, "message": "当前 DL 考试没有设置截止时间。", "window_end": window_end}
    if current_time > window_end:
        return {"can_start": False, "message": "当前考试已超过 DL。", "window_end": window_end}
    return {"can_start": True, "message": "当前时间小于 DL，可以进入考试。", "window_end": window_end}


def expire_exam_session(session: ExamSession) -> None:
    if session.status in {ExamSession.STATUS_ASSIGNED, ExamSession.STATUS_IN_PROGRESS}:
        session.status = ExamSession.STATUS_EXPIRED
        session.save(update_fields=["status", "updated_at"])


def generate_unique_exam_access_code() -> str:
    for _ in range(200):
        code = f"{secrets.randbelow(10 ** EXAM_ACCESS_CODE_LENGTH):0{EXAM_ACCESS_CODE_LENGTH}d}"
        if not ExamPaper.objects.filter(access_code=code, is_active=True).exists() and not ExamRun.objects.filter(access_code=code, is_active=True).exists():
            return code
    raise ExamError("暂时无法生成唯一考试口令，请稍后重试。")


def get_exam_run_window_end(paper: ExamPaper):
    return get_exam_window_end(paper)


def ensure_exam_run_for_paper(*, paper: ExamPaper, teacher: PortalUser | None = None) -> ExamRun | None:
    if not paper.access_code:
        return None
    existing_run = (
        ExamRun.objects.filter(paper=paper, access_code=paper.access_code, is_active=True)
        .order_by("-generated_at", "-id")
        .first()
    )
    if existing_run:
        return existing_run
    generated_at = paper.access_code_generated_at or timezone.now()
    return ExamRun.objects.create(
        paper=paper,
        access_code=paper.access_code,
        generated_at=generated_at,
        starts_at=paper.start_at,
        ends_at=get_exam_run_window_end(paper),
        created_by=teacher or paper.teacher,
        is_active=True,
    )


def activate_exam_access_code(*, paper: ExamPaper, teacher: PortalUser) -> ExamPaper:
    if paper.teacher_id != teacher.id or not paper.is_active:
        raise ExamError("未找到可开始的考试。")
    now = timezone.now()
    window_end = get_exam_window_end(paper)
    running_exam_sessions = paper.sessions.filter(
        is_active=True,
        session_type=ExamSession.SESSION_TYPE_EXAM,
        status=ExamSession.STATUS_IN_PROGRESS,
    )
    if window_end and now > window_end:
        running_exam_sessions.update(status=ExamSession.STATUS_EXPIRED, updated_at=now)
    elif running_exam_sessions.exists():
        raise ExamError("当前正在考试中，不允许重新生成口令。")
    if not paper.questions.filter(is_active=True).exists():
        raise ExamError("当前试卷还没有题目，不能开始考试。")

    with transaction.atomic():
        locked_paper = (
            ExamPaper.objects.select_for_update()
            .select_related("teacher")
            .get(id=paper.id, teacher=teacher, is_active=True)
        )
        now = timezone.now()
        window_end = get_exam_window_end(locked_paper)
        running_exam_sessions = locked_paper.sessions.filter(
            is_active=True,
            session_type=ExamSession.SESSION_TYPE_EXAM,
            status=ExamSession.STATUS_IN_PROGRESS,
        )
        if window_end and now > window_end:
            running_exam_sessions.update(status=ExamSession.STATUS_EXPIRED, updated_at=now)
        elif running_exam_sessions.exists():
            raise ExamError("当前正在考试中，不允许重新生成口令。")
        if not locked_paper.questions.filter(is_active=True).exists():
            raise ExamError("当前试卷还没有题目，不能开始考试。")
        locked_paper.access_code = generate_unique_exam_access_code()
        locked_paper.access_code_generated_at = now
        locked_paper.start_at = now
        locked_paper.end_at = now + timedelta(minutes=int(locked_paper.duration_minutes or 60))
        locked_paper.status = ExamPaper.STATUS_PUBLISHED
        locked_paper.save(update_fields=["access_code", "access_code_generated_at", "start_at", "end_at", "status", "updated_at"])
        ensure_exam_run_for_paper(paper=locked_paper, teacher=teacher)
        return locked_paper


def create_or_get_exam_session_by_access_code(*, student: Student, access_code: str) -> ExamSession:
    normalized_code = normalize_exam_access_code(access_code)
    if len(normalized_code) != EXAM_ACCESS_CODE_LENGTH:
        raise ExamError("请输入 6 位考试口令。")

    paper = (
        ExamPaper.objects.select_related("teacher", "course")
        .filter(access_code=normalized_code, status=ExamPaper.STATUS_PUBLISHED, is_active=True)
        .order_by("-access_code_generated_at", "-id")
        .first()
    )
    if not paper:
        raise ExamError("考试口令无效或已失效。")
    if paper.course_id and not TeacherStudentAssignment.objects.filter(
        teacher=paper.teacher,
        student=student,
        course_id=paper.course_id,
        is_active=True,
    ).exists():
        raise ExamError("当前考试不在你的负责课程范围内。")
    if not paper.questions.filter(is_active=True).exists():
        raise ExamError("当前考试还没有题目。")
    current_run = ensure_exam_run_for_paper(paper=paper, teacher=paper.teacher)

    existing_open_session_query = ExamSession.objects.filter(
        paper=paper,
        student=student,
        is_active=True,
        status__in=[ExamSession.STATUS_ASSIGNED, ExamSession.STATUS_IN_PROGRESS],
    )
    if current_run:
        existing_open_session_query = existing_open_session_query.filter(Q(exam_run=current_run) | Q(exam_run__isnull=True))
    existing_open_session = existing_open_session_query.order_by("-attempt_no", "-id").first()
    if existing_open_session:
        if current_run and existing_open_session.exam_run_id is None:
            existing_open_session.exam_run = current_run
            existing_open_session.save(update_fields=["exam_run", "updated_at"])
        return existing_open_session

    with transaction.atomic():
        locked_paper = ExamPaper.objects.select_for_update().get(id=paper.id, is_active=True)
        current_run = ensure_exam_run_for_paper(paper=locked_paper, teacher=paper.teacher)
        existing_open_session_query = ExamSession.objects.filter(
            paper=locked_paper,
            student=student,
            is_active=True,
            status__in=[ExamSession.STATUS_ASSIGNED, ExamSession.STATUS_IN_PROGRESS],
        )
        if current_run:
            existing_open_session_query = existing_open_session_query.filter(Q(exam_run=current_run) | Q(exam_run__isnull=True))
        existing_open_session = existing_open_session_query.order_by("-attempt_no", "-id").first()
        if existing_open_session:
            if current_run and existing_open_session.exam_run_id is None:
                existing_open_session.exam_run = current_run
                existing_open_session.save(update_fields=["exam_run", "updated_at"])
            return existing_open_session
        latest_attempt_no = (
            ExamSession.objects.filter(paper=locked_paper, student=student, is_active=True)
            .aggregate(latest_attempt_no=Max("attempt_no"))
            .get("latest_attempt_no")
            or 0
        )
        session = ExamSession.objects.create(
            paper=locked_paper,
            exam_run=current_run,
            student=student,
            assigned_by=paper.teacher,
            attempt_no=int(latest_attempt_no) + 1,
            status=ExamSession.STATUS_ASSIGNED,
            is_active=True,
        )
        entry_state = build_exam_entry_state(session)
        if not entry_state["can_start"]:
            raise ExamError(str(entry_state["message"]))
        return session


def create_exam_practice_session(
    *,
    source_session: ExamSession,
    student: Student,
    session_type: str,
) -> ExamSession:
    if source_session.student_id != student.id or not source_session.is_active:
        raise ExamError("未找到可练习的考试记录。")
    if source_session.status not in FINISHED_EXAM_SESSION_STATUSES:
        raise ExamError("请先提交考试后再开始练习。")
    if session_type not in EXAM_PRACTICE_SESSION_TYPES:
        raise ExamError("请选择有效的练习模式。")

    source_questions = list(source_session.paper.questions.filter(is_active=True).order_by("question_no", "id"))
    if session_type == ExamSession.SESSION_TYPE_WRONG_PRACTICE:
        source_session = get_first_finished_exam_session_for_wrong_practice(source_session)
        source_questions = list(source_session.paper.questions.filter(is_active=True).order_by("question_no", "id"))
        question_ids = get_wrong_question_ids_from_first_exam_session(source_session)
        if not question_ids:
            raise ExamError("第一次正式考试没有错题，暂时不能开启错题练习。")
    else:
        question_ids = [question.id for question in source_questions]
        if not question_ids:
            raise ExamError("当前考试还没有题目，不能开启整卷练习。")

    with transaction.atomic():
        locked_paper = ExamPaper.objects.select_for_update().get(id=source_session.paper_id, is_active=True)
        latest_attempt_no = (
            ExamSession.objects.filter(paper=locked_paper, student=student, is_active=True)
            .aggregate(latest_attempt_no=Max("attempt_no"))
            .get("latest_attempt_no")
            or 0
        )
        now = timezone.now()
        return ExamSession.objects.create(
            paper=locked_paper,
            exam_run=source_session.exam_run,
            student=student,
            assigned_by=source_session.assigned_by or locked_paper.teacher,
            attempt_no=int(latest_attempt_no) + 1,
            session_type=session_type,
            question_scope_json={
                "source_session_id": source_session.id,
                "source_session_type": source_session.session_type,
                "question_ids": question_ids,
            },
            status=ExamSession.STATUS_IN_PROGRESS,
            started_at=now,
            is_active=True,
        )


def create_exam_for_students(
    *,
    teacher: PortalUser,
    student_ids: list[int],
    course_id: int,
    title: str,
    description: str,
    mode: str,
    start_at,
    end_at,
    duration_minutes: int,
    proctoring_enabled: bool,
    question_payloads: list[dict[str, Any]] | None = None,
    question_bank_item_ids: list[int] | None = None,
) -> ExamPaper:
    normalized_title = normalize_candidate_text(title)
    if not normalized_title:
        raise ExamError("请填写考试标题。")
    normalized_student_ids = []
    seen_student_ids = set()
    for student_id in student_ids:
        if student_id and student_id not in seen_student_ids:
            seen_student_ids.add(student_id)
            normalized_student_ids.append(student_id)
    if not normalized_student_ids:
        raise ExamError("请选择至少一名考试学生。")

    schedule = normalize_exam_schedule(
        mode=mode,
        start_at=start_at,
        end_at=end_at,
        duration_minutes=duration_minutes,
    )
    if question_bank_item_ids:
        question_payloads = build_exam_question_payloads_from_bank_items(
            teacher=teacher,
            course_id=course_id,
            question_bank_item_ids=question_bank_item_ids,
        )
    normalized_questions = normalize_and_validate_exam_question_payloads(question_payloads or [])
    assignments = list(
        TeacherStudentAssignment.objects.select_related("course", "student")
        .filter(
            teacher=teacher,
            course_id=course_id,
            student_id__in=normalized_student_ids,
            is_active=True,
        )
        .order_by("student_id", "id")
    )
    assignment_by_student = {}
    for assignment in assignments:
        assignment_by_student.setdefault(assignment.student_id, assignment)
    if len(assignment_by_student) != len(normalized_student_ids):
        raise ExamError("请选择当前老师负责范围内的课程和学生。")
    course = assignments[0].course

    with transaction.atomic():
        paper = ExamPaper.objects.create(
            teacher=teacher,
            course=course,
            title=normalized_title,
            description=str(description or "").strip(),
            mode=schedule["mode"],
            duration_minutes=schedule["duration_minutes"],
            start_at=schedule["start_at"],
            end_at=schedule["end_at"],
            proctoring_enabled=proctoring_enabled,
            status=ExamPaper.STATUS_PUBLISHED,
            is_active=True,
        )
        run = ensure_exam_run_for_paper(paper=paper, teacher=teacher)
        for question_payload in normalized_questions:
            question = ExamQuestion(
                paper=paper,
                question_no=question_payload["question_no"],
                question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                stem=question_payload["stem"],
                options_json=deepcopy(question_payload["options"]),
                correct_answer=question_payload["correct_answer"],
                analysis=question_payload["analysis"],
                score=question_payload["score"],
                wrong_point_label=question_payload["wrong_point_label"],
                image_path=question_payload["image_path"],
                source_snapshot_json=question_payload["source_snapshot_json"] or {"created_from": "teacher_exam_batch_form"},
                is_active=True,
            )
            try:
                question.full_clean()
            except ValidationError as exc:
                raise ExamError(f"第 {question.question_no} 题校验失败：{exc}") from exc
            question.save()

        sessions = [
            ExamSession(
                paper=paper,
                exam_run=run,
                student=assignment_by_student[student_id].student,
                assigned_by=teacher,
                attempt_no=1,
                status=ExamSession.STATUS_ASSIGNED,
                is_active=True,
            )
            for student_id in normalized_student_ids
        ]
        ExamSession.objects.bulk_create(sessions)
        return paper


def create_exam_for_student(
    *,
    teacher: PortalUser,
    student: Student,
    course_id: int | None,
    title: str,
    description: str,
    duration_minutes: int,
    proctoring_enabled: bool,
    question_payloads: list[dict[str, Any]],
    mode: str = ExamPaper.MODE_DEADLINE,
    start_at=None,
    end_at=None,
) -> ExamSession:
    course = resolve_exam_course(teacher=teacher, student=student, course_id=course_id)
    if end_at is None and start_at is None:
        end_at = timezone.now() + timedelta(days=7)
    paper = create_exam_for_students(
        teacher=teacher,
        student_ids=[student.id],
        course_id=course.id,
        title=title,
        description=description,
        mode=mode,
        start_at=start_at,
        end_at=end_at,
        duration_minutes=duration_minutes,
        proctoring_enabled=proctoring_enabled,
        question_payloads=question_payloads,
    )
    return paper.sessions.get(student=student, is_active=True)


def start_exam_session(session: ExamSession, *, student: Student) -> ExamSession:
    if session.student_id != student.id or not session.is_active:
        raise ExamError("未找到可开始的考试。")
    if session.status not in {ExamSession.STATUS_ASSIGNED, ExamSession.STATUS_IN_PROGRESS}:
        raise ExamError("当前考试已经结束，不能重新开始。")

    entry_state = build_exam_entry_state(session)
    if not entry_state["can_start"]:
        if "超过" in str(entry_state["message"]):
            expire_exam_session(session)
        raise ExamError(str(entry_state["message"]))

    if session.status == ExamSession.STATUS_ASSIGNED:
        now = timezone.now()
        if session.exam_run_id is None:
            session.exam_run = ensure_exam_run_for_paper(paper=session.paper, teacher=session.assigned_by or session.paper.teacher)
        session.status = ExamSession.STATUS_IN_PROGRESS
        session.started_at = now
        session.save(update_fields=["exam_run", "status", "started_at", "updated_at"])
    return session


def grade_exam_session(
    session: ExamSession,
    *,
    student: Student,
    selected_answers: dict[int, str],
    explanation_texts: dict[int, str] | None = None,
) -> ExamSession:
    if session.student_id != student.id or not session.is_active:
        raise ExamError("未找到可提交的考试。")
    if session.status == ExamSession.STATUS_ASSIGNED:
        raise ExamError("请先点击开始考试。")
    if session.status != ExamSession.STATUS_IN_PROGRESS:
        raise ExamError("当前考试已经提交，不能重复交卷。")

    questions = get_exam_session_questions(session)
    if not questions:
        raise ExamError("当前考试还没有正式题目，暂时不能提交。")

    explanation_texts = explanation_texts or {}
    with transaction.atomic():
        locked_session = (
            ExamSession.objects.select_for_update()
            .select_related("paper")
            .get(id=session.id, student=student, is_active=True)
        )
        if locked_session.status == ExamSession.STATUS_ASSIGNED:
            raise ExamError("请先点击开始考试。")
        if locked_session.status != ExamSession.STATUS_IN_PROGRESS:
            raise ExamError("当前考试已经提交，不能重复交卷。")
        if not is_exam_practice_session(locked_session):
            entry_state = build_exam_entry_state(locked_session)
            if not entry_state["can_start"]:
                if "超过" in str(entry_state["message"]):
                    locked_session.status = ExamSession.STATUS_EXPIRED
                    locked_session.save(update_fields=["status", "updated_at"])
                raise ExamError(str(entry_state["message"]))

        correct_count = 0
        earned_score = Decimal("0.00")
        total_score = Decimal("0.00")
        questions = get_exam_session_questions(locked_session)
        gradable_questions = [question for question in questions if is_auto_gradable_exam_question(question)]
        if locked_session.session_type == ExamSession.SESSION_TYPE_WRONG_PRACTICE:
            short_explanation_questions = [
                question
                for question in gradable_questions
                if count_student_explanation_chars(explanation_texts.get(question.id)) < 10
            ]
            if short_explanation_questions:
                joined_numbers = "、".join(str(question.question_no) for question in short_explanation_questions)
                raise ExamError(
                    f"错题练习需要为每题写出不少于 10 字的解析，请补充第 {joined_numbers} 题。",
                    target_question_id=short_explanation_questions[0].id,
                )
        for question in gradable_questions:
            selected_answer = normalize_exam_answer(selected_answers.get(question.id))
            explanation_text = str(explanation_texts.get(question.id) or "").strip()
            is_correct = bool(selected_answer and selected_answer == question.correct_answer)
            total_score += Decimal(question.score)
            score = Decimal(question.score) if is_correct else Decimal("0.00")
            if is_correct:
                correct_count += 1
                earned_score += score
            ExamSubmissionAnswer.objects.create(
                session=locked_session,
                question=question,
                selected_answer=selected_answer,
                explanation_text=explanation_text,
                is_correct=is_correct,
                score=score.quantize(Decimal("0.01")),
                correct_answer_snapshot=question.correct_answer,
                analysis_snapshot=build_question_analysis_markdown(question),
            )

        now = timezone.now()
        total_count = len(gradable_questions)
        locked_session.status = ExamSession.STATUS_AUTO_CHECKED
        locked_session.total_count = total_count
        locked_session.correct_count = correct_count
        locked_session.wrong_count = total_count - correct_count
        locked_session.total_score = total_score.quantize(Decimal("0.01"))
        locked_session.earned_score = earned_score.quantize(Decimal("0.01"))
        locked_session.started_at = locked_session.started_at or now
        locked_session.submitted_at = now
        locked_session.checked_at = now
        locked_session.save(
            update_fields=[
                "status",
                "total_count",
                "correct_count",
                "wrong_count",
                "total_score",
                "earned_score",
                "started_at",
                "submitted_at",
                "checked_at",
                "updated_at",
            ]
        )
        return locked_session


def recalculate_exam_session_score(session: ExamSession) -> ExamSession:
    with transaction.atomic():
        locked_session = ExamSession.objects.select_for_update().select_related("paper").get(id=session.id)
        if not locked_session.is_active or locked_session.status not in RECALCULABLE_EXAM_SESSION_STATUSES:
            return locked_session

        questions = get_exam_session_questions(locked_session)
        gradable_questions = [question for question in questions if is_auto_gradable_exam_question(question)]
        answers_by_question = {
            answer.question_id: answer
            for answer in ExamSubmissionAnswer.objects.select_for_update().filter(session=locked_session, question__in=gradable_questions)
        }
        correct_count = 0
        earned_score = Decimal("0.00")
        total_score = Decimal("0.00")

        for question in gradable_questions:
            answer = answers_by_question.get(question.id)
            selected_answer = normalize_exam_answer(answer.selected_answer if answer else "")
            is_correct = bool(selected_answer and selected_answer == normalize_exam_answer(question.correct_answer))
            total_score += Decimal(question.score)
            score = Decimal(question.score) if is_correct else Decimal("0.00")
            if is_correct:
                correct_count += 1
                earned_score += score
            if answer:
                answer.selected_answer = selected_answer
                answer.is_correct = is_correct
                answer.score = score.quantize(Decimal("0.01"))
                answer.correct_answer_snapshot = normalize_exam_answer(question.correct_answer)
                answer.analysis_snapshot = build_question_analysis_markdown(question)
                answer.save(
                    update_fields=[
                        "selected_answer",
                        "is_correct",
                        "score",
                        "correct_answer_snapshot",
                        "analysis_snapshot",
                        "updated_at",
                    ]
                )
            else:
                ExamSubmissionAnswer.objects.create(
                    session=locked_session,
                    question=question,
                    selected_answer="",
                    explanation_text="",
                    is_correct=False,
                    score=Decimal("0.00"),
                    correct_answer_snapshot=normalize_exam_answer(question.correct_answer),
                    analysis_snapshot=build_question_analysis_markdown(question),
                )

        locked_session.total_count = len(gradable_questions)
        locked_session.correct_count = correct_count
        locked_session.wrong_count = locked_session.total_count - correct_count
        locked_session.total_score = total_score.quantize(Decimal("0.01"))
        locked_session.earned_score = earned_score.quantize(Decimal("0.01"))
        locked_session.checked_at = timezone.now()
        locked_session.save(
            update_fields=[
                "total_count",
                "correct_count",
                "wrong_count",
                "total_score",
                "earned_score",
                "checked_at",
                "updated_at",
            ]
        )
        return locked_session


def recalculate_exam_scores_for_paper(paper: ExamPaper) -> int:
    updated_count = 0
    with transaction.atomic():
        sessions = list(
            ExamSession.objects.select_for_update()
            .filter(paper=paper, is_active=True, status__in=RECALCULABLE_EXAM_SESSION_STATUSES)
            .order_by("id")
        )
        for session in sessions:
            recalculate_exam_session_score(session)
            updated_count += 1
    return updated_count


def record_exam_proctor_event(
    session: ExamSession,
    *,
    student: Student,
    event_type: str,
    metadata: dict[str, Any] | None = None,
) -> ExamProctorEvent:
    if session.student_id != student.id or not session.is_active:
        raise ExamError("未找到可记录的考试。")
    if event_type not in {choice[0] for choice in ExamProctorEvent.EVENT_CHOICES}:
        raise ExamError("未知监考事件。")
    if session.status != ExamSession.STATUS_IN_PROGRESS:
        raise ExamError("当前考试不在进行中。")

    with transaction.atomic():
        locked_session = ExamSession.objects.select_for_update().get(id=session.id, student=student, is_active=True)
        event = ExamProctorEvent.objects.create(
            session=locked_session,
            event_type=event_type,
            metadata_json=metadata or {},
        )
        if event_type in SWITCH_EVENT_TYPES:
            locked_session.switch_count += 1
            locked_session.save(update_fields=["switch_count", "updated_at"])
    return event
