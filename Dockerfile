# The application image, which the migrate and app services both run
# (ENG-01 7.1). The settings and passwords come from compose.yaml, never from
# the image: .dockerignore keeps .env and the client's data out of it.
FROM python:3.12-slim

# uv, the same version as on the developer's machine, from its own image.
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

# The release this image is, from the build argument compose.yaml passes, so
# an export's Source sheet can name it (ENG-01 7.1).
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

# The locked dependencies first, without the development tools, so a change
# to the code doesn't install them again.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY manage.py ./
COPY config config
COPY ids_audit ids_audit
COPY sql sql

# The CSS and JavaScript WhiteNoise serves. The two values only let the
# settings load: nothing connects to the database here.
RUN SECRET_KEY=collectstatic DB_APP_PASSWORD=unused \
    python manage.py collectstatic --noinput

# Not as root. A new uploads volume takes /uploads' owner when it's first
# mounted, so the application can write to it.
RUN useradd --system --no-create-home app && mkdir /uploads && chown app /uploads
USER app

EXPOSE 8000
CMD ["gunicorn", "--config", "config/gunicorn.py", "config.wsgi"]
