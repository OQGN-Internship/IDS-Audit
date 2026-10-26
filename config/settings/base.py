"""Settings shared by every environment (ENG-01 4).

Every value comes from the environment. Outside Docker, Django first reads
them from .env in the repository root (ENG-01 7.5). ENG-01 7.1 lists each
setting and its default.
"""

import datetime
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str:
    """Return a setting from the environment, or its default if it has one."""
    value = os.environ.get(name) or default
    if not value:
        raise ImproperlyConfigured(f"{name} is not set. Add it to .env (ENG-01 7.1).")
    return value


SECRET_KEY = env("SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    # First, so its changepassword and axes_reset_username replace Django's and
    # django-axes' commands of the same names (ENG-01 6.1).
    "ids_audit.accounts",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "ids_audit.imports",
    "ids_audit.ledger",
    "ids_audit.reports",
    "ids_audit.audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Every page but the sign-in page needs a signed-in user, who has chosen
    # their own password (ENG-01 6.1).
    "ids_audit.accounts.middleware.SignInRequired",
    "ids_audit.accounts.middleware.PasswordChangeRequired",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # No page is kept in the browser's cache (ENG-01 6.4).
    "ids_audit.accounts.middleware.NoStore",
    # Last, as django-axes asks, so it can answer a locked-out sign-in.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Pages and the partial templates HTMX swaps in (ENG-01 4).
        "DIRS": [BASE_DIR / "ids_audit" / "templates"],
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

# Set before the first migration, as Django requires (ENG-01 6.1).
AUTH_USER_MODEL = "accounts.User"

# Each environment's settings add the role and its password (ENG-01 6.3).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "ids_audit"),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "5432"),
        # Fail within seconds, not minutes, when the database isn't running.
        "OPTIONS": {"connect_timeout": 5},
    }
}

# Sign-in (ENG-01 6.1). django-axes checks a sign-in for a lock-out before
# Django checks the password.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "sign_in"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "sign_in"

# Argon2 first, so every new password is hashed with it.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ("username", "full_name", "email")},
    },
]

# A session ends after this long without a request (ENG-01 6.1). Saving it on
# every request starts the time again.
SESSION_IDLE_MINUTES = int(env("SESSION_IDLE_MINUTES", "30"))
SESSION_COOKIE_AGE = SESSION_IDLE_MINUTES * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# The lock-out (ENG-01 6.1). None of these is django-axes' default. By
# username, since every user signs in from the same machine.
LOCKOUT_ATTEMPTS = int(env("LOCKOUT_ATTEMPTS", "5"))
LOCKOUT_MINUTES = int(env("LOCKOUT_MINUTES", "15"))
AXES_FAILURE_LIMIT = LOCKOUT_ATTEMPTS
AXES_COOLOFF_TIME = datetime.timedelta(minutes=LOCKOUT_MINUTES)
AXES_LOCKOUT_PARAMETERS = ["username"]
AXES_RESET_ON_SUCCESS = True
# So the lock lasts LOCKOUT_MINUTES from the failure that started it, however
# often someone tries meanwhile.
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
AXES_LOCKOUT_CALLABLE = "ids_audit.accounts.views.locked_out"
# django-axes warns when the lock-out isn't by address too. It's by username on
# purpose: a lock-out by address would lock everyone on this machine.
SILENCED_SYSTEM_CHECKS = ["axes.W006"]

# Every response says it may not be shown inside a frame (ENG-01 6.4).
X_FRAME_OPTIONS = "DENY"

# CSS and the JavaScript libraries, kept in the repository (ENG-01 3).
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "ids_audit" / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Where received files are stored (ENG-01 7.5). It has no default, so no file
# is ever stored in the working copy by accident: the tests point it at a
# temporary folder, and from Phase 2 compose.yaml sets it to the uploads volume.
UPLOADS_DIR = os.environ.get("UPLOADS_DIR", "")

# The largest file received, in megabytes of 1,048,576 bytes (ENG-01 6.4).
MAX_UPLOAD_MB = int(env("MAX_UPLOAD_MB", "50"))

# The release this is, which the image is built with (ENG-01 7.1). An
# export's Source sheet shows it (ENG-03 9.2).
APP_VERSION = os.environ.get("APP_VERSION") or "dev"

# Ids are bigint, as ENG-02 2 gives for posting.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django's warnings and errors, a failed request's traceback included, go to
# standard error, where docker compose logs app shows them (ENG-01 7.4).
# Django's own default sends errors only to ADMINS by email, and there are
# none. Gunicorn writes each request's line itself (config/gunicorn.py).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "line": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "line",
        },
    },
    "root": {"handlers": ["stderr"], "level": "WARNING"},
}

# Times are stored in UTC and shown in Muscat time (ENG-01 6.3).
USE_TZ = True
TIME_ZONE = env("TIME_ZONE", "Asia/Muscat")
