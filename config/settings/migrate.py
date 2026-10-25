"""Settings for the migrate job in Docker (ENG-01 7.1).

It connects as ids_owner, which owns the tables, to run the migrations and
then sql/grants.sql. Only this job is given the owner's password, so the app
service can never connect as the owner.
"""

from .base import *  # noqa: F403
from .base import DATABASES, env

DATABASES = {
    "default": {
        **DATABASES["default"],
        "USER": "ids_owner",
        "PASSWORD": env("DB_OWNER_PASSWORD"),
    }
}
