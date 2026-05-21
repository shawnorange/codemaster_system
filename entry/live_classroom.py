from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from .models import (
    ClassroomLiveActivity,
    ClassroomLiveActivityResponse,
    ClassroomLiveParticipant,
    ClassroomLiveRecording,
    ClassroomLiveSession,
    PortalUser,
    Student,
    TeacherStudentAssignment,
)
from .portal_context import get_student_by_user


class LiveClassroomError(Exception):
    def __init__(self, message: str, *, code: str = "live_classroom_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class LiveKitJoinToken:
    token: str
    livekit_url: str
    room_name: str
    identity: str


ANSWER_OPTIONS = ("A", "B", "C", "D", "E", "F")
TRUE_FALSE_OPTIONS = {"true": "正确", "false": "错误"}


def lobby_group_name_for_teacher(teacher_id: int) -> str:
    return f"classroom_lobby_teacher_{int(teacher_id)}"


def session_group_name(session_id: int) -> str:
    return f"classroom_session_{int(session_id)}"


def broadcast_lobby_event(teacher_id: int, event: str, payload: dict[str, Any]) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        lobby_group_name_for_teacher(teacher_id),
        {
            "type": "classroom.event",
            "event": event,
            "payload": payload,
        },
    )


def broadcast_session_event(session_id: int, event: str, payload: dict[str, Any]) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        session_group_name(session_id),
        {
            "type": "classroom.event",
            "event": event,
            "payload": payload,
        },
    )


def get_teacher_active_session(teacher: PortalUser) -> ClassroomLiveSession | None:
    return (
        ClassroomLiveSession.objects.select_related("teacher", "spotlight_participant", "pinned_participant")
        .filter(teacher=teacher, status=ClassroomLiveSession.STATUS_ACTIVE)
        .order_by("-started_at", "-id")
        .first()
    )


def get_visible_teacher_ids_for_student(student: Student) -> list[int]:
    teacher_ids = set(
        TeacherStudentAssignment.objects.filter(student=student, is_active=True)
        .values_list("teacher_id", flat=True)
    )
    if student.teacher_user_id:
        teacher_ids.add(student.teacher_user_id)
    return sorted(teacher_ids)


def get_visible_active_sessions_for_student(student: Student) -> list[ClassroomLiveSession]:
    teacher_ids = get_visible_teacher_ids_for_student(student)
    if not teacher_ids:
        return []
    return list(
        ClassroomLiveSession.objects.select_related("teacher")
        .filter(teacher_id__in=teacher_ids, status=ClassroomLiveSession.STATUS_ACTIVE)
        .order_by("-started_at", "-id")
    )


def teacher_can_access_session(teacher: PortalUser, session: ClassroomLiveSession) -> bool:
    return teacher.role == PortalUser.ROLE_TEACHER and session.teacher_id == teacher.id


def student_can_access_session(student: Student, session: ClassroomLiveSession) -> bool:
    if session.status != ClassroomLiveSession.STATUS_ACTIVE:
        return False
    return session.teacher_id in get_visible_teacher_ids_for_student(student)


def create_live_session(teacher: PortalUser) -> ClassroomLiveSession:
    if teacher.role != PortalUser.ROLE_TEACHER:
        raise PermissionDenied("只有老师可以开始实时课堂。")

    with transaction.atomic():
        existing = (
            ClassroomLiveSession.objects.select_for_update()
            .filter(teacher=teacher, status=ClassroomLiveSession.STATUS_ACTIVE)
            .order_by("-started_at", "-id")
            .first()
        )
        if existing:
            return existing

        session = ClassroomLiveSession.objects.create(
            teacher=teacher,
            title=f"{teacher.full_name} 实时课堂",
            livekit_room_name=ClassroomLiveSession.build_room_name(teacher),
        )
        ClassroomLiveParticipant.objects.create(
            session=session,
            portal_user=teacher,
            role=ClassroomLiveParticipant.ROLE_TEACHER,
            livekit_identity=ClassroomLiveParticipant.build_identity(teacher, session),
            connection_state=ClassroomLiveParticipant.CONNECTION_JOINED,
            joined_at=timezone.now(),
        )

    broadcast_lobby_event(teacher.id, "session_started", serialize_session(session))
    broadcast_session_event(session.id, "session_state", build_session_snapshot(session))
    return session


