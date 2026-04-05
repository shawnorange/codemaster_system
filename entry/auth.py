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
        "page_title": "学生课程中心",
        "page_description": "统一系统壳下的学生课程入口，当前以静态课程卡片占位，为下一步接入真实课程内容预留结构。",
    },
    "parent": {
        "label": "家长",
        "landing_url_name": "parent-student-profile",
        "page_title": "家长学生档案",
        "page_description": "统一系统壳下的家长查看入口，当前以学生信息卡和关注事项占位，为下一步接入档案与反馈做准备。",
    },
    "teacher": {
        "label": "教师",
        "landing_url_name": "teacher-students",
        "page_title": "教师学生管理",
        "page_description": "统一系统壳下的教师工作入口，当前以学生列表和教学提醒占位，为下一步接入课堂管理和反馈记录预留位置。",
    },
    "principal": {
        "label": "校长",
        "landing_url_name": "principal-dashboard",
        "page_title": "校长校区概览",
        "page_description": "统一系统壳下的校区总览入口，当前以概览卡片和重点事项占位，为下一步接入运营与教学数据预留结构。",
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
