"""Project URL configuration."""

from django.conf import settings
from django.views.static import serve
from django.urls import include, path, re_path


def debug_media_serve(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("", include("entry.urls")),
]

if settings.DEBUG:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", debug_media_serve),
    ]
