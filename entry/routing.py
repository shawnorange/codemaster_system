from django.urls import path

from .live_consumers import ClassroomLobbyConsumer, ClassroomSessionConsumer


websocket_urlpatterns = [
    path("ws/classroom/lobby/", ClassroomLobbyConsumer.as_asgi()),
    path("ws/classroom/sessions/<int:session_id>/", ClassroomSessionConsumer.as_asgi()),
]
