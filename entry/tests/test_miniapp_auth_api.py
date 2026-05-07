from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from django.core import signing
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from entry.auth import AUTH_COOKIE_NAME, AUTH_COOKIE_MAX_AGE, AUTH_COOKIE_SALT
from entry.models import PortalUser, Student


@override_settings(
    WECHAT_MINIAPP_APPID="wx-test-appid",
    WECHAT_MINIAPP_SECRET="wx-test-secret",
)
class MiniappAuthApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.anchor_date = date(2026, 5, 4)
        self.parent_password = "parent_123456"
        self.principal_password = "principal_123456"
        self.teacher_password = "teacher_123456"

        self.parent = self.create_portal_user(
            username="miniapp_parent",
            role=PortalUser.ROLE_PARENT,
            full_name="家长甲",
            password=self.parent_password,
        )
        self.principal = self.create_portal_user(
            username="miniapp_principal",
            role=PortalUser.ROLE_PRINCIPAL,
            full_name="校长甲",
            password=self.principal_password,
        )
        self.teacher = self.create_portal_user(
            username="miniapp_teacher",
            role=PortalUser.ROLE_TEACHER,
            full_name="教师甲",
            password=self.teacher_password,
        )
        self.student_user = self.create_portal_user(
            username="miniapp_student",
            role=PortalUser.ROLE_STUDENT,
            full_name="学生甲",
            password="student_123456",
        )
        self.child = Student.objects.create(
            user=self.student_user,
            parent_user=self.parent,
            teacher_user=self.teacher,
            display_name="张小明",
            grade="四年级",
            campus="张江校区",
            primary_course_name="CPP",
            primary_track_name="基础体系",
            primary_level_name="C1",
        )

    def create_portal_user(self, *, username: str, role: str, full_name: str, password: str) -> PortalUser:
        portal_user = PortalUser(
            username=username,
            role=role,
            full_name=full_name,
            password="",
        )
        portal_user.set_password(password)
        portal_user.save()
        return portal_user

    def miniapp_login_url(self) -> str:
        return reverse("api-miniapp-login")

    def parent_url(self) -> str:
        return reverse("api-parent-get-my-child")

    def principal_url(self) -> str:
        return reverse("api-principal-get-students-info")

    def phone_login_url(self) -> str:
        return reverse("api-miniapp-login-by-phone")

    def login(self, *, username: str, password: str):
        return self.client.post(
            self.miniapp_login_url(),
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )

    def login_by_phone(self, *, code: str, client: Client | None = None):
        request_client = client or self.client
        return request_client.post(
            self.phone_login_url(),
            data=json.dumps({"code": code}),
            content_type="application/json",
        )

    def build_auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Codemaster {token}"}

    def mock_wechat_access_token_response(self, access_token: str = "wechat-access-token"):
        class _Response:
            def json(self_inner):
                return {"access_token": access_token, "expires_in": 7200}

        return _Response()

    def mock_wechat_phone_response(self, *, errcode: int = 0, phone_number: str = "13800138000"):
        class _Response:
            def json(self_inner):
                if errcode != 0:
                    return {"errcode": errcode, "errmsg": "invalid code"}
                return {
                    "errcode": 0,
                    "errmsg": "ok",
                    "phone_info": {
                        "phoneNumber": phone_number,
                        "purePhoneNumber": phone_number,
                        "countryCode": "86",
                    },
                }

        return _Response()

    def test_miniapp_login_returns_signed_token_and_user_for_parent(self) -> None:
        response = self.login(username=self.parent.username, password=self.parent_password)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["user"]["username"], self.parent.username)
        self.assertEqual(payload["user"]["role"], PortalUser.ROLE_PARENT)
        self.assertEqual(payload["user"]["full_name"], self.parent.full_name)
        signed_payload = signing.loads(
            payload["token"],
            salt=AUTH_COOKIE_SALT,
            max_age=AUTH_COOKIE_MAX_AGE,
        )
        self.assertEqual(
            signed_payload,
            {"username": self.parent.username, "role": PortalUser.ROLE_PARENT},
        )
        self.assertIn(AUTH_COOKIE_NAME, response.cookies)

    def test_miniapp_login_allows_json_post_without_csrf_cookie(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            self.miniapp_login_url(),
            data=json.dumps({"username": self.parent.username, "password": self.parent_password}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], self.parent.username)

    def test_miniapp_login_returns_signed_token_and_user_for_principal(self) -> None:
        response = self.login(username=self.principal.username, password=self.principal_password)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["user"]["username"], self.principal.username)
        self.assertEqual(payload["user"]["role"], PortalUser.ROLE_PRINCIPAL)
        signed_payload = signing.loads(
            payload["token"],
            salt=AUTH_COOKIE_SALT,
            max_age=AUTH_COOKIE_MAX_AGE,
        )
        self.assertEqual(
            signed_payload,
            {"username": self.principal.username, "role": PortalUser.ROLE_PRINCIPAL},
        )

    def test_miniapp_login_rejects_teacher_even_with_valid_password(self) -> None:
        response = self.login(username=self.teacher.username, password=self.teacher_password)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "仅支持家长或校长账号登录。"})

    def test_miniapp_login_uses_real_password_hash_check(self) -> None:
        self.assertNotEqual(self.parent.password, self.parent_password)
        self.assertTrue(self.parent.check_password(self.parent_password))

        response = self.login(username=self.parent.username, password="wrong_password")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "账号或密码错误。"})

    def test_parent_api_accepts_authorization_header_token(self) -> None:
        login_response = self.login(username=self.parent.username, password=self.parent_password)
        token = login_response.json()["token"]

        response = self.client.get(
            self.parent_url(),
            {"anchor_date": self.anchor_date.isoformat()},
            headers=self.build_auth_headers(token),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["anchor_date"], self.anchor_date.isoformat())
        self.assertEqual(len(payload["children"]), 1)
        self.assertEqual(payload["children"][0]["student_id"], self.child.id)
        self.assertEqual(payload["children"][0]["display_name"], self.child.display_name)

    def test_principal_api_accepts_authorization_header_token(self) -> None:
        login_response = self.login(username=self.principal.username, password=self.principal_password)
        token = login_response.json()["token"]

        response = self.client.get(
            self.principal_url(),
            {"anchor_date": self.anchor_date.isoformat()},
            headers=self.build_auth_headers(token),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["anchor_date"], self.anchor_date.isoformat())
        student_row = next(item for item in payload["students"] if item["student_id"] == self.child.id)
        self.assertEqual(student_row["teacher_id"], self.teacher.id)

    def test_miniapp_phone_login_requires_code(self) -> None:
        response = self.client.post(
            self.phone_login_url(),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "missing_code")

    @patch("entry.views.requests.post")
    @patch("entry.views.requests.get")
    def test_miniapp_phone_login_rejects_invalid_wechat_code(self, mock_get, mock_post) -> None:
        mock_get.return_value = self.mock_wechat_access_token_response()
        mock_post.return_value = self.mock_wechat_phone_response(errcode=40029)

        response = self.login_by_phone(code="invalid-code")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "invalid_phone_code")

    @patch("entry.views.requests.post")
    @patch("entry.views.requests.get")
    def test_miniapp_phone_login_returns_token_for_active_parent(self, mock_get, mock_post) -> None:
        self.parent.phone = "13800138000"
        self.parent.save(update_fields=["phone", "updated_at"])
        mock_get.return_value = self.mock_wechat_access_token_response()
        mock_post.return_value = self.mock_wechat_phone_response(phone_number="13800138000")

        response = self.login_by_phone(code="valid-code")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["user"]["role"], PortalUser.ROLE_PARENT)
        self.assertEqual(payload["user"]["username"], self.parent.username)
        signed_payload = signing.loads(
            payload["token"],
            salt=AUTH_COOKIE_SALT,
            max_age=AUTH_COOKIE_MAX_AGE,
        )
        self.assertEqual(
            signed_payload,
            {"username": self.parent.username, "role": PortalUser.ROLE_PARENT},
        )

    @patch("entry.views.requests.post")
    @patch("entry.views.requests.get")
    def test_miniapp_phone_login_rejects_unbound_phone(self, mock_get, mock_post) -> None:
        mock_get.return_value = self.mock_wechat_access_token_response()
        mock_post.return_value = self.mock_wechat_phone_response(phone_number="13900000000")

        response = self.login_by_phone(code="valid-code")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "phone_not_bound")

    @patch("entry.views.requests.post")
    @patch("entry.views.requests.get")
    def test_miniapp_phone_login_rejects_inactive_parent(self, mock_get, mock_post) -> None:
        self.parent.phone = "13800138000"
        self.parent.is_active = False
        self.parent.save(update_fields=["phone", "is_active", "updated_at"])
        mock_get.return_value = self.mock_wechat_access_token_response()
        mock_post.return_value = self.mock_wechat_phone_response(phone_number="13800138000")

        response = self.login_by_phone(code="valid-code")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "phone_not_bound")

    @patch("entry.views.requests.post")
    @patch("entry.views.requests.get")
    def test_miniapp_phone_login_rejects_non_parent_roles(self, mock_get, mock_post) -> None:
        self.teacher.phone = "13800138000"
        self.teacher.save(update_fields=["phone", "updated_at"])
        self.student_user.phone = "13800138000"
        self.student_user.save(update_fields=["phone", "updated_at"])
        self.principal.phone = "13800138000"
        self.principal.save(update_fields=["phone", "updated_at"])
        mock_get.return_value = self.mock_wechat_access_token_response()
        mock_post.return_value = self.mock_wechat_phone_response(phone_number="13800138000")

        response = self.login_by_phone(code="valid-code")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "phone_not_bound")

    @patch("entry.views.requests.post")
    @patch("entry.views.requests.get")
    def test_miniapp_phone_login_rejects_phone_bound_to_multiple_parents(self, mock_get, mock_post) -> None:
        self.parent.phone = "13800138000"
        self.parent.save(update_fields=["phone", "updated_at"])
        another_parent = self.create_portal_user(
            username="miniapp_parent_b",
            role=PortalUser.ROLE_PARENT,
            full_name="家长乙",
            password="parent_b_123456",
        )
        another_parent.phone = "13800138000"
        another_parent.save(update_fields=["phone", "updated_at"])
        mock_get.return_value = self.mock_wechat_access_token_response()
        mock_post.return_value = self.mock_wechat_phone_response(phone_number="13800138000")

        response = self.login_by_phone(code="valid-code")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error_code"], "phone_conflict")

    @patch("entry.views.requests.post")
    @patch("entry.views.requests.get")
    def test_miniapp_phone_login_token_can_access_parent_api(self, mock_get, mock_post) -> None:
        self.parent.phone = "13800138000"
        self.parent.save(update_fields=["phone", "updated_at"])
        mock_get.return_value = self.mock_wechat_access_token_response()
        mock_post.return_value = self.mock_wechat_phone_response(phone_number="13800138000")

        login_response = self.login_by_phone(code="valid-code")
        token = login_response.json()["token"]

        response = self.client.get(
            self.parent_url(),
            {"anchor_date": self.anchor_date.isoformat()},
            headers=self.build_auth_headers(token),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["children"][0]["student_id"], self.child.id)
