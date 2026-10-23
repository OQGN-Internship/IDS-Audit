"""Settings shared by every environment (ENG-01 4).

Every value comes from the environment. Outside Docker, Django first reads
them from .env in the repository root (ENG-01 7.5). ENG-01 7.1 lists each
setting and its default.
"""

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
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "ids_audit.accounts",
    "ids_audit.imports",
    "ids_audit.ledger",
    "ids_audit.reports",
    "ids_audit.audit",
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

# Ids are bigint, as ENG-02 2 gives for posting.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Times are stored in UTC and shown in Muscat time (ENG-01 6.3).
USE_TZ = True
TIME_ZONE = env("TIME_ZONE", "Asia/Muscat")