def join_live_session_as_teacher(teacher: PortalUser, session: ClassroomLiveSession) -> ClassroomLiveParticipant:
    if not teacher_can_access_session(teacher, session):
        raise PermissionDenied("无权加入该课堂。")
    participant, _ = ClassroomLiveParticipant.objects.get_or_create(
        session=session,
        portal_user=teacher,
        defaults={
            "role": ClassroomLiveParticipant.ROLE_TEACHER,
            "livekit_identity": ClassroomLiveParticipant.build_identity(teacher, session),
        },
    )
    update_fields: list[str] = []
    if participant.role != ClassroomLiveParticipant.ROLE_TEACHER:
        participant.role = ClassroomLiveParticipant.ROLE_TEACHER
        update_fields.append("role")
    if participant.mark_joined():
        update_fields.extend(["connection_state", "joined_at", "left_at"])
    if update_fields:
        update_fields.append("updated_at")
        participant.save(update_fields=sorted(set(update_fields)))
    return participant


def end_live_session(teacher: PortalUser, session: ClassroomLiveSession) -> ClassroomLiveSession:
    if not teacher_can_access_session(teacher, session):
        raise PermissionDenied("无权结束该课堂。")
    if session.end():
        session.save(update_fields=["status", "ended_at", "updated_at"])
    now = timezone.now()
    ClassroomLiveParticipant.objects.filter(
        session=session,
        connection_state=ClassroomLiveParticipant.CONNECTION_JOINED,
    ).update(
        connection_state=ClassroomLiveParticipant.CONNECTION_LEFT,
        left_at=now,
        updated_at=now,
    )
    broadcast_lobby_event(session.teacher_id, "session_ended", serialize_session(session))
    broadcast_session_event(session.id, "session_ended", serialize_session(session))
    return session


def join_live_session_as_student(student_user: PortalUser, session: ClassroomLiveSession) -> ClassroomLiveParticipant:
    if student_user.role != PortalUser.ROLE_STUDENT:
        raise PermissionDenied("只有学生可以加入课堂。")
    student = get_student_by_user(student_user)
    if not student_can_access_session(student, session):
        raise PermissionDenied("无权加入该课堂。")

    participant, created = ClassroomLiveParticipant.objects.get_or_create(
        session=session,
        portal_user=student_user,
        defaults={
            "student": student,
            "role": ClassroomLiveParticipant.ROLE_STUDENT,
            "livekit_identity": ClassroomLiveParticipant.build_identity(student_user, session),
            "screen_state": ClassroomLiveParticipant.SCREEN_PENDING,
        },
    )
    update_fields: list[str] = []
    if participant.student_id != student.id:
        participant.student = student
        update_fields.append("student")
    if participant.role != ClassroomLiveParticipant.ROLE_STUDENT:
        participant.role = ClassroomLiveParticipant.ROLE_STUDENT
        update_fields.append("role")
    if participant.screen_state == ClassroomLiveParticipant.SCREEN_NONE:
        participant.screen_state = ClassroomLiveParticipant.SCREEN_PENDING
        update_fields.append("screen_state")
    if participant.mark_joined():
        update_fields.extend(["connection_state", "joined_at", "left_at"])
    if update_fields:
        update_fields.append("updated_at")
        participant.save(update_fields=sorted(set(update_fields)))

    if created or update_fields:
        broadcast_session_event(session.id, "participant_joined", serialize_participant(participant))
    return participant


def mark_participant_left(portal_user: PortalUser, session: ClassroomLiveSession) -> None:
    participant = ClassroomLiveParticipant.objects.filter(session=session, portal_user=portal_user).first()
    if not participant:
        return
    if participant.mark_left():
        participant.save(update_fields=["connection_state", "left_at", "screen_state", "screen_stopped_at", "updated_at"])
        broadcast_session_event(session.id, "participant_left", serialize_participant(participant))


