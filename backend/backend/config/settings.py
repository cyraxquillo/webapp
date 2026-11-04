import os
import logging
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set before starting Django")

DEBUG = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes"}
SECURE_SSL_REDIRECT = os.getenv(
    "SECURE_SSL_REDIRECT",
    "false" if DEBUG else "true",
).lower() in {"1", "true", "yes"}
SECURE_HSTS_SECONDS = int(
    os.getenv("SECURE_HSTS_SECONDS", "0" if DEBUG else "3600")
)
SESSION_COOKIE_HTTPONLY = os.getenv("SESSION_COOKIE_HTTPONLY", "true").lower() in {
    "1",
    "true",
    "yes",
}
X_FRAME_OPTIONS = os.getenv("X_FRAME_OPTIONS", "DENY")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "ALLOWED_HOSTS",
        "127.0.0.1,localhost,webapp-backend-skts.onrender.com,.onrender.com",
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "api.apps.ApiConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "api.middleware.RequirePasswordChangeMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "api.context_processors.notification_count",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


def database_config():
    url = os.getenv("DATABASE_URL", "sqlite:///./django.sqlite3")
    parsed = urlparse(url)
    if parsed.scheme in {"sqlite", "sqlite3"}:
        path = unquote(parsed.path)
        if path.startswith("/./"):
            path = path[1:]
        if path in {"", "/:memory:"}:
            name = ":memory:"
        else:
            name = Path(path.lstrip("/")) if not Path(path).is_absolute() else Path(path)
            name = str((BASE_DIR / name).resolve()) if not Path(name).is_absolute() else str(name)

        sqlite_options = {"timeout": 30}
        if name != ":memory:":
            try:
                with sqlite3.connect(name, timeout=30) as connection:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute("PRAGMA synchronous=NORMAL")
                    connection.execute("PRAGMA busy_timeout = 30000")
            except sqlite3.Error as error:
                logger.warning(
                    "Could not enable SQLite WAL mode for %s: %s",
                    name,
                    error,
                )
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": name,
            "OPTIONS": sqlite_options,
        }
    if parsed.scheme in {"postgres", "postgresql", "postgresql+psycopg"}:
        query = parse_qs(parsed.query)
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or unquote(query.get("host", [""])[0]),
            "PORT": parsed.port or query.get("port", [""])[0],
            "CONN_MAX_AGE": 60,
        }
    raise ValueError("DATABASE_URL must use sqlite or postgresql")


DATABASES = {"default": database_config()}

AUTH_USER_MODEL = "api.User"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() in {"1", "true", "yes"}
EMAIL_HOST_CONFIGURED = bool(os.getenv("EMAIL_HOST", "").strip())
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CSRF_TRUSTED_ORIGINS",
        "",
    ).split(",")
    if origin.strip()
]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "model_console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
        },
    },
    "loggers": {
        "api.ml.predict": {
            "handlers": ["model_console"],
            "level": "INFO",
            "propagate": False,
        },
        "api.views": {
            "handlers": ["model_console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
CORS_ALLOWED_ORIGINS = FRONTEND_ORIGINS
CORS_ALLOW_CREDENTIALS = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
    "DEFAULT_THROTTLE_RATES": {
        "login": "5/minute",
    },
}
SIMPLE_JWT = {
    "SIGNING_KEY": SECRET_KEY,
    "ALGORITHM": os.getenv("ALGORITHM", "HS256"),
    "ACCESS_TOKEN_LIFETIME": __import__("datetime").timedelta(
        minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    ),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "sub",
}
