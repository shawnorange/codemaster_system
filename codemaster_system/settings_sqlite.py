from .settings import *  # noqa: F401,F403


DEBUG = True
STATIC_ROOT = BASE_DIR / ".tmp_sqlite_staticfiles"
MEDIA_ROOT = BASE_DIR / ".tmp_sqlite_media"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test.sqlite3",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