def update_participant_screen_state(
    portal_user: PortalUser,
    session: ClassroomLiveSession,
    *,
    screen_state: str,
    display_surface: str = "",
) -> ClassroomLiveParticipant:
    participant = ClassroomLiveParticipant.objects.filter(session=session, portal_user=portal_user).first()
    if not participant:
        raise PermissionDenied("未加入该课堂。")
    if participant.role != ClassroomLiveParticipant.ROLE_STUDENT:
        raise PermissionDenied("只有学生需要上报投屏状态。")
    participant.set_screen_state(screen_state=screen_state, display_surface=display_surface)
    participant.save(
        update_fields=[
            "screen_state",
            "display_surface",
            "screen_started_at",
            "screen_stopped_at",
            "updated_at",
        ]
    )
    broadcast_session_event(session.id, "screen_state_changed", serialize_participant(participant))
    return participant


def update_teacher_view_state(
    teacher: PortalUser,
    session: ClassroomLiveSession,
    *,
    view_mode: str,
    spotlight_participant_id: int | None = None,
    pinned_participant_id: int | None = None,
) -> ClassroomLiveSession:
    if not teacher_can_access_session(teacher, session):
        raise PermissionDenied("无权操作该课堂视图。")

    spotlight_participant = None
    pinned_participant = None
    if spotlight_participant_id:
        spotlight_participant = ClassroomLiveParticipant.objects.filter(
            id=spotlight_participant_id,
            session=session,
            role=ClassroomLiveParticipant.ROLE_STUDENT,
        ).first()
        if spotlight_participant is None:
            raise LiveClassroomError("未找到该学生投屏。", code="spotlight_participant_not_found")
    if pinned_participant_id:
        pinned_participant = ClassroomLiveParticipant.objects.filter(
            id=pinned_participant_id,
            session=session,
            role=ClassroomLiveParticipant.ROLE_STUDENT,
        ).first()
        if pinned_participant is None:
            raise LiveClassroomError("未找到该学生。", code="pinned_participant_not_found")

    session.set_teacher_view(
        view_mode=view_mode,
        spotlight_participant=spotlight_participant,
        pinned_participant=pinned_participant,
    )
    session.save(update_fields=["view_mode", "spotlight_participant", "pinned_participant", "updated_at"])
    broadcast_session_event(session.id, "teacher_view_changed", serialize_session(session))
    return session


def _clean_text(value: object, *, max_length: int = 10000) -> str:
    return str(value or "").strip()[:max_length]


def _normalize_activity_type(activity_type: object) -> str:
    normalized = _clean_text(activity_type, max_length=32)
    allowed = {choice[0] for choice in ClassroomLiveActivity.TYPE_CHOICES}
    if normalized not in allowed:
        raise LiveClassroomError("不支持的题型。", code="invalid_activity_type")
    return normalized


def _normalize_answer(answer: object, activity_type: str) -> str:
    normalized = _clean_text(answer, max_length=16)
    if activity_type == ClassroomLiveActivity.TYPE_SINGLE_CHOICE:
        normalized = normalized.upper()
        if normalized not in ANSWER_OPTIONS:
            raise LiveClassroomError("选择题答案必须是 A-F。", code="invalid_answer")
        return normalized
    if activity_type == ClassroomLiveActivity.TYPE_TRUE_FALSE:
        lowered = normalized.lower()
        truthy = {"true", "t", "yes", "y", "1", "正确", "对"}
        falsy = {"false", "f", "no", "n", "0", "错误", "错"}
        if lowered in truthy or normalized in truthy:
            return "true"
        if lowered in falsy or normalized in falsy:
            return "false"
    raise LiveClassroomError("判断题答案必须是正确或错误。", code="invalid_answer")


def _normalize_options(activity_type: str, options: object) -> dict[str, str]:
    if activity_type == ClassroomLiveActivity.TYPE_TRUE_FALSE:
        return TRUE_FALSE_OPTIONS.copy()
    if not isinstance(options, dict):
        raise LiveClassroomError("选择题需要提供选项。", code="invalid_options")
    normalized: dict[str, str] = {}
    for key in ANSWER_OPTIONS:
        value = _clean_text(options.get(key) or options.get(key.lower()), max_length=500)
        if value:
            normalized[key] = value
    if len(normalized) < 2:
        raise LiveClassroomError("选择题至少需要 2 个选项。", code="invalid_options")
    return normalized


