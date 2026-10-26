"""Settings for Django on the developer's machine, used by manage.py (ENG-01 7.5)."""

from .base import *  # noqa: F403
from .base import DATABASES, env

DEBUG = True

# On the developer's machine, Django connects as the owner role, for the tests,
# migrate, makemigrations and the shell (ENG-01 6.3 and 7.5).
DATABASES = {
    "default": {
        **DATABASES["default"],
        "USER": "ids_owner",
        "PASSWORD": env("DB_OWNER_PASSWORD"),
    }
}
