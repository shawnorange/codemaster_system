from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import (
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
    return {
        "session": serialize_session(refreshed_session),
        "participants": [serialize_participant(participant) for participant in participants],
        "recordings": [serialize_recording(item) for item in recordings],
        "recording": serialize_recording(recording),
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
