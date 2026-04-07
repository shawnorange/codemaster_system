"""
Django settings for codemaster_system project.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def get_env(name: str, *, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value or ""


load_env_file(BASE_DIR / ".env")

SECRET_KEY = "django-insecure-codemaster-mvp-login-shell"

DEBUG = True

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]


INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "entry.apps.EntryConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "codemaster_system.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.csrf",
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "codemaster_system.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": get_env("POSTGRES_DB", required=True),
        "USER": get_env("POSTGRES_USER", required=True),
        "PASSWORD": get_env("POSTGRES_PASSWORD", required=True),
        "HOST": get_env("POSTGRES_HOST", default="127.0.0.1"),
        "PORT": get_env("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": int(get_env("POSTGRES_CONN_MAX_AGE", default="60")),
    }
}

LANGUAGE_CODE = "zh-hans"

TIME_ZONE = "Asia/Shanghai"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