def get_current_activity(session: ClassroomLiveSession) -> ClassroomLiveActivity | None:
    return (
        ClassroomLiveActivity.objects.filter(
            session=session,
            status=ClassroomLiveActivity.STATUS_PUBLISHED,
        )
        .order_by("-published_at", "-id")
        .first()
    )


def serialize_activity(activity: ClassroomLiveActivity | None, *, include_correct_answer: bool = False) -> dict[str, Any] | None:
    if activity is None:
        return None
    payload = {
        "id": activity.id,
        "session_id": activity.session_id,
        "activity_type": activity.activity_type,
        "title": activity.title,
        "prompt_text": activity.prompt_text,
        "options": activity.options_json or {},
        "status": activity.status,
        "has_correct_answer": bool(activity.correct_answer),
        "published_at": activity.published_at.isoformat() if activity.published_at else "",
        "closed_at": activity.closed_at.isoformat() if activity.closed_at else "",
    }
    if include_correct_answer:
        payload["correct_answer"] = activity.correct_answer
    return payload


def serialize_activity_response(
    response: ClassroomLiveActivityResponse | None,
    *,
    include_correctness: bool = False,
) -> dict[str, Any] | None:
    if response is None:
        return None
    payload = {
        "id": response.id,
        "activity_id": response.activity_id,
        "session_id": response.session_id,
        "participant_id": response.participant_id,
        "student_id": response.student_id,
        "answer": response.answer,
        "attempt_no": response.attempt_no,
        "submitted_at": response.created_at.isoformat() if response.created_at else "",
    }
    if include_correctness:
        payload["is_correct"] = response.is_correct
    return payload


def get_latest_activity_response_for_user(
    activity: ClassroomLiveActivity,
    student_user: PortalUser,
) -> ClassroomLiveActivityResponse | None:
    if student_user.role != PortalUser.ROLE_STUDENT:
        return None
    student = get_student_by_user(student_user)
    return (
        ClassroomLiveActivityResponse.objects.filter(activity=activity, student=student)
        .order_by("-created_at", "-id")
        .first()
    )


def serialize_activity_history(
    session: ClassroomLiveSession,
    *,
    student_user: PortalUser | None = None,
) -> list[dict[str, Any]]:
    activities = ClassroomLiveActivity.objects.filter(session=session).order_by("-published_at", "-id")
    if not student_user or student_user.role != PortalUser.ROLE_STUDENT:
        return [serialize_activity(activity) for activity in activities if activity]

    student = get_student_by_user(student_user)
    latest_by_activity: dict[int, ClassroomLiveActivityResponse] = {}
    responses = (
        ClassroomLiveActivityResponse.objects.filter(activity__in=activities, student=student)
        .order_by("-created_at", "-id")
    )
    for response in responses:
        if response.activity_id not in latest_by_activity:
            latest_by_activity[response.activity_id] = response

    history: list[dict[str, Any]] = []
    for activity in activities:
        payload = serialize_activity(activity)
        if payload:
            payload["latest_response"] = serialize_activity_response(latest_by_activity.get(activity.id))
            history.append(payload)
    return history


def _latest_responses_by_student(activity: ClassroomLiveActivity) -> dict[int, ClassroomLiveActivityResponse]:
    latest: dict[int, ClassroomLiveActivityResponse] = {}
    responses = (
        ClassroomLiveActivityResponse.objects.select_related("student", "participant")
        .filter(activity=activity)
        .order_by("-created_at", "-id")
    )
    for response in responses:
        if response.student_id not in latest:
            latest[response.student_id] = response
    return latest


