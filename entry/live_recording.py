from __future__ import annotations

import re
from pathlib import Path
from pathlib import PurePosixPath

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from .live_classroom import LiveClassroomError, broadcast_session_event, serialize_recording
from .models import ClassroomLiveRecording, ClassroomLiveSession


def build_recording_file_path(session: ClassroomLiveSession) -> str:
    prefix = str(settings.LIVEKIT_RECORDING_FILE_PREFIX or "/recordings").strip() or "/recordings"
    filename = f"classroom-{session.id}-screen-{timezone.now().strftime('%Y%m%d-%H%M%S-%f')}.mp4"
    return str(PurePosixPath(prefix) / filename)


def build_recording_public_url(file_path: str) -> str:
    public_prefix = str(settings.LIVEKIT_RECORDING_PUBLIC_URL_PREFIX or "").strip().rstrip("/")
    recording_prefix = str(settings.LIVEKIT_RECORDING_FILE_PREFIX or "").strip().rstrip("/")
    normalized_path = str(file_path or "").strip()
    if not public_prefix or not recording_prefix or not normalized_path.startswith(recording_prefix):
        return ""
    relative_path = normalized_path.removeprefix(recording_prefix).lstrip("/")
    if not relative_path:
        return ""
    return f"{public_prefix}/{relative_path}"


def resolve_recording_file_path(recording: ClassroomLiveRecording) -> Path | None:
    raw_path = str(recording.file_path or "").strip()
    if not raw_path:
        return None

    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    recording_prefix = str(settings.LIVEKIT_RECORDING_FILE_PREFIX or "").strip().rstrip("/")
    if recording_prefix and raw_path.startswith(recording_prefix):
        relative_path = raw_path.removeprefix(recording_prefix).lstrip("/")
        return (media_root / "live-classroom" / relative_path).resolve(strict=False)

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = media_root / candidate
    candidate = candidate.resolve(strict=False)
    if not candidate.is_relative_to(media_root):
        return None
    return candidate


def _active_recording_for_session(
    session: ClassroomLiveSession,
    recording_type: str,
) -> ClassroomLiveRecording | None:
    return (
        ClassroomLiveRecording.objects.filter(
            session=session,
            recording_type=recording_type,
            status__in=[ClassroomLiveRecording.STATUS_STARTING, ClassroomLiveRecording.STATUS_ACTIVE],
        )
        .order_by("-started_at", "-id")
        .first()
    )


def _browser_audio_extension(uploaded_file: UploadedFile) -> str:
    content_type = str(uploaded_file.content_type or "").lower()
    filename = str(uploaded_file.name or "").lower()
    if "mp4" in content_type or filename.endswith((".mp4", ".m4a")):
        return ".m4a"
    if "ogg" in content_type or filename.endswith(".ogg"):
        return ".ogg"
    return ".webm"


def _browser_video_extension(uploaded_file: UploadedFile) -> str:
    content_type = str(uploaded_file.content_type or "").lower()
    filename = str(uploaded_file.name or "").lower()
    if "mp4" in content_type or filename.endswith(".mp4"):
        return ".mp4"
    return ".webm"


def _browser_media_public_url(path: Path) -> str:
    relative_path = path.relative_to(Path(settings.MEDIA_ROOT)).as_posix()
    return f"{settings.MEDIA_URL.rstrip('/')}/{relative_path}"


def _safe_recording_teacher_name(session: ClassroomLiveSession) -> str:
    teacher = session.teacher
    raw_name = str(teacher.full_name or teacher.username or f"teacher-{teacher.id}").strip()
    safe_name = re.sub(r'[\\/\0\r\n\t:*?"<>|]+', "_", raw_name).strip(" ._")
    return safe_name or f"teacher-{teacher.id}"


def _recording_filename(session: ClassroomLiveSession, extension: str) -> str:
    recorded_at = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H:%M:%S")
    return f"{recorded_at}{_safe_recording_teacher_name(session)}{extension}"


def _unique_output_path(output_dir: Path, filename: str) -> Path:
    output_path = output_dir / filename
    if not output_path.exists():
        return output_path

    stem = output_path.stem
    suffix = output_path.suffix
    for index in range(2, 1000):
        candidate = output_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    return output_dir / f"{stem}-{timezone.now().strftime('%f')}{suffix}"


