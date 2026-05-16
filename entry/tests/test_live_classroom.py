from __future__ import annotations

import tempfile

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from entry.auth import AUTH_COOKIE_NAME, build_auth_token, build_user_payload
from entry.live_classroom import (
    build_livekit_join_token,
    create_live_session,
    end_live_session,
    get_accessible_session_for_user,
    join_live_session_as_student,
    update_participant_screen_state,
)
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
        self.assertContains(response, "entry/js/live_classroom.js")

    def test_student_join_prompt_page_keeps_lobby_websocket_root(self) -> None:
        create_live_session(self.teacher)
        client = self.authenticated_client(self.student_user)

        response = client.get(reverse("student-live-classroom"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role="student-lobby"')
        self.assertContains(response, "entry/js/live_classroom.js")

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
        self.assertContains(response, 'data-recording-action="start"')
        self.assertContains(response, 'data-recording-action="stop"')
        self.assertContains(response, "录音状态")
        self.assertContains(response, "停止共享")
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
            self.assertTrue(payload["file_url"].endswith(".webm"))
            recording.refresh_from_db()
            self.assertTrue(recording.file_path)

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