def serialize_activity_summary(activity: ClassroomLiveActivity, *, include_correct_answer: bool = False) -> dict[str, Any]:
    participants = list(
        ClassroomLiveParticipant.objects.select_related("student", "portal_user")
        .filter(session=activity.session, role=ClassroomLiveParticipant.ROLE_STUDENT, student__isnull=False)
        .order_by("student__display_name", "id")
    )
    latest_by_student = _latest_responses_by_student(activity)
    attempt_counts = dict(
        ClassroomLiveActivityResponse.objects.filter(activity=activity)
        .values("student_id")
        .annotate(total=Count("id"))
        .values_list("student_id", "total")
    )
    answer_counts = {key: 0 for key in (activity.options_json or {}).keys()}
    students: list[dict[str, Any]] = []
    submitted_count = 0
    for participant in participants:
        latest = latest_by_student.get(participant.student_id)
        if latest:
            submitted_count += 1
            answer_counts[latest.answer] = answer_counts.get(latest.answer, 0) + 1
        students.append(
            {
                "participant_id": participant.id,
                "student_id": participant.student_id,
                "student_name": participant.student.display_name if participant.student else participant.portal_user.full_name,
                "latest_answer": latest.answer if latest else "",
                "is_correct": latest.is_correct if include_correct_answer and latest else None,
                "submitted_at": latest.created_at.isoformat() if latest else "",
                "attempt_count": attempt_counts.get(participant.student_id, 0),
            }
        )
    summary = {
        "activity_id": activity.id,
        "total_students": len(participants),
        "submitted_count": submitted_count,
        "unsubmitted_count": max(len(participants) - submitted_count, 0),
        "answer_counts": answer_counts,
        "students": students,
        "response_count": ClassroomLiveActivityResponse.objects.filter(activity=activity).count(),
    }
    if include_correct_answer:
        summary["correct_answer"] = activity.correct_answer
    return summary


def publish_live_activity(
    teacher: PortalUser,
    session: ClassroomLiveSession,
    *,
    activity_type: object,
    title: object,
    prompt_text: object,
    options: object,
    correct_answer: object = "",
) -> ClassroomLiveActivity:
    if not teacher_can_access_session(teacher, session):
        raise PermissionDenied("无权给该课堂布置任务。")
    if session.status != ClassroomLiveSession.STATUS_ACTIVE:
        raise LiveClassroomError("课堂已结束，不能布置任务。", code="session_not_active")

    normalized_type = _normalize_activity_type(activity_type)
    normalized_prompt = _clean_text(prompt_text)
    if not normalized_prompt:
        raise LiveClassroomError("题面不能为空。", code="prompt_required")
    normalized_options = _normalize_options(normalized_type, options)
    normalized_answer = ""
    if _clean_text(correct_answer, max_length=16):
        normalized_answer = _normalize_answer(correct_answer, normalized_type)
        if normalized_answer not in normalized_options:
            raise LiveClassroomError("正确答案必须属于当前选项。", code="invalid_correct_answer")
    normalized_title = _clean_text(title, max_length=128) or normalized_prompt.splitlines()[0][:64] or "课堂任务"

    with transaction.atomic():
        now = timezone.now()
        ClassroomLiveActivity.objects.select_for_update().filter(
            session=session,
            status=ClassroomLiveActivity.STATUS_PUBLISHED,
        ).update(status=ClassroomLiveActivity.STATUS_CLOSED, closed_at=now, updated_at=now)
        activity = ClassroomLiveActivity.objects.create(
            session=session,
            teacher=teacher,
            activity_type=normalized_type,
            title=normalized_title,
            prompt_text=normalized_prompt,
            options_json=normalized_options,
            correct_answer=normalized_answer,
        )

    broadcast_session_event(
        session.id,
        "activity_published",
        {
            "activity": serialize_activity(activity),
            "activity_history": serialize_activity_history(session),
        },
    )
    return activity