def start_browser_audio_recording(session: ClassroomLiveSession) -> ClassroomLiveRecording:
    recording = _active_recording_for_session(session, ClassroomLiveRecording.TYPE_AUDIO)
    if recording:
        return recording

    recording = ClassroomLiveRecording.objects.create(
        session=session,
        recording_type=ClassroomLiveRecording.TYPE_AUDIO,
        provider=ClassroomLiveRecording.PROVIDER_BROWSER_AUDIO,
    )
    recording.mark_active()
    recording.save(update_fields=["recording_type", "provider", "status", "error_message", "updated_at"])
    broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
    return recording


def complete_browser_audio_recording(
    session: ClassroomLiveSession,
    *,
    recording_id: int,
    uploaded_file: UploadedFile | None,
) -> ClassroomLiveRecording:
    recording = ClassroomLiveRecording.objects.filter(
        id=recording_id,
        session=session,
        recording_type=ClassroomLiveRecording.TYPE_AUDIO,
        provider=ClassroomLiveRecording.PROVIDER_BROWSER_AUDIO,
    ).first()
    if recording is None:
        raise LiveClassroomError("未找到浏览器录音记录。", code="recording_not_found")
    if uploaded_file is None or uploaded_file.size <= 0:
        recording.mark_failed("录音文件为空。")
        recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
        broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
        return recording

    output_dir = Path(settings.MEDIA_ROOT) / "live-classroom" / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    extension = _browser_audio_extension(uploaded_file)
    output_path = _unique_output_path(output_dir, _recording_filename(session, extension))
    with output_path.open("wb") as target:
        for chunk in uploaded_file.chunks():
            target.write(chunk)

    recording.mark_completed(
        file_path=str(output_path),
        file_url=_browser_media_public_url(output_path),
        file_size=output_path.stat().st_size,
        content_type=str(uploaded_file.content_type or ""),
    )
    recording.save(
        update_fields=[
            "status",
            "file_path",
            "file_url",
            "file_size",
            "content_type",
            "ended_at",
            "expires_at",
            "updated_at",
        ]
    )
    broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
    return recording


def start_browser_screen_recording(session: ClassroomLiveSession) -> ClassroomLiveRecording:
    recording = _active_recording_for_session(session, ClassroomLiveRecording.TYPE_SCREEN)
    if recording:
        return recording

    recording = ClassroomLiveRecording.objects.create(
        session=session,
        recording_type=ClassroomLiveRecording.TYPE_SCREEN,
        provider=ClassroomLiveRecording.PROVIDER_BROWSER_SCREEN,
    )
    recording.mark_active()
    recording.save(update_fields=["recording_type", "provider", "status", "error_message", "updated_at"])
    broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
    return recording


def complete_browser_screen_recording(
    session: ClassroomLiveSession,
    *,
    recording_id: int,
    uploaded_file: UploadedFile | None,
) -> ClassroomLiveRecording:
    recording = ClassroomLiveRecording.objects.filter(
        id=recording_id,
        session=session,
        recording_type=ClassroomLiveRecording.TYPE_SCREEN,
        provider=ClassroomLiveRecording.PROVIDER_BROWSER_SCREEN,
    ).first()
    if recording is None:
        raise LiveClassroomError("未找到浏览器录屏记录。", code="recording_not_found")
    if uploaded_file is None or uploaded_file.size <= 0:
        recording.mark_failed("录屏文件为空。")
        recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
        broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
        return recording

    output_dir = Path(settings.MEDIA_ROOT) / "live-classroom" / "screen"
    output_dir.mkdir(parents=True, exist_ok=True)
    extension = _browser_video_extension(uploaded_file)
    output_path = _unique_output_path(output_dir, _recording_filename(session, extension))
    with output_path.open("wb") as target:
        for chunk in uploaded_file.chunks():
            target.write(chunk)

    recording.mark_completed(
        file_path=str(output_path),
        file_url=_browser_media_public_url(output_path),
        file_size=output_path.stat().st_size,
        content_type=str(uploaded_file.content_type or ""),
    )
    recording.save(
        update_fields=[
            "status",
            "file_path",
            "file_url",
            "file_size",
            "content_type",
            "ended_at",
            "expires_at",
            "updated_at",
        ]
    )
    broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
    return recording


def fail_active_browser_audio_recording(session: ClassroomLiveSession, message: str) -> ClassroomLiveRecording | None:
    recording = _active_recording_for_session(session, ClassroomLiveRecording.TYPE_AUDIO)
    if recording is None:
        return None
    if recording.provider != ClassroomLiveRecording.PROVIDER_BROWSER_AUDIO:
        return None
    recording.mark_failed(message)
    recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
    broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
    return recording


