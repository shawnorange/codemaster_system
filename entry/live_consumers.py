from __future__ import annotations

from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from .live_classroom import (
    LiveClassroomError,
    build_session_snapshot,
    get_accessible_session_for_user,
    get_visible_active_sessions_for_student,
    get_visible_teacher_ids_for_student,
    join_live_session_as_teacher,
    join_live_session_as_student,
    lobby_group_name_for_teacher,
    serialize_session,
    session_group_name,
    update_participant_screen_state,
    update_teacher_view_state,
)
from .models import ClassroomLiveParticipant, PortalUser
from .portal_context import get_student_by_user


@database_sync_to_async
def _get_portal_user(user_payload: dict[str, str]) -> PortalUser | None:
    if not user_payload:
        return None
    return (
        PortalUser.objects.filter(
            username=user_payload.get("username", ""),
            role=user_payload.get("role", ""),
            is_active=True,
        )
        .order_by("id")
        .first()
    )


@database_sync_to_async
def _build_lobby_groups_and_snapshot(user_payload: dict[str, str]) -> tuple[list[str], dict[str, Any]]:
    portal_user = (
        PortalUser.objects.filter(
            username=user_payload.get("username", ""),
            role=user_payload.get("role", ""),
            is_active=True,
        )
        .order_by("id")
        .first()
    )
    if portal_user is None:
        return [], {"active_sessions": []}

    if portal_user.role == PortalUser.ROLE_TEACHER:
        teacher_ids = [portal_user.id]
        active_sessions = []
    elif portal_user.role == PortalUser.ROLE_STUDENT:
        try:
            student = get_student_by_user(portal_user)
        except ObjectDoesNotExist:
            teacher_ids = []
            active_sessions = []
        else:
            teacher_ids = get_visible_teacher_ids_for_student(student)
            active_sessions = [serialize_session(session) for session in get_visible_active_sessions_for_student(student)]
    else:
        teacher_ids = []
        active_sessions = []

    return [lobby_group_name_for_teacher(teacher_id) for teacher_id in teacher_ids], {"active_sessions": active_sessions}


@database_sync_to_async
def _build_session_snapshot_for_user(user_payload: dict[str, str], session_id: int) -> tuple[dict[str, Any] | None, str]:
    portal_user = (
        PortalUser.objects.filter(
            username=user_payload.get("username", ""),
            role=user_payload.get("role", ""),
            is_active=True,
        )
        .order_by("id")
        .first()
    )
    if portal_user is None:
        return None, "not_authenticated"
    try:
        session = get_accessible_session_for_user(portal_user, session_id)
        if portal_user.role == PortalUser.ROLE_TEACHER:
            join_live_session_as_teacher(portal_user, session)
        elif portal_user.role == PortalUser.ROLE_STUDENT:
            join_live_session_as_student(portal_user, session)
        snapshot = build_session_snapshot(session)
    except ObjectDoesNotExist:
        return None, "permission_denied"
    except PermissionDenied:
        return None, "permission_denied"
    except LiveClassroomError as exc:
        return None, exc.code
    return snapshot, ""


@database_sync_to_async
def _update_screen_state(user_payload: dict[str, str], session_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    portal_user = PortalUser.objects.filter(
        username=user_payload.get("username", ""),
        role=user_payload.get("role", ""),
        is_active=True,
    ).first()
    if portal_user is None:
        raise PermissionDenied("未登录")
    session = get_accessible_session_for_user(portal_user, session_id)
    participant = update_participant_screen_state(
        portal_user,
        session,
        screen_state=str(payload.get("screen_state") or ClassroomLiveParticipant.SCREEN_NONE),
        display_surface=str(payload.get("display_surface") or ""),
    )
    return {"participant_id": participant.id}


@database_sync_to_async
def _update_teacher_view(user_payload: dict[str, str], session_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    portal_user = PortalUser.objects.filter(
        username=user_payload.get("username", ""),
        role=user_payload.get("role", ""),
        is_active=True,
    ).first()
    if portal_user is None:
        raise PermissionDenied("未登录")
    session = get_accessible_session_for_user(portal_user, session_id)
    spotlight_id = payload.get("spotlight_participant_id")
    pinned_id = payload.get("pinned_participant_id")
    update_teacher_view_state(
        portal_user,
        session,
        view_mode=str(payload.get("view_mode") or ""),
        spotlight_participant_id=int(spotlight_id) if spotlight_id else None,
        pinned_participant_id=int(pinned_id) if pinned_id else None,
    )
    return {"session_id": session_id}


class ClassroomLobbyConsumer(AsyncJsonWebsocketConsumer):
    groups_to_join: list[str]

    async def connect(self) -> None:
        user_payload = self.scope.get("codemaster_user")
        if not user_payload:
            await self.close(code=4401)
            return

        self.groups_to_join, snapshot = await _build_lobby_groups_and_snapshot(user_payload)
        if not self.groups_to_join:
            await self.close(code=4403)
            return

        for group in self.groups_to_join:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()
        await self.send_json({"event": "lobby_snapshot", "payload": snapshot})

    async def disconnect(self, close_code: int) -> None:
        for group in getattr(self, "groups_to_join", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def classroom_event(self, event: dict[str, Any]) -> None:
        event_name = event.get("event", "")
        user_payload = self.scope.get("codemaster_user") or {}
        if event_name == "activity_summary_updated" and user_payload.get("role") != PortalUser.ROLE_TEACHER:
            return
        await self.send_json({"event": event_name, "payload": event.get("payload", {})})


class ClassroomSessionConsumer(AsyncJsonWebsocketConsumer):
    session_id: int
    session_group: str

    async def connect(self) -> None:
        user_payload = self.scope.get("codemaster_user")
        if not user_payload:
            await self.close(code=4401)
            return

        self.session_id = int(self.scope["url_route"]["kwargs"]["session_id"])
        snapshot, error_code = await _build_session_snapshot_for_user(user_payload, self.session_id)
        if snapshot is None:
            await self.close(code=4403 if error_code == "permission_denied" else 4404)
            return

        self.session_group = session_group_name(self.session_id)
        await self.channel_layer.group_add(self.session_group, self.channel_name)
        await self.accept()
        await self.send_json({"event": "session_snapshot", "payload": snapshot})

    async def disconnect(self, close_code: int) -> None:
        if getattr(self, "session_group", ""):
            await self.channel_layer.group_discard(self.session_group, self.channel_name)

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        user_payload = self.scope.get("codemaster_user")
        if not user_payload:
            await self.close(code=4401)
            return
        event = str(content.get("event") or "").strip()
        payload = content.get("payload") if isinstance(content.get("payload"), dict) else {}
        try:
            if event == "screen_state":
                await _update_screen_state(user_payload, self.session_id, payload)
            elif event == "teacher_view":
                await _update_teacher_view(user_payload, self.session_id, payload)
            else:
                await self.send_json({"event": "error", "payload": {"error": "unknown_event"}})
        except ValueError as exc:
            await self.send_json({"event": "error", "payload": {"error": str(exc)}})
        except (PermissionDenied, LiveClassroomError) as exc:
            await self.send_json({"event": "error", "payload": {"error": getattr(exc, "message", str(exc))}})

    async def classroom_event(self, event: dict[str, Any]) -> None:
        await self.send_json({"event": event.get("event", ""), "payload": event.get("payload", {})})