def submit_live_activity_response(
    student_user: PortalUser,
    session: ClassroomLiveSession,
    *,
    activity_id: int,
    answer: object,
) -> ClassroomLiveActivityResponse:
    if student_user.role != PortalUser.ROLE_STUDENT:
        raise PermissionDenied("只有学生可以提交课堂任务。")
    student = get_student_by_user(student_user)
    if not student_can_access_session(student, session):
        raise PermissionDenied("无权提交该课堂任务。")
    if session.status != ClassroomLiveSession.STATUS_ACTIVE:
        raise LiveClassroomError("课堂已结束，不能提交答案。", code="session_not_active")

    activity = (
        ClassroomLiveActivity.objects.select_related("session")
        .filter(id=activity_id, session=session)
        .first()
    )
    if activity is None:
        raise LiveClassroomError("课堂任务不存在。", code="activity_not_found")
    if activity.status != ClassroomLiveActivity.STATUS_PUBLISHED:
        raise LiveClassroomError("该课堂任务已关闭。", code="activity_closed")

    participant = ClassroomLiveParticipant.objects.filter(
        session=session,
        portal_user=student_user,
        role=ClassroomLiveParticipant.ROLE_STUDENT,
    ).first()
    if participant is None:
        participant = join_live_session_as_student(student_user, session)

    normalized_answer = _normalize_answer(answer, activity.activity_type)
    if normalized_answer not in (activity.options_json or {}):
        raise LiveClassroomError("答案不属于当前题目选项。", code="invalid_answer")
    attempt_no = (
        ClassroomLiveActivityResponse.objects.filter(activity=activity, student=student).count() + 1
    )
    correct_answer = activity.correct_answer or ""
    response = ClassroomLiveActivityResponse.objects.create(
        activity=activity,
        session=session,
        participant=participant,
        student=student,
        answer=normalized_answer,
        is_correct=(normalized_answer == correct_answer) if correct_answer else None,
        correct_answer_snapshot=correct_answer,
        attempt_no=attempt_no,
    )
    broadcast_session_event(
        session.id,
        "activity_summary_updated",
        {
            "activity_id": activity.id,
            "summary": serialize_activity_summary(activity),
        },
    )
    return response


def get_or_create_recording(session: ClassroomLiveSession) -> ClassroomLiveRecording:
    try:
        return ClassroomLiveRecording.objects.create(
            session=session,
            recording_type=ClassroomLiveRecording.TYPE_SCREEN,
            provider=ClassroomLiveRecording.PROVIDER_LIVEKIT_EGRESS,
        )
    except IntegrityError:
        existing = (
            ClassroomLiveRecording.objects.filter(
                session=session,
                recording_type=ClassroomLiveRecording.TYPE_SCREEN,
                status__in=[ClassroomLiveRecording.STATUS_STARTING, ClassroomLiveRecording.STATUS_ACTIVE],
            )
            .order_by("-started_at", "-id")
            .first()
        )
        if existing:
            return existing
        raise


def serialize_session(session: ClassroomLiveSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "title": session.title,
        "teacher_id": session.teacher_id,
        "teacher_name": session.teacher.full_name if getattr(session, "teacher", None) else "",
        "status": session.status,
        "workspace_type": session.workspace_type,
        "livekit_room_name": session.livekit_room_name,
        "view_mode": session.view_mode,
        "spotlight_participant_id": session.spotlight_participant_id,
        "pinned_participant_id": session.pinned_participant_id,
        "started_at": session.started_at.isoformat() if session.started_at else "",
        "ended_at": session.ended_at.isoformat() if session.ended_at else "",
    }


def serialize_participant(participant: ClassroomLiveParticipant) -> dict[str, Any]:
    return {
        "id": participant.id,
        "session_id": participant.session_id,
        "portal_user_id": participant.portal_user_id,
        "student_id": participant.student_id,
        "display_name": participant.portal_user.full_name if getattr(participant, "portal_user", None) else "",
        "student_name": participant.student.display_name if getattr(participant, "student", None) else "",
        "role": participant.role,
        "livekit_identity": participant.livekit_identity,
        "connection_state": participant.connection_state,
        "screen_state": participant.screen_state,
        "display_surface": participant.display_surface,
        "joined_at": participant.joined_at.isoformat() if participant.joined_at else "",
        "left_at": participant.left_at.isoformat() if participant.left_at else "",
    }


