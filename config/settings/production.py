"""Settings for the app service in Docker (ENG-01 7.1).

It connects as ids_app, which can never change an audit event or a posting
(ENG-01 6.3). Only migrate connects as the owner, with config.settings.migrate.
"""

from .base import *  # noqa: F403
from .base import DATABASES, MIDDLEWARE, env

DEBUG = env("DEBUG", "False") == "True"

DATABASES = {
    "default": {
        **DATABASES["default"],
        "USER": "ids_app",
        "PASSWORD": env("DB_APP_PASSWORD"),
    }
}

# WhiteNoise serves the CSS and JavaScript, straight after the security
# middleware, as its documentation asks.
MIDDLEWARE = [
    MIDDLEWARE[0],
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[1:],
]
# Each file's name carries a hash of its content, so a browser never keeps an
# old copy after an update.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
