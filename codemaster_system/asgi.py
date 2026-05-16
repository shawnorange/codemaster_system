"""
ASGI config for codemaster_system project.
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "codemaster_system.settings")

django_asgi_app = get_asgi_application()

from entry.live_auth import CodemasterAuthMiddlewareStack  # noqa: E402
from entry.routing import websocket_urlpatterns  # noqa: E402


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": CodemasterAuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