def fail_active_browser_screen_recording(session: ClassroomLiveSession, message: str) -> ClassroomLiveRecording | None:
    recording = _active_recording_for_session(session, ClassroomLiveRecording.TYPE_SCREEN)
    if recording is None:
        return None
    if recording.provider != ClassroomLiveRecording.PROVIDER_BROWSER_SCREEN:
        return None
    recording.mark_failed(message)
    recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
    broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
    return recording


async def _start_room_composite_egress(session: ClassroomLiveSession, filepath: str) -> str:
    from livekit import api

    livekit_api = api.LiveKitAPI(
        url=settings.LIVEKIT_SERVER_URL,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )
    try:
        request = api.RoomCompositeEgressRequest(
            room_name=session.livekit_room_name,
            layout="grid",
            file_outputs=[api.EncodedFileOutput(filepath=filepath)],
        )
        response = await livekit_api.egress.start_room_composite_egress(request)
        return str(getattr(response, "egress_id", "") or "")
    finally:
        close = getattr(livekit_api, "aclose", None)
        if close:
            await close()


async def _stop_egress(egress_id: str) -> None:
    from livekit import api

    livekit_api = api.LiveKitAPI(
        url=settings.LIVEKIT_SERVER_URL,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )
    try:
        await livekit_api.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
    finally:
        close = getattr(livekit_api, "aclose", None)
        if close:
            await close()


def start_live_recording(session: ClassroomLiveSession) -> ClassroomLiveRecording:
    recording = _active_recording_for_session(session, ClassroomLiveRecording.TYPE_SCREEN)
    if recording:
        return recording

    recording = ClassroomLiveRecording.objects.create(
        session=session,
        recording_type=ClassroomLiveRecording.TYPE_SCREEN,
        provider=ClassroomLiveRecording.PROVIDER_LIVEKIT_EGRESS,
    )
    if not settings.LIVEKIT_RECORDING_ENABLED:
        recording.mark_failed("LiveKit 录制未启用。请设置 LIVEKIT_RECORDING_ENABLED=True。")
        recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
        broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
        return recording
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        recording.mark_failed("LiveKit API Key/Secret 未配置。")
        recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
        broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
        return recording

    filepath = build_recording_file_path(session)
    recording.file_path = filepath
    recording.save(update_fields=["file_path", "updated_at"])
    try:
        egress_id = async_to_sync(_start_room_composite_egress)(session, filepath)
    except Exception as exc:  # External SDK errors vary by transport and LiveKit version.
        recording.mark_failed(str(exc))
        recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
    else:
        recording.mark_active(egress_id=egress_id)
        recording.save(update_fields=["status", "egress_id", "error_message", "updated_at"])
    broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
    return recording


def stop_live_recording(session: ClassroomLiveSession) -> ClassroomLiveRecording | None:
    recording = _active_recording_for_session(session, ClassroomLiveRecording.TYPE_SCREEN)
    if recording is None:
        return None
    if recording.provider != ClassroomLiveRecording.PROVIDER_LIVEKIT_EGRESS:
        recording.mark_failed(
            "浏览器录屏尚未上传文件。请在当前页面点击停止录屏生成文件；如果刷新过页面，临时录屏数据已经丢失。"
        )
        recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
        broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
        return recording

    if recording.egress_id:
        try:
            async_to_sync(_stop_egress)(recording.egress_id)
        except Exception as exc:  # External SDK errors vary by transport and LiveKit version.
            recording.mark_failed(str(exc))
            recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
            broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
            return recording

    storage_path = resolve_recording_file_path(recording)
    file_size = storage_path.stat().st_size if storage_path and storage_path.exists() else 0
    if file_size <= 0:
        recording.mark_failed("录屏文件未生成。请先共享老师或学生屏幕，并等待画面出现后再开始录屏。")
        recording.save(update_fields=["status", "error_message", "ended_at", "updated_at"])
        broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
        return recording

    recording.mark_completed(
        file_path=recording.file_path,
        file_url=build_recording_public_url(recording.file_path),
        file_size=file_size,
        content_type="video/mp4",
    )
    recording.save(
        update_fields=[
            "status",
            "file_path",
            "file_url",
            "file_size",
            "content_type",
            "ended_at",
            "expires_at",
            "updated_at",
        ]
    )
    broadcast_session_event(session.id, "recording_changed", {"recording": serialize_recording(recording)})
    return recording
