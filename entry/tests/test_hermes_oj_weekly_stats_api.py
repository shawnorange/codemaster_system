from __future__ import annotations

import json
from datetime import date

from django.test import TestCase, override_settings
from django.urls import reverse

from entry.models import HomeworkSubmission, PortalUser, Student, StudentOjWeeklyStat


@override_settings(HERMES_INGEST_TOKEN="hermes-test-token")
class HermesOjWeeklyStatsApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.student = self.create_student("hermes_student_zhangsan", "张三")

    def create_student(self, username: str, display_name: str) -> Student:
        portal_user = PortalUser.objects.create(
            username=username,
            role=PortalUser.ROLE_STUDENT,
            full_name=display_name,
        )
        return Student.objects.create(
            user=portal_user,
            display_name=display_name,
            grade="四年级",
            campus="张江校区",
            primary_course_name="CPP",
            primary_track_name="基础体系",
            primary_level_name="C1",
        )

    def import_url(self) -> str:
        return reverse("api-hermes-oj-weekly-stats-import")

    def query_url(self) -> str:
        return reverse("api-hermes-oj-weekly-stats")

    def auth_headers(self, token: str = "hermes-test-token") -> dict[str, str]:
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def valid_payload(self, **overrides):
        payload = {
            "source": "dashima-oj",
            "week_start": "2026-05-11",
            "week_end": "2026-05-17",
            "items": [
                {
                    "oj_username": "zhangsan",
                    "display_name": self.student.display_name,
                    "submission_count": 10,
                    "accepted_count": 6,
                    "raw_record_count": 100,
                }
            ],
        }
        payload.update(overrides)
        return payload

    def post_import(self, payload, *, token: str = "hermes-test-token"):
        return self.client.post(
            self.import_url(),
            data=json.dumps(payload),
            content_type="application/json",
            **self.auth_headers(token),
        )

    def test_import_without_authorization_returns_401(self) -> None:
        response = self.client.post(
            self.import_url(),
            data=json.dumps(self.valid_payload()),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error_code"], "missing_authorization")

    def test_import_with_wrong_token_returns_401(self) -> None:
        response = self.post_import(self.valid_payload(), token="wrong-token")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error_code"], "invalid_token")

    @override_settings(HERMES_INGEST_TOKEN="")
    def test_import_without_configured_token_returns_clear_error(self) -> None:
        response = self.post_import(self.valid_payload())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error_code"], "hermes_ingest_not_configured")

    def test_import_with_valid_token_creates_student_oj_weekly_stat(self) -> None:
        response = self.post_import(self.valid_payload())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["created_count"], 1)
        self.assertEqual(payload["updated_count"], 0)
        self.assertEqual(payload["matched_students"], 1)
        self.assertEqual(payload["total_submission_count"], 10)
        self.assertEqual(payload["total_accepted_count"], 6)

        stat = StudentOjWeeklyStat.objects.get(
            student=self.student,
            source="dashima-oj",
            week_start=date(2026, 5, 11),
        )
        self.assertEqual(stat.week_end, date(2026, 5, 17))
        self.assertEqual(stat.oj_username, "zhangsan")
        self.assertEqual(stat.submission_count, 10)
        self.assertEqual(stat.accepted_count, 6)
        self.assertEqual(stat.raw_record_count, 100)
        self.assertIsNotNone(stat.synced_at)

    def test_import_updates_existing_student_source_week_instead_of_creating_duplicate(self) -> None:
        first_response = self.post_import(self.valid_payload())
        self.assertEqual(first_response.status_code, 200)

        second_payload = self.valid_payload(
            items=[
                {
                    "oj_username": "zhangsan",
                    "display_name": self.student.display_name,
                    "submission_count": 12,
                    "accepted_count": 7,
                    "raw_record_count": 120,
                }
            ]
        )
        second_response = self.post_import(second_payload)

        self.assertEqual(second_response.status_code, 200)
        payload = second_response.json()
        self.assertEqual(payload["created_count"], 0)
        self.assertEqual(payload["updated_count"], 1)
        self.assertEqual(
            StudentOjWeeklyStat.objects.filter(
                student=self.student,
                source="dashima-oj",
                week_start=date(2026, 5, 11),
            ).count(),
            1,
        )
        stat = StudentOjWeeklyStat.objects.get(student=self.student, source="dashima-oj")
        self.assertEqual(stat.submission_count, 12)
        self.assertEqual(stat.accepted_count, 7)
        self.assertEqual(stat.raw_record_count, 120)

    def test_import_rejects_accepted_count_greater_than_submission_count(self) -> None:
        payload = self.valid_payload(
            items=[
                {
                    "oj_username": "zhangsan",
                    "display_name": self.student.display_name,
                    "submission_count": 6,
                    "accepted_count": 7,
                }
            ]
        )

        response = self.post_import(payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "invalid_payload")
        self.assertEqual(StudentOjWeeklyStat.objects.count(), 0)

    def test_import_rejects_invalid_week_date_format(self) -> None:
        response = self.post_import(self.valid_payload(week_start="2026/05/11"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "invalid_payload")
        self.assertIn("week_start", response.json()["message"])

    def test_import_unmatched_display_name_is_reported_without_creating_stat(self) -> None:
        payload = self.valid_payload(
            items=[
                {
                    "oj_username": "missing",
                    "display_name": "不存在的学生",
                    "submission_count": 10,
                    "accepted_count": 6,
                    "raw_record_count": 100,
                }
            ]
        )

        response = self.post_import(payload)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["created_count"], 0)
        self.assertEqual(payload["matched_students"], 0)
        self.assertEqual(payload["unmatched_items"][0]["display_name"], "不存在的学生")
        self.assertEqual(StudentOjWeeklyStat.objects.count(), 0)

    def test_import_ambiguous_display_name_is_reported_without_creating_stat(self) -> None:
        self.create_student("hermes_student_dup_a", "重名学生")
        self.create_student("hermes_student_dup_b", "重名学生")
        payload = self.valid_payload(
            items=[
                {
                    "oj_username": "duplicate",
                    "display_name": "重名学生",
                    "submission_count": 10,
                    "accepted_count": 6,
                    "raw_record_count": 100,
                }
            ]
        )

        response = self.post_import(payload)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["created_count"], 0)
        self.assertEqual(payload["matched_students"], 0)
        self.assertEqual(payload["ambiguous_items"][0]["display_name"], "重名学生")
        self.assertEqual(len(payload["ambiguous_items"][0]["matched_student_ids"]), 2)
        self.assertEqual(StudentOjWeeklyStat.objects.count(), 0)

    def test_import_steven_oj_username_overrides_display_name_to_houxi(self) -> None:
        houxi = self.create_student("hermes_student_houxi", "侯曦")
        payload = self.valid_payload(
            items=[
                {
                    "oj_username": "steven",
                    "display_name": "传错的名字",
                    "submission_count": 10,
                    "accepted_count": 6,
                    "raw_record_count": 100,
                }
            ]
        )

        response = self.post_import(payload)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["created_count"], 1)
        self.assertEqual(payload["unmatched_items"], [])
        stat = StudentOjWeeklyStat.objects.get(oj_username="steven")
        self.assertEqual(stat.student, houxi)
        self.assertEqual(stat.student.display_name, "侯曦")

    def test_import_does_not_add_oj_fields_to_homework_submission(self) -> None:
        homework_submission_fields = {field.name for field in HomeworkSubmission._meta.get_fields()}

        self.assertFalse(
            {
                "source",
                "oj_username",
                "week_start",
                "week_end",
                "submission_count",
                "accepted_count",
                "raw_record_count",
                "synced_at",
            }
            & homework_submission_fields
        )

    def test_query_weekly_stats_returns_filtered_stats(self) -> None:
        self.post_import(self.valid_payload())
        other_student = self.create_student("hermes_student_lisi", "李四")
        StudentOjWeeklyStat.objects.create(
            student=other_student,
            source="other-oj",
            oj_username="lisi",
            week_start=date(2026, 5, 11),
            week_end=date(2026, 5, 17),
            submission_count=20,
            accepted_count=15,
        )

        response = self.client.get(
            self.query_url(),
            data={"week_start": "2026-05-11", "source": "dashima-oj"},
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        result = payload["results"][0]
        self.assertEqual(result["student_id"], self.student.id)
        self.assertEqual(result["display_name"], self.student.display_name)
        self.assertEqual(result["oj_username"], "zhangsan")
        self.assertEqual(result["source"], "dashima-oj")
        self.assertEqual(result["week_start"], "2026-05-11")
        self.assertEqual(result["week_end"], "2026-05-17")
        self.assertEqual(result["submission_count"], 10)
        self.assertEqual(result["accepted_count"], 6)
