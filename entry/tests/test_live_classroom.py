from __future__ import annotations

from datetime import timedelta
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from entry.auth import AUTH_COOKIE_NAME, build_auth_token, build_user_payload
from entry.live_classroom import (
    build_livekit_join_token,
    create_live_session,
    end_live_session,
    get_accessible_session_for_user,
    join_live_session_as_student,
    update_participant_screen_state,
)
from entry.live_recording import resolve_recording_file_path
from entry.live_views import _load_json_body
from entry.models import (
    ClassroomLiveParticipant,
    ClassroomLiveRecording,
    ClassroomLiveSession,
    Course,
    PortalUser,
    Student,
    TeacherStudentAssignment,
)


TEST_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}


@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
class LiveClassroomTests(TestCase):
    def setUp(self) -> None:
        self.teacher = self.create_user("teacher-live", PortalUser.ROLE_TEACHER, "老师甲")
        self.other_teacher = self.create_user("teacher-other-live", PortalUser.ROLE_TEACHER, "老师乙")
        self.student_user = self.create_user("student-live", PortalUser.ROLE_STUDENT, "学生甲")
        self.student = Student.objects.create(
            user=self.student_user,
            teacher_user=self.teacher,
            display_name="学生甲",
            grade="四年级",
        )
        self.course = Course.objects.create(slug="live-cpp", title="C++")
        TeacherStudentAssignment.objects.create(
            teacher=self.teacher,
            student=self.student,
            course=self.course,
            level_code="GESP2",
        )

    def create_user(self, username: str, role: str, full_name: str) -> PortalUser:
        user = PortalUser(username=username, role=role, full_name=full_name, password="")
        user.set_password("pass_123456")
        user.save()
        return user

    def authenticated_client(self, portal_user: PortalUser) -> Client:
        client = Client()
        client.cookies[AUTH_COOKIE_NAME] = build_auth_token(build_user_payload(portal_user))
        return client

    def test_teacher_start_is_idempotent_and_creates_teacher_participant(self) -> None:
        session = create_live_session(self.teacher)
        second_session = create_live_session(self.teacher)

        self.assertEqual(second_session.id, session.id)
        self.assertEqual(session.status, ClassroomLiveSession.STATUS_ACTIVE)
        teacher_participant = ClassroomLiveParticipant.objects.get(session=session, portal_user=self.teacher)
        self.assertEqual(teacher_participant.role, ClassroomLiveParticipant.ROLE_TEACHER)
        self.assertEqual(teacher_participant.connection_state, ClassroomLiveParticipant.CONNECTION_JOINED)

    def test_assigned_student_can_join_active_teacher_session(self) -> None:
        session = create_live_session(self.teacher)

        participant = join_live_session_as_student(self.student_user, session)

        self.assertEqual(participant.role, ClassroomLiveParticipant.ROLE_STUDENT)
        self.assertEqual(participant.connection_state, ClassroomLiveParticipant.CONNECTION_JOINED)
        self.assertEqual(participant.screen_state, ClassroomLiveParticipant.SCREEN_PENDING)

    def test_student_cannot_join_unassigned_teacher_session(self) -> None:
        session = create_live_session(self.other_teacher)

        with self.assertRaises(PermissionDenied):
            get_accessible_session_for_user(self.student_user, session.id)

    def test_student_screen_share_requires_monitor_surface(self) -> None:
        session = create_live_session(self.teacher)
        participant = join_live_session_as_student(self.student_user, session)

        with self.assertRaises(ValueError):
            participant.set_screen_state(
                screen_state=ClassroomLiveParticipant.SCREEN_SHARING,
                display_surface="window",
            )

        updated = update_participant_screen_state(
            self.student_user,
            session,
            screen_state=ClassroomLiveParticipant.SCREEN_SHARING,
            display_surface=ClassroomLiveParticipant.DISPLAY_MONITOR,
        )
        self.assertEqual(updated.screen_state, ClassroomLiveParticipant.SCREEN_SHARING)
        self.assertEqual(updated.display_surface, ClassroomLiveParticipant.DISPLAY_MONITOR)

    @override_settings(
        LIVEKIT_API_KEY="devkey",
        LIVEKIT_API_SECRET="dev-secret-for-live-classroom-tests",
        LIVEKIT_PUBLIC_URL="wss://live.example.test",
    )
    def test_livekit_join_token_uses_public_url_and_participant_identity(self) -> None:
        session = create_live_session(self.teacher)

        token = build_livekit_join_token(self.teacher, session)

        self.assertTrue(token.token)
        self.assertEqual(token.livekit_url, "wss://live.example.test")
        self.assertEqual(token.room_name, session.livekit_room_name)
        self.assertEqual(token.identity, f"teacher-{self.teacher.id}-session-{session.id}")

    def test_teacher_live_classroom_start_and_end_views(self) -> None:
        client = self.authenticated_client(self.teacher)

        start_response = client.post(reverse("teacher-live-classroom"), {"form_action": "start"})
        self.assertEqual(start_response.status_code, 302)
        session = ClassroomLiveSession.objects.get(teacher=self.teacher)

        end_response = client.post(
            reverse("teacher-live-classroom"),
            {"form_action": "end", "session_id": str(session.id)},
        )
        self.assertEqual(end_response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.status, ClassroomLiveSession.STATUS_ENDED)

    def test_student_live_classroom_join_view_requires_assignment(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.student_user)

        response = client.post(reverse("student-live-classroom"), {"session_id": str(session.id)})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ClassroomLiveParticipant.objects.filter(
                session=session,
                portal_user=self.student_user,
                connection_state=ClassroomLiveParticipant.CONNECTION_JOINED,
            ).exists()
        )

    def test_student_waiting_page_keeps_lobby_websocket_root(self) -> None:
        client = self.authenticated_client(self.student_user)

        response = client.get(reverse("student-live-classroom"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role="student-lobby"')
        self.assertContains(response, "entry/js/live_classroom.js?v=20260517-browser-recording-v5")

    def test_student_join_prompt_page_keeps_lobby_websocket_root(self) -> None:
        create_live_session(self.teacher)
        client = self.authenticated_client(self.student_user)

        response = client.get(reverse("student-live-classroom"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role="student-lobby"')
        self.assertContains(response, "entry/js/live_classroom.js?v=20260517-browser-recording-v5")

    def test_student_active_page_keeps_share_button_above_status(self) -> None:
        session = create_live_session(self.teacher)
        join_live_session_as_student(self.student_user, session)
        client = self.authenticated_client(self.student_user)

        response = client.get(f"{reverse('student-live-classroom')}?session_id={session.id}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "共享学生屏幕")
        self.assertContains(response, "老师共享屏幕后会显示在这里")
        self.assertNotContains(response, "compact-portal-header")
        body = response.content.decode("utf-8")
        self.assertLess(body.index("live-classroom-student-actions"), body.index("data-live-status"))

    def test_teacher_active_page_exposes_recording_controls(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)

        response = client.get(f"{reverse('teacher-live-classroom')}?session_id={session.id}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-recording-action="start_screen"')
        self.assertContains(response, 'data-recording-action="stop_screen"')
        self.assertContains(response, "录制状态")
        self.assertContains(response, "开始录屏")
        self.assertNotContains(response, "开始录音")
        self.assertNotContains(response, "停止录音")
        self.assertContains(response, "停止共享")
        self.assertContains(response, "entry/js/live_classroom.js?v=20260517-browser-recording-v5")
        self.assertContains(response, "entry/css/live_classroom.css?v=20260517-browser-recording-v5")
        self.assertNotContains(response, "compact-portal-header")

    def test_teacher_recording_api_accepts_browser_audio_upload(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)
        url = reverse("api-live-classroom-recording", args=[session.id])

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            start_response = client.post(
                url,
                data='{"action": "start_audio"}',
                content_type="application/json",
            )
            self.assertEqual(start_response.status_code, 200)
            recording_id = start_response.json()["recording"]["id"]
            recording = ClassroomLiveRecording.objects.get(id=recording_id)
            self.assertEqual(recording.provider, ClassroomLiveRecording.PROVIDER_BROWSER_AUDIO)
            self.assertEqual(recording.recording_type, ClassroomLiveRecording.TYPE_AUDIO)
            self.assertEqual(recording.status, ClassroomLiveRecording.STATUS_ACTIVE)

            upload_response = client.post(
                url,
                {
                    "action": "upload_audio",
                    "recording_id": str(recording_id),
                    "audio": SimpleUploadedFile("lesson.webm", b"webm-audio-data", content_type="audio/webm"),
                },
            )

            self.assertEqual(upload_response.status_code, 200)
            payload = upload_response.json()["recording"]
            self.assertEqual(payload["status"], ClassroomLiveRecording.STATUS_COMPLETED)
            self.assertEqual(payload["recording_type"], ClassroomLiveRecording.TYPE_AUDIO)
            self.assertTrue(payload["file_url"])
            self.assertTrue(payload["download_url"])
            self.assertTrue(payload["expires_at"])
            recording.refresh_from_db()
            self.assertRegex(Path(recording.file_path).name, r"^\d{8}_\d{2}:\d{2}:\d{2}老师甲\.webm$")
            self.assertGreater(recording.file_size, 0)
            self.assertEqual(recording.content_type, "audio/webm")

    def test_recording_upload_json_loader_ignores_multipart_body(self) -> None:
        request = RequestFactory().post(
            "/api/live-classroom/sessions/1/recording",
            {
                "action": "upload_audio",
                "audio": SimpleUploadedFile("lesson.webm", b"webm-audio-data", content_type="audio/webm"),
            },
        )
        self.assertEqual(request.POST.get("action"), "upload_audio")

        self.assertEqual(_load_json_body(request), {})

    @override_settings(
        LIVEKIT_API_KEY="devkey",
        LIVEKIT_API_SECRET="dev-secret-for-live-classroom-tests",
        LIVEKIT_RECORDING_ENABLED=True,
    )
    def test_teacher_can_run_audio_and_screen_recordings_independently(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)
        url = reverse("api-live-classroom-recording", args=[session.id])

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            audio_response = client.post(
                url,
                data='{"action": "start_audio"}',
                content_type="application/json",
            )
            screen_response = client.post(
                url,
                data='{"action": "start_screen"}',
                content_type="application/json",
            )

            self.assertEqual(audio_response.status_code, 200)
            self.assertEqual(screen_response.status_code, 200)
            self.assertEqual(audio_response.json()["recording"]["recording_type"], ClassroomLiveRecording.TYPE_AUDIO)
            self.assertEqual(screen_response.json()["recording"]["recording_type"], ClassroomLiveRecording.TYPE_SCREEN)
            self.assertEqual(
                ClassroomLiveRecording.objects.filter(
                    session=session,
                    status=ClassroomLiveRecording.STATUS_ACTIVE,
                ).count(),
                2,
            )

            screen_recording = ClassroomLiveRecording.objects.get(
                session=session,
                recording_type=ClassroomLiveRecording.TYPE_SCREEN,
            )
            self.assertEqual(screen_recording.provider, ClassroomLiveRecording.PROVIDER_BROWSER_SCREEN)

            upload_response = client.post(
                url,
                {
                    "action": "upload_screen",
                    "recording_id": str(screen_recording.id),
                    "screen": SimpleUploadedFile("lesson.webm", b"screen-video", content_type="video/webm"),
                },
            )

        self.assertEqual(upload_response.status_code, 200)
        payload = upload_response.json()["recording"]
        self.assertEqual(payload["status"], ClassroomLiveRecording.STATUS_COMPLETED)
        self.assertEqual(payload["recording_type"], ClassroomLiveRecording.TYPE_SCREEN)
        self.assertTrue(payload["download_url"])
        screen_recording.refresh_from_db()
        self.assertRegex(Path(screen_recording.file_path).name, r"^\d{8}_\d{2}:\d{2}:\d{2}老师甲\.webm$")
        self.assertEqual(screen_recording.file_size, len(b"screen-video"))
        self.assertEqual(screen_recording.content_type, "video/webm")
        self.assertIsNotNone(screen_recording.expires_at)

    def test_stop_screen_does_not_treat_browser_recording_as_livekit_file(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)
        url = reverse("api-live-classroom-recording", args=[session.id])

        self.assertEqual(
            client.post(url, data='{"action": "start_screen"}', content_type="application/json").status_code,
            200,
        )
        response = client.post(url, data='{"action": "stop_screen"}', content_type="application/json")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["recording"]
        self.assertEqual(payload["provider"], ClassroomLiveRecording.PROVIDER_BROWSER_SCREEN)
        self.assertEqual(payload["status"], ClassroomLiveRecording.STATUS_FAILED)
        self.assertIn("浏览器录屏尚未上传文件", payload["error_message"])
        self.assertNotIn("录屏文件未生成", payload["error_message"])

    @override_settings(
        LIVEKIT_API_KEY="devkey",
        LIVEKIT_API_SECRET="dev-secret-for-live-classroom-tests",
        LIVEKIT_RECORDING_ENABLED=True,
    )
    def test_livekit_screen_recording_can_be_stopped_when_file_exists(self) -> None:
        async def fake_start_egress(session, filepath):
            return "egress-test-1"

        async def fake_stop_egress(egress_id):
            return None

        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)
        url = reverse("api-live-classroom-recording", args=[session.id])

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            with patch("entry.live_recording._start_room_composite_egress", fake_start_egress), patch(
                "entry.live_recording._stop_egress",
                fake_stop_egress,
            ):
                start_response = client.post(
                    url,
                    data='{"action": "start_livekit"}',
                    content_type="application/json",
                )
                self.assertEqual(start_response.status_code, 200)

                screen_recording = ClassroomLiveRecording.objects.get(
                    session=session,
                    recording_type=ClassroomLiveRecording.TYPE_SCREEN,
                )
                screen_path = resolve_recording_file_path(screen_recording)
                self.assertIsNotNone(screen_path)
                Path(screen_path).parent.mkdir(parents=True, exist_ok=True)
                Path(screen_path).write_bytes(b"screen-video")

                stop_response = client.post(
                    url,
                    data='{"action": "stop_screen"}',
                    content_type="application/json",
                )

        self.assertEqual(stop_response.status_code, 200)
        payload = stop_response.json()["recording"]
        self.assertEqual(payload["status"], ClassroomLiveRecording.STATUS_COMPLETED)
        self.assertEqual(payload["recording_type"], ClassroomLiveRecording.TYPE_SCREEN)
        self.assertTrue(payload["download_url"])
        screen_recording.refresh_from_db()
        self.assertEqual(screen_recording.file_size, len(b"screen-video"))
        self.assertIsNotNone(screen_recording.expires_at)

    @override_settings(
        LIVEKIT_API_KEY="devkey",
        LIVEKIT_API_SECRET="dev-secret-for-live-classroom-tests",
        LIVEKIT_RECORDING_ENABLED=True,
    )
    def test_screen_recording_stop_fails_when_egress_file_is_missing(self) -> None:
        async def fake_start_egress(session, filepath):
            return "egress-missing-file"

        async def fake_stop_egress(egress_id):
            return None

        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)
        url = reverse("api-live-classroom-recording", args=[session.id])

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            with patch("entry.live_recording._start_room_composite_egress", fake_start_egress), patch(
                "entry.live_recording._stop_egress",
                fake_stop_egress,
            ):
                self.assertEqual(
                    client.post(url, data='{"action": "start_livekit"}', content_type="application/json").status_code,
                    200,
                )
                response = client.post(url, data='{"action": "stop_screen"}', content_type="application/json")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["recording"]
        self.assertEqual(payload["status"], ClassroomLiveRecording.STATUS_FAILED)
        self.assertEqual(payload["download_url"], "")
        self.assertIn("录屏文件未生成", payload["error_message"])

    def test_end_session_fails_active_browser_audio_without_upload(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)
        ClassroomLiveRecording.objects.create(
            session=session,
            recording_type=ClassroomLiveRecording.TYPE_AUDIO,
            provider=ClassroomLiveRecording.PROVIDER_BROWSER_AUDIO,
            status=ClassroomLiveRecording.STATUS_ACTIVE,
        )

        response = client.post(
            reverse("teacher-live-classroom"),
            {"form_action": "end", "session_id": str(session.id)},
        )

        self.assertEqual(response.status_code, 302)
        recording = ClassroomLiveRecording.objects.get(
            session=session,
            recording_type=ClassroomLiveRecording.TYPE_AUDIO,
        )
        self.assertEqual(recording.status, ClassroomLiveRecording.STATUS_FAILED)
        self.assertIn("未停止上传", recording.error_message)

    def test_end_session_fails_active_browser_screen_without_egress_error(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)
        ClassroomLiveRecording.objects.create(
            session=session,
            recording_type=ClassroomLiveRecording.TYPE_SCREEN,
            provider=ClassroomLiveRecording.PROVIDER_BROWSER_SCREEN,
            status=ClassroomLiveRecording.STATUS_ACTIVE,
        )

        response = client.post(
            reverse("teacher-live-classroom"),
            {"form_action": "end", "session_id": str(session.id)},
        )

        self.assertEqual(response.status_code, 302)
        recording = ClassroomLiveRecording.objects.get(
            session=session,
            recording_type=ClassroomLiveRecording.TYPE_SCREEN,
        )
        self.assertEqual(recording.status, ClassroomLiveRecording.STATUS_FAILED)
        self.assertIn("浏览器录屏未停止上传", recording.error_message)
        self.assertNotIn("录屏文件未生成", recording.error_message)

    def test_teacher_can_download_completed_recording_before_expiry(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)
        content = b"audio-data"

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            output_path = Path(media_root) / "live-classroom" / "audio" / "lesson.webm"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(content)
            recording = ClassroomLiveRecording.objects.create(
                session=session,
                recording_type=ClassroomLiveRecording.TYPE_AUDIO,
                provider=ClassroomLiveRecording.PROVIDER_BROWSER_AUDIO,
                status=ClassroomLiveRecording.STATUS_COMPLETED,
                file_path=str(output_path),
                file_size=len(content),
                content_type="audio/webm",
                ended_at=timezone.now(),
                expires_at=timezone.now() + timedelta(days=15),
            )

            response = client.get(reverse("api-live-classroom-recording-download", args=[recording.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), content)

    def test_recording_download_expires_after_15_days(self) -> None:
        session = create_live_session(self.teacher)
        client = self.authenticated_client(self.teacher)

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            output_path = Path(media_root) / "live-classroom" / "audio" / "expired.webm"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"expired")
            recording = ClassroomLiveRecording.objects.create(
                session=session,
                recording_type=ClassroomLiveRecording.TYPE_AUDIO,
                provider=ClassroomLiveRecording.PROVIDER_BROWSER_AUDIO,
                status=ClassroomLiveRecording.STATUS_COMPLETED,
                file_path=str(output_path),
                file_size=7,
                content_type="audio/webm",
                ended_at=timezone.now() - timedelta(days=16),
                expires_at=timezone.now() - timedelta(days=1),
            )

            response = client.get(reverse("api-live-classroom-recording-download", args=[recording.id]))

        self.assertEqual(response.status_code, 404)

    def test_purge_expired_live_recordings_deletes_file_and_disables_link(self) -> None:
        session = create_live_session(self.teacher)

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            output_path = Path(media_root) / "live-classroom" / "audio" / "old.webm"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"old")
            recording = ClassroomLiveRecording.objects.create(
                session=session,
                recording_type=ClassroomLiveRecording.TYPE_AUDIO,
                provider=ClassroomLiveRecording.PROVIDER_BROWSER_AUDIO,
                status=ClassroomLiveRecording.STATUS_COMPLETED,
                file_url="/media/live-classroom/audio/old.webm",
                file_path=str(output_path),
                file_size=3,
                content_type="audio/webm",
                ended_at=timezone.now() - timedelta(days=16),
                expires_at=timezone.now() - timedelta(seconds=1),
            )

            output = StringIO()
            call_command("purge_expired_live_recordings", stdout=output)

            recording.refresh_from_db()
            self.assertFalse(output_path.exists())
            self.assertIsNotNone(recording.deleted_at)
            self.assertEqual(recording.file_url, "")
            self.assertIn("Purged 1 expired live classroom recordings", output.getvalue())

    def test_end_session_marks_joined_participants_left(self) -> None:
        session = create_live_session(self.teacher)
        join_live_session_as_student(self.student_user, session)

        end_live_session(self.teacher, session)

        self.assertFalse(
            ClassroomLiveParticipant.objects.filter(
                session=session,
                connection_state=ClassroomLiveParticipant.CONNECTION_JOINED,
            ).exists()
        )
