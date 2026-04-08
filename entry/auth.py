from __future__ import annotations

from functools import wraps
from typing import Any

from django.core import signing
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from .models import PortalUser


AUTH_COOKIE_NAME = "codemaster_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 8
AUTH_COOKIE_SALT = "codemaster.entry.auth"
DEFAULT_TEST_PASSWORD = "123456"

ROLE_CONFIG = {
    "student": {
        "label": "学生",
        "landing_url_name": "student-courses",
        "page_title": "学生课程选择页",
        "page_description": "请选择已开放的学习内容。课程入口按方向整理展示，大部分内容将随学习阶段逐步解锁。",
    },
    "parent": {
        "label": "家长",
        "landing_url_name": "parent-student-profile",
        "page_title": "家长学生档案",
        "page_description": "家长端当前聚焦孩子基础信息、GESP4 已开放专题，以及教师评价、奖励、课时变动等最小真实记录。",
    },
    "teacher": {
        "label": "教师",
        "landing_url_name": "teacher-students",
        "page_title": "教师工作台",
        "page_description": "教师端当前按“学生 / 课程”双入口组织。先从教师首页进入学生或课程，再继续完成专题开放和教学记录操作。",
    },
    "principal": {
        "label": "校长",
        "landing_url_name": "principal-dashboard",
        "page_title": "校长校区概览",
        "page_description": "校长端当前提供 GESP4 多专题开放与教师记录的最基础概览，用来验证最小真实闭环已经打通。",
    },
}

TEST_ACCOUNTS = {
    "student001": {"role": "student"},
    "parent001": {"role": "parent"},
    "teacher001": {"role": "teacher"},
    "principal001": {"role": "principal"},
}


def build_user_payload(portal_user: PortalUser) -> dict[str, str]:
    role_config = ROLE_CONFIG[portal_user.role]
    return {
        "id": str(portal_user.id),
        "username": portal_user.username,
        "full_name": portal_user.full_name,
        "role": portal_user.role,
        "role_label": role_config["label"],
        "landing_url": reverse(role_config["landing_url_name"]),
    }


def _query_portal_users() -> list[PortalUser]:
    try:
        return list(PortalUser.objects.filter(is_active=True).order_by("id"))
    except (OperationalError, ProgrammingError):
        return []


def list_test_accounts() -> list[dict[str, str]]:
    db_accounts = _query_portal_users()
    if db_accounts:
        return [
            {"username": account.username, "role_label": ROLE_CONFIG[account.role]["label"]}
            for account in db_accounts
        ]

    return [
        {"username": username, "role_label": ROLE_CONFIG[account["role"]]["label"]}
        for username, account in TEST_ACCOUNTS.items()
    ]


def authenticate_credentials(username: str, password: str) -> dict[str, str] | None:
    try:
        portal_user = (
            PortalUser.objects.filter(username=username, is_active=True)
            .order_by("id")
            .first()
        )
    except (OperationalError, ProgrammingError):
        portal_user = None

    if portal_user and portal_user.check_password(password):
        return build_user_payload(portal_user)

    if portal_user:
        return None

    if _query_portal_users():
        return None

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
    role_config = ROLE_CONFIG.get(role)
    if not role_config:
        return None

    try:
        portal_user = PortalUser.objects.filter(username=username, role=role, is_active=True).first()
    except (OperationalError, ProgrammingError):
        portal_user = None

    if portal_user:
        return build_user_payload(portal_user)

    if _query_portal_users():
        return None

    account = TEST_ACCOUNTS.get(username)
    if not account or account["role"] != role:
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
