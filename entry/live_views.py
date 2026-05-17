from __future__ import annotations

import json

from django.core.exceptions import PermissionDenied
from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from .auth import ROLE_CONFIG, get_authenticated_user, role_required
from .live_classroom import (
    LiveClassroomError,
    build_livekit_join_token,
    build_session_snapshot,
    create_live_session,
    end_live_session,
    get_accessible_session_for_user,
    get_teacher_active_session,
    get_visible_active_sessions_for_student,
    join_live_session_as_student,
    serialize_recording,
)
from .live_recording import (
    complete_browser_audio_recording,
    complete_browser_screen_recording,
    fail_active_browser_audio_recording,
    fail_active_browser_screen_recording,
    resolve_recording_file_path,
    start_browser_audio_recording,
    start_browser_screen_recording,
    start_live_recording,
    stop_live_recording,
)
from .models import ClassroomLiveRecording, ClassroomLiveSession, PortalUser
from .portal_context import get_student_by_user


def _get_portal_user_from_request(request: HttpRequest) -> PortalUser:
    return PortalUser.objects.get(
        username=request.codemaster_user["username"],
        role=request.codemaster_user["role"],
        is_active=True,
    )


def _build_shell_identity_context(request: HttpRequest) -> dict[str, str]:
    user = request.codemaster_user
    return {
        "viewer_display_name": user.get("full_name") or user["username"],
        "viewer_username": user["username"],
        "account_settings_href": reverse("student-account-settings") if user["role"] == "student" else "",
    }


def _redirect_with_session(session: ClassroomLiveSession, route_name: str) -> HttpResponse:
    return redirect(f"{reverse(route_name)}?session_id={session.id}")


def _json_error(message: str, *, status: int, code: str = "error") -> JsonResponse:
    return JsonResponse({"error": message, "error_code": code}, status=status)


def _load_json_body(request: HttpRequest) -> dict:
    content_type = str(request.content_type or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return {}
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_int(value: object) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except (TypeError, ValueError):
        return 0


def _require_api_user(request: HttpRequest) -> PortalUser | JsonResponse:
    user = get_authenticated_user(request)
    if not user:
        return _json_error("未登录", status=401, code="not_authenticated")
    request.codemaster_user = user
    return _get_portal_user_from_request(request)


@role_required("teacher")
def teacher_live_classroom(request: HttpRequest) -> HttpResponse:
    teacher = _get_portal_user_from_request(request)
    if request.method == "POST":
        action = str(request.POST.get("form_action") or "").strip()
        if action == "start":
            session = create_live_session(teacher)
            return _redirect_with_session(session, "teacher-live-classroom")
        session_id = _parse_int(request.POST.get("session_id"))
        session = get_accessible_session_for_user(teacher, session_id) if session_id else get_teacher_active_session(teacher)
        if session and action == "end":
            fail_active_browser_screen_recording(session, "课堂已结束，浏览器录屏未停止上传，未生成录屏文件。")
            stop_live_recording(session)
            fail_active_browser_audio_recording(session, "课堂已结束，浏览器录音未停止上传，未生成录音文件。")
            end_live_session(teacher, session)
            return redirect("teacher-live-classroom")
        if session and action in {"start_recording", "start_screen_recording"}:
            start_live_recording(session)
            return _redirect_with_session(session, "teacher-live-classroom")
        if session and action in {"stop_recording", "stop_screen_recording"}:
            stop_live_recording(session)
            return _redirect_with_session(session, "teacher-live-classroom")

    requested_session_id = _parse_int(request.GET.get("session_id"))
    active_session = get_teacher_active_session(teacher)
    selected_session = active_session
    if requested_session_id:
        try:
            selected_session = get_accessible_session_for_user(teacher, requested_session_id)
        except (LiveClassroomError, PermissionDenied):
            selected_session = active_session

    snapshot = build_session_snapshot(selected_session) if selected_session else None
    return render(
        request,
        "entry/teacher_live_classroom.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            "page_title": "实时课堂",
            "page_description": "第一版只承接开课、学生整屏投屏、老师主屏切换和 LiveKit 录制。",
            "active_session": selected_session,
            "snapshot": snapshot,
            "snapshot_json": json.dumps(snapshot or {}, ensure_ascii=False),
            "livekit_client_js_url": settings.LIVEKIT_CLIENT_JS_URL,
            **_build_shell_identity_context(request),
        },
    )


