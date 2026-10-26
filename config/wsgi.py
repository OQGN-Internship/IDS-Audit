"""The entry point Gunicorn serves (ENG-01 2). It uses the production settings
unless DJANGO_SETTINGS_MODULE names others.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()
