"""Gunicorn's settings for the app service (ENG-01 7.1), from the
environment that compose.yaml gives it.
"""

import os

# Every interface inside the container. Compose publishes the port on the
# machine's 127.0.0.1 only (ENG-01 6.4).
bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
# A request still running after this many seconds is stopped. It must stay
# below the 10 minutes after which a read counts as interrupted (ENG-02 6).
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "300"))
# Each request to standard output, with how long it took (ENG-01 7.4).
accesslog = "-"
access_log_format = '%(t)s "%(r)s" %(s)s %(b)s %(M)sms'
