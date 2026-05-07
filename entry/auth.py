from __future__ import annotations

from functools import wraps
from typing import Any

from django.conf import settings
from django.core import signing
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from .models import PortalUser


AUTH_COOKIE_NAME = "codemaster_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 8
AUTH_COOKIE_SALT = "codemaster.entry.auth"
AUTHORIZATION_SCHEME = "Codemaster"
DEFAULT_TEST_PASSWORD = str(getattr(settings, "DEFAULT_TEST_PASSWORD", "") or "")

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
        "page_description": "",
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


def build_auth_token(user: dict[str, str]) -> str:
    return signing.dumps(
        {"username": user["username"], "role": user["role"]},
        salt=AUTH_COOKIE_SALT,
    )


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


def get_active_portal_user(username: str) -> PortalUser | None:
    try:
        return (
            PortalUser.objects.filter(username=username, is_active=True)
            .order_by("id")
            .first()
        )
    except (OperationalError, ProgrammingError):
        return None


def authenticate_portal_user(username: str, password: str) -> PortalUser | None:
    portal_user = get_active_portal_user(username)
    if portal_user is None:
        return None
    if not portal_user.check_password(password):
        return None
    return portal_user


def authenticate_credentials(username: str, password: str) -> dict[str, str] | None:
    portal_user = authenticate_portal_user(username, password)
    if portal_user:
        return build_user_payload(portal_user)

    portal_user = get_active_portal_user(username)
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


def _load_signed_auth_payload(signed_payload: str) -> dict[str, Any] | None:
    try:
        return signing.loads(
            signed_payload,
            salt=AUTH_COOKIE_SALT,
            max_age=AUTH_COOKIE_MAX_AGE,
        )
    except signing.BadSignature:
        return None


def _get_authorization_token(request: HttpRequest) -> str:
    raw_value = str(
        request.headers.get("Authorization")
        or request.META.get("HTTP_AUTHORIZATION")
        or ""
    ).strip()
    if not raw_value:
        return ""

    scheme, _, token = raw_value.partition(" ")
    if scheme.lower() != AUTHORIZATION_SCHEME.lower():
        return ""
    return token.strip()


def _resolve_authenticated_user_from_signed_payload(signed_payload: str) -> dict[str, str] | None:
    payload = _load_signed_auth_payload(signed_payload)
    if not payload:
        return None

    username = payload.get("username")
    role = payload.get("role")
    role_config = ROLE_CONFIG.get(role)
    if not role_config:
        return None

    portal_user = get_active_portal_user(str(username or ""))
    if portal_user and portal_user.role == role:
        return build_user_payload(portal_user)

    if portal_user:
        return None

    if _query_portal_users():
        return None

    account = TEST_ACCOUNTS.get(str(username or ""))
    if not account or account["role"] != role:
        return None

    return {
        "username": str(username or ""),
        "role": role,
        "role_label": role_config["label"],
        "landing_url": reverse(role_config["landing_url_name"]),
    }


def get_authenticated_user(request: HttpRequest) -> dict[str, str] | None:
    authorization_token = _get_authorization_token(request)
    if authorization_token:
        user = _resolve_authenticated_user_from_signed_payload(authorization_token)
        if user:
            return user

    signed_payload = str(request.COOKIES.get(AUTH_COOKIE_NAME) or "").strip()
    if not signed_payload:
        return None
    return _resolve_authenticated_user_from_signed_payload(signed_payload)


def set_auth_cookie(response: HttpResponse, user: dict[str, str]) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        build_auth_token(user),
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


def api_role_required(expected_role: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any):
            user = get_authenticated_user(request)
            if not user:
                return JsonResponse({"error": "未登录"}, status=401)

            if user["role"] != expected_role:
                return JsonResponse({"error": "无权访问"}, status=403)

            request.codemaster_user = user
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
