from __future__ import annotations

from functools import wraps
from typing import Any

from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse


AUTH_COOKIE_NAME = "codemaster_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 8
AUTH_COOKIE_SALT = "codemaster.entry.auth"
DEFAULT_TEST_PASSWORD = "123456"

ROLE_CONFIG = {
    "student": {
        "label": "学生",
        "landing_url_name": "student-courses",
        "page_title": "学生课程页",
        "page_description": "这是学生端最小占位页，当前只用于验证登录后角色跳转链路，后续再接入课程、作业与学习进度。",
    },
    "parent": {
        "label": "家长",
        "landing_url_name": "parent-student-profile",
        "page_title": "家长学生档案页",
        "page_description": "这是家长端最小占位页，当前只用于验证登录后角色跳转链路，后续再接入学习档案、反馈与沟通模块。",
    },
    "teacher": {
        "label": "教师",
        "landing_url_name": "teacher-students",
        "page_title": "教师学生管理页",
        "page_description": "这是教师端最小占位页，当前只用于验证登录后角色跳转链路，后续再接入学生管理、授课记录与教学协同。",
    },
    "principal": {
        "label": "校长",
        "landing_url_name": "principal-dashboard",
        "page_title": "校长总览页",
        "page_description": "这是校长端最小占位页，当前只用于验证登录后角色跳转链路，后续再接入校区运营总览、排课与数据看板。",
    },
}

TEST_ACCOUNTS = {
    "student001": {"role": "student"},
    "parent001": {"role": "parent"},
    "teacher001": {"role": "teacher"},
    "principal001": {"role": "principal"},
}


def list_test_accounts() -> list[dict[str, str]]:
    return [
        {"username": username, "role_label": ROLE_CONFIG[account["role"]]["label"]}
        for username, account in TEST_ACCOUNTS.items()
    ]


def authenticate_credentials(username: str, password: str) -> dict[str, str] | None:
    account = TEST_ACCOUNTS.get(username)
    if not account or password != DEFAULT_TEST_PASSWORD:
        return None

    role = account["role"]
    role_config = ROLE_CONFIG[role]
    return {
        "username": username,
        "role": role,
        "role_label": role_config["label"],
        "landing_url": reverse(role_config["landing_url_name"]),
    }


def get_authenticated_user(request: HttpRequest) -> dict[str, str] | None:
    signed_payload = request.COOKIES.get(AUTH_COOKIE_NAME)
    if not signed_payload:
        return None

    try:
        payload = signing.loads(
            signed_payload,
            salt=AUTH_COOKIE_SALT,
            max_age=AUTH_COOKIE_MAX_AGE,
        )
    except signing.BadSignature:
        return None

    username = payload.get("username")
    role = payload.get("role")
    account = TEST_ACCOUNTS.get(username)
    role_config = ROLE_CONFIG.get(role)
    if not account or account["role"] != role or not role_config:
        return None

    return {
        "username": username,
        "role": role,
        "role_label": role_config["label"],
        "landing_url": reverse(role_config["landing_url_name"]),
    }


def set_auth_cookie(response: HttpResponse, user: dict[str, str]) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        signing.dumps(
            {"username": user["username"], "role": user["role"]},
            salt=AUTH_COOKIE_SALT,
        ),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )


def clear_auth_cookie(response: HttpResponse) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME)


def role_required(expected_role: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any):
            user = get_authenticated_user(request)
            if not user:
                return redirect("login")

            if user["role"] != expected_role:
                return redirect(user["landing_url"])

            request.codemaster_user = user
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
