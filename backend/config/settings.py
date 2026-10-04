import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-this-secret")
DEBUG = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if host.strip()]

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
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": name}
    if parsed.scheme in {"postgres", "postgresql", "postgresql+psycopg"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or "",
            "CONN_MAX_AGE": 60,
        }
    if parsed.scheme in {"mysql", "mysql+pymysql"}:
        return {
            "ENGINE": "django.db.backends.mysql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or "",
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"charset": "utf8mb4"},
        }
    raise ValueError("DATABASE_URL must use sqlite, postgresql, or mysql")


DATABASES = {"default": database_config()}
if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    import pymysql

    pymysql.install_as_MySQLdb()

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
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Manila")
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
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