def serialize_recording(recording: ClassroomLiveRecording | None) -> dict[str, Any] | None:
    if recording is None:
        return None
    download_url = ""
    if recording.is_download_available():
        download_url = reverse("api-live-classroom-recording-download", args=[recording.id])
    return {
        "id": recording.id,
        "session_id": recording.session_id,
        "recording_type": recording.recording_type,
        "provider": recording.provider,
        "status": recording.status,
        "egress_id": recording.egress_id,
        "file_url": download_url,
        "file_path": "",
        "file_size": recording.file_size,
        "content_type": recording.content_type,
        "download_url": download_url,
        "is_download_available": bool(download_url),
        "error_message": recording.error_message,
        "started_at": recording.started_at.isoformat() if recording.started_at else "",
        "ended_at": recording.ended_at.isoformat() if recording.ended_at else "",
        "expires_at": recording.expires_at.isoformat() if recording.expires_at else "",
        "deleted_at": recording.deleted_at.isoformat() if recording.deleted_at else "",
    }


def build_session_snapshot(session: ClassroomLiveSession) -> dict[str, Any]:
    refreshed_session = (
        ClassroomLiveSession.objects.select_related("teacher", "spotlight_participant", "pinned_participant")
        .filter(id=session.id)
        .get()
    )
    participants = list(
        ClassroomLiveParticipant.objects.select_related("portal_user", "student")
        .filter(session=refreshed_session)
        .order_by("role", "student__display_name", "id")
    )
    recordings = list(
        ClassroomLiveRecording.objects.filter(session=refreshed_session)
        .order_by("-started_at", "-id")
    )
    recording = recordings[0] if recordings else None
    current_activity = get_current_activity(refreshed_session)
    return {
        "session": serialize_session(refreshed_session),
        "participants": [serialize_participant(participant) for participant in participants],
        "recordings": [serialize_recording(item) for item in recordings],
        "recording": serialize_recording(recording),
        "current_activity": serialize_activity(current_activity),
        "activity_history": serialize_activity_history(refreshed_session),
    }


def get_accessible_session_for_user(portal_user: PortalUser, session_id: int) -> ClassroomLiveSession:
    session = (
        ClassroomLiveSession.objects.select_related("teacher", "spotlight_participant", "pinned_participant")
        .filter(id=session_id)
        .first()
    )
    if session is None:
        raise LiveClassroomError("未找到课堂。", code="session_not_found")
    if portal_user.role == PortalUser.ROLE_TEACHER and teacher_can_access_session(portal_user, session):
        return session
    if portal_user.role == PortalUser.ROLE_STUDENT:
        student = get_student_by_user(portal_user)
        if student_can_access_session(student, session):
            return session
    raise PermissionDenied("无权访问该课堂。")


def build_livekit_join_token(portal_user: PortalUser, session: ClassroomLiveSession) -> LiveKitJoinToken:
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise LiveClassroomError("LiveKit API Key/Secret 未配置。", code="livekit_not_configured")

    participant = ClassroomLiveParticipant.objects.filter(session=session, portal_user=portal_user).first()
    if participant is None:
        if portal_user.role == PortalUser.ROLE_TEACHER and teacher_can_access_session(portal_user, session):
            participant = join_live_session_as_teacher(portal_user, session)
        elif portal_user.role == PortalUser.ROLE_STUDENT:
            participant = join_live_session_as_student(portal_user, session)
        else:
            raise PermissionDenied("无权加入该课堂。")
    elif participant.role == ClassroomLiveParticipant.ROLE_TEACHER and participant.mark_joined():
        participant.save(update_fields=["connection_state", "joined_at", "left_at", "updated_at"])
    elif participant.role == ClassroomLiveParticipant.ROLE_STUDENT and participant.connection_state != ClassroomLiveParticipant.CONNECTION_JOINED:
        participant = join_live_session_as_student(portal_user, session)

    try:
        from livekit import api
    except ImportError as exc:
        raise LiveClassroomError("livekit-api 依赖未安装。", code="livekit_dependency_missing") from exc

    grants = api.VideoGrants(
        room_join=True,
        room=session.livekit_room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(participant.livekit_identity)
        .with_name(portal_user.full_name or portal_user.username)
        .with_grants(grants)
        .to_jwt()
    )
    return LiveKitJoinToken(
        token=token,
        livekit_url=settings.LIVEKIT_PUBLIC_URL,
        room_name=session.livekit_room_name,
        identity=participant.livekit_identity,
    )
