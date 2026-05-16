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


def get_env_bool(name: str, *, default: bool = False) -> bool:
    raw_value = get_env(name, default="true" if default else "false")
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def get_env_int(name: str, *, default: int) -> int:
    raw_value = get_env(name, default=str(default))
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


def get_env_float(name: str, *, default: float) -> float:
    raw_value = get_env(name, default=str(default))
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def get_env_list(name: str, *, default: str = "") -> list[str]:
    return [
        item.strip()
        for item in get_env(name, default=default).split(",")
        if item.strip()
    ]


load_env_file(BASE_DIR / ".env")

DEBUG = get_env_bool("DJANGO_DEBUG", default=True)

SECRET_KEY = get_env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Missing required environment variable: DJANGO_SECRET_KEY when DJANGO_DEBUG is false")
    SECRET_KEY = "django-insecure-local-dev-only"

ALLOWED_HOSTS = get_env_list(
    "DJANGO_ALLOWED_HOSTS",
    default="127.0.0.1,localhost,testserver",
)
CSRF_TRUSTED_ORIGINS = get_env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = get_env_bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = get_env_int("DJANGO_SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = get_env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = get_env_bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)
CSRF_COOKIE_SECURE = get_env_bool("DJANGO_CSRF_COOKIE_SECURE", default=not DEBUG)
CODEMASTER_AUTH_COOKIE_SECURE = get_env_bool("CODEMASTER_AUTH_COOKIE_SECURE", default=not DEBUG)


HOMEWORK_IMPORT_ROUTER_ENABLED = get_env_bool("HOMEWORK_IMPORT_ROUTER_ENABLED", default=False)
HOMEWORK_IMPORT_DEBUG = get_env_bool("HOMEWORK_IMPORT_DEBUG", default=False)

HOMEWORK_PARSE_PROVIDER_TEXT = get_env("HOMEWORK_PARSE_PROVIDER_TEXT", default="qwen")
DASHSCOPE_API_KEY = get_env("DASHSCOPE_API_KEY")
HOMEWORK_LLM_MODEL = get_env("HOMEWORK_LLM_MODEL", default="qwen-plus")
HOMEWORK_LLM_API_URL = get_env(
    "HOMEWORK_LLM_API_URL",
    default="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
)
HOMEWORK_LLM_TIMEOUT_SECONDS = get_env_int("HOMEWORK_LLM_TIMEOUT_SECONDS", default=40)
HOMEWORK_SSL_VERIFY = get_env_bool("HOMEWORK_SSL_VERIFY", default=True)
HOMEWORK_SSL_CA_BUNDLE = get_env("HOMEWORK_SSL_CA_BUNDLE")
HOMEWORK_REQUESTS_USER_AGENT = get_env(
    "HOMEWORK_REQUESTS_USER_AGENT",
    default="codemaster-homework-import/1.0",
)

HOMEWORK_PARSE_PROVIDER_OCR = get_env("HOMEWORK_PARSE_PROVIDER_OCR", default="volc_vision")
ARK_API_KEY = get_env("ARK_API_KEY")
VOLC_VISION_MODEL = get_env("VOLC_VISION_MODEL", default="doubao-seed-1-6-vision-250815")
VOLC_VISION_API_URL = get_env(
    "VOLC_VISION_API_URL",
    default="https://ark.cn-beijing.volces.com/api/v3/responses",
)
VOLC_VISION_TIMEOUT_SECONDS = get_env_int("VOLC_VISION_TIMEOUT_SECONDS", default=40)
VOLC_VISION_CONNECT_TIMEOUT_SECONDS = get_env_float(
    "VOLC_VISION_CONNECT_TIMEOUT_SECONDS",
    default=min(float(VOLC_VISION_TIMEOUT_SECONDS), 10.0),
)
VOLC_VISION_READ_TIMEOUT_SECONDS = get_env_float(
    "VOLC_VISION_READ_TIMEOUT_SECONDS",
    default=max(float(VOLC_VISION_TIMEOUT_SECONDS), 60.0),
)
VOLC_VISION_MAX_RETRIES = get_env_int("VOLC_VISION_MAX_RETRIES", default=2)
VOLC_VISION_RETRY_BACKOFF_SECONDS = get_env_float("VOLC_VISION_RETRY_BACKOFF_SECONDS", default=0.2)

HOMEWORK_PDF_FORCE_OCR = get_env_bool("HOMEWORK_PDF_FORCE_OCR", default=False)
HOMEWORK_PDF_MIN_TEXT_LENGTH = get_env_int("HOMEWORK_PDF_MIN_TEXT_LENGTH", default=200)
HOMEWORK_PDF_MIN_LINE_COUNT = get_env_int("HOMEWORK_PDF_MIN_LINE_COUNT", default=8)
HOMEWORK_PARSE_REQUIRE_REVIEW = get_env_bool("HOMEWORK_PARSE_REQUIRE_REVIEW", default=True)
HOMEWORK_IMPORT_ALLOW_FALLBACK_HEURISTIC = get_env_bool(
    "HOMEWORK_IMPORT_ALLOW_FALLBACK_HEURISTIC",
    default=True,
)
DEFAULT_TEST_PASSWORD = get_env("CODEMASTER_DEFAULT_TEST_PASSWORD")
IMPORT_CPP_DEFAULT_PASSWORD = get_env("CODEMASTER_IMPORT_DEFAULT_PASSWORD")
WECHAT_MINIAPP_APPID = get_env("WECHAT_MINIAPP_APPID")
WECHAT_MINIAPP_SECRET = get_env("WECHAT_MINIAPP_SECRET")
HERMES_INGEST_TOKEN = get_env("HERMES_INGEST_TOKEN")

REDIS_URL = get_env("REDIS_URL")
LIVEKIT_URL = get_env("LIVEKIT_URL", default="ws://localhost:7880")
LIVEKIT_PUBLIC_URL = get_env("LIVEKIT_PUBLIC_URL", default=LIVEKIT_URL)
LIVEKIT_SERVER_URL = get_env(
    "LIVEKIT_SERVER_URL",
    default=LIVEKIT_URL.replace("wss://", "https://", 1).replace("ws://", "http://", 1),
)
LIVEKIT_API_KEY = get_env("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = get_env("LIVEKIT_API_SECRET")
LIVEKIT_CLIENT_JS_URL = get_env(
    "LIVEKIT_CLIENT_JS_URL",
    default="https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js",
)
LIVEKIT_RECORDING_ENABLED = get_env_bool("LIVEKIT_RECORDING_ENABLED", default=False)
LIVEKIT_RECORDING_FILE_PREFIX = get_env("LIVEKIT_RECORDING_FILE_PREFIX", default="/recordings/live-classroom")
LIVEKIT_RECORDING_PUBLIC_URL_PREFIX = get_env("LIVEKIT_RECORDING_PUBLIC_URL_PREFIX", default="/media/live-classroom")


INSTALLED_APPS = [
    "daphne",
    "channels",
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
ASGI_APPLICATION = "codemaster_system.asgi.application"

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

if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

LANGUAGE_CODE = "zh-hans"

TIME_ZONE = "Asia/Shanghai"

USE_I18N = True

USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = Path(get_env("DJANGO_STATIC_ROOT", default=str(BASE_DIR / "staticfiles")))
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(get_env("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": get_env("DJANGO_LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django.server": {
            "handlers": ["console"],
            "level": get_env("DJANGO_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
}