@role_required("student")
def student_live_classroom(request: HttpRequest) -> HttpResponse:
    student_user = _get_portal_user_from_request(request)
    student = get_student_by_user(student_user)
    selected_session = None
    error_message = ""
    if request.method == "POST":
        session_id = _parse_int(request.POST.get("session_id"))
        try:
            selected_session = get_accessible_session_for_user(student_user, session_id)
            join_live_session_as_student(student_user, selected_session)
            return _redirect_with_session(selected_session, "student-live-classroom")
        except (LiveClassroomError, PermissionDenied) as exc:
            error_message = getattr(exc, "message", str(exc))

    requested_session_id = _parse_int(request.GET.get("session_id"))
    if requested_session_id:
        try:
            selected_session = get_accessible_session_for_user(student_user, requested_session_id)
            join_live_session_as_student(student_user, selected_session)
        except (LiveClassroomError, PermissionDenied) as exc:
            error_message = getattr(exc, "message", str(exc))
            selected_session = None

    active_sessions = get_visible_active_sessions_for_student(student)
    snapshot = build_session_snapshot(selected_session) if selected_session else None
    return render(
        request,
        "entry/student_live_classroom.html",
        {
            "role_label": ROLE_CONFIG["student"]["label"],
            "page_title": "实时课堂",
            "page_description": "加入课堂后必须共享整个电脑屏幕，不能只共享窗口或浏览器标签页。",
            "active_sessions": active_sessions,
            "active_session": selected_session,
            "error_message": error_message,
            "snapshot": snapshot,
            "snapshot_json": json.dumps(snapshot or {}, ensure_ascii=False),
            "livekit_client_js_url": settings.LIVEKIT_CLIENT_JS_URL,
            **_build_shell_identity_context(request),
        },
    )


def api_live_classroom_token(request: HttpRequest, session_id: int) -> JsonResponse:
    portal_user_or_response = _require_api_user(request)
    if isinstance(portal_user_or_response, JsonResponse):
        return portal_user_or_response
    portal_user = portal_user_or_response
    if request.method != "POST":
        return _json_error("只支持 POST。", status=405, code="method_not_allowed")
    try:
        session = get_accessible_session_for_user(portal_user, session_id)
        token = build_livekit_join_token(portal_user, session)
    except PermissionDenied as exc:
        return _json_error(str(exc), status=403, code="permission_denied")
    except LiveClassroomError as exc:
        status = 404 if exc.code == "session_not_found" else 503
        return _json_error(exc.message, status=status, code=exc.code)
    return JsonResponse(
        {
            "token": token.token,
            "livekit_url": token.livekit_url,
            "room_name": token.room_name,
            "identity": token.identity,
        }
    )


def api_live_classroom_recording(request: HttpRequest, session_id: int) -> JsonResponse:
    portal_user_or_response = _require_api_user(request)
    if isinstance(portal_user_or_response, JsonResponse):
        return portal_user_or_response
    teacher = portal_user_or_response
    if teacher.role != PortalUser.ROLE_TEACHER:
        return _json_error("只有老师可以操作录制。", status=403, code="permission_denied")
    if request.method != "POST":
        return _json_error("只支持 POST。", status=405, code="method_not_allowed")
    try:
        session = get_accessible_session_for_user(teacher, session_id)
    except PermissionDenied as exc:
        return _json_error(str(exc), status=403, code="permission_denied")
    except LiveClassroomError as exc:
        return _json_error(exc.message, status=404, code=exc.code)

    payload = _load_json_body(request)
    action = str(payload.get("action") or request.POST.get("action") or "").strip()
    if action in {"start", "start_audio"}:
        recording = start_browser_audio_recording(session)
    elif action == "start_screen":
        recording = start_browser_screen_recording(session)
    elif action == "start_livekit":
        recording = start_live_recording(session)
    elif action == "upload_audio":
        recording_id = _parse_int(request.POST.get("recording_id"))
        try:
            recording = complete_browser_audio_recording(
                session,
                recording_id=recording_id,
                uploaded_file=request.FILES.get("audio"),
            )
        except LiveClassroomError as exc:
            return _json_error(exc.message, status=404, code=exc.code)
    elif action == "upload_screen":
        recording_id = _parse_int(request.POST.get("recording_id"))
        try:
            recording = complete_browser_screen_recording(
                session,
                recording_id=recording_id,
                uploaded_file=request.FILES.get("screen"),
            )
        except LiveClassroomError as exc:
            return _json_error(exc.message, status=404, code=exc.code)
    elif action in {"stop", "stop_screen"}:
        recording = stop_live_recording(session)
    else:
        return _json_error("未知录制操作。", status=400, code="invalid_action")
    if recording is None:
        return JsonResponse({"recording": None})
    return JsonResponse(
        {
            "recording": serialize_recording(recording),
        }
    )


def api_live_classroom_recording_download(request: HttpRequest, recording_id: int) -> HttpResponse:
    portal_user_or_response = _require_api_user(request)
    if isinstance(portal_user_or_response, JsonResponse):
        return portal_user_or_response
    teacher = portal_user_or_response
    if teacher.role != PortalUser.ROLE_TEACHER:
        return _json_error("只有老师可以下载课堂录制。", status=403, code="permission_denied")
    if request.method != "GET":
        return _json_error("只支持 GET。", status=405, code="method_not_allowed")

    recording = (
        ClassroomLiveRecording.objects.select_related("session", "session__teacher")
        .filter(id=recording_id)
        .first()
    )
    if recording is None or recording.session.teacher_id != teacher.id:
        return _json_error("录制文件不存在或已失效。", status=404, code="recording_not_found")
    if not recording.is_download_available():
        return _json_error("录制文件不存在或已失效。", status=404, code="recording_not_available")

    file_path = resolve_recording_file_path(recording)
    if file_path is None or not file_path.exists() or not file_path.is_file():
        return _json_error("录制文件不存在或已失效。", status=404, code="recording_file_missing")

    filename = file_path.name
    return FileResponse(
        file_path.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type=recording.content_type or "application/octet-stream",
    )
