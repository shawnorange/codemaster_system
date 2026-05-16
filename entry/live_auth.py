from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any, Callable

from channels.db import database_sync_to_async

from .auth import AUTH_COOKIE_NAME, _resolve_authenticated_user_from_signed_payload


def _get_cookie_value(scope: dict[str, Any], name: str) -> str:
    headers = dict(scope.get("headers") or [])
    raw_cookie = headers.get(b"cookie", b"").decode("latin1")
    if not raw_cookie:
        return ""
    cookie = SimpleCookie()
    cookie.load(raw_cookie)
    morsel = cookie.get(name)
    return morsel.value if morsel else ""


@database_sync_to_async
def _resolve_user_from_cookie_value(cookie_value: str) -> dict[str, str] | None:
    if not cookie_value:
        return None
    return _resolve_authenticated_user_from_signed_payload(cookie_value)


class CodemasterAuthMiddleware:
    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> Any:
        cookie_value = _get_cookie_value(scope, AUTH_COOKIE_NAME)
        scope["codemaster_user"] = await _resolve_user_from_cookie_value(cookie_value)
        return await self.app(scope, receive, send)


def CodemasterAuthMiddlewareStack(app: Callable) -> CodemasterAuthMiddleware:
    return CodemasterAuthMiddleware(app)
