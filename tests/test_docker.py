"""Running the application in Docker (ENG-01 7.1 and 7.5): the settings the
migrate and app services use, Gunicorn's, and what compose.yaml gives each
service. None of these needs the database.
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ids_audit.imports.states import READ_TIME_LIMIT

ROOT = Path(__file__).resolve().parents[1]
# Every setting .env may hold (ENG-01 7.1).
SETTING_NAMES = [
    line.split("=")[0]
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    if "=" in line and not line.startswith("#")
]
DUMMY = {
    "SECRET_KEY": "test-only-" + "k" * 50,
    "POSTGRES_PASSWORD": "test-only-postgres",
    "DB_OWNER_PASSWORD": "test-only-owner",
    "DB_APP_PASSWORD": "test-only-app",
}


def without_settings() -> dict:
    """The environment without any setting from .env, so nothing real reaches
    a command these tests run, or its output.
    """
    return {k: v for k, v in os.environ.items() if k not in SETTING_NAMES}


def load(module: str, monkeypatch, **values: str):
    """Import a settings or config module afresh with these values set."""
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(importlib.import_module(module))


def test_the_app_connects_as_ids_app_and_migrate_as_the_owner(monkeypatch) -> None:
    """Verify the production settings connect as ids_app with its password,
    and the migrate job's as ids_owner with the owner's, each with DEBUG off.
    """
    production = load("config.settings.production", monkeypatch, **DUMMY)
    database = production.DATABASES["default"]
    assert (database["USER"], database["PASSWORD"]) == ("ids_app", "test-only-app")
    assert production.DEBUG is False
    assert production.MIDDLEWARE[:2] == [
        "django.middleware.security.SecurityMiddleware",
        "whitenoise.middleware.WhiteNoiseMiddleware",
    ]

    migrate = load("config.settings.migrate", monkeypatch, **DUMMY)
    database = migrate.DATABASES["default"]
    assert (database["USER"], database["PASSWORD"]) == ("ids_owner", "test-only-owner")
    assert migrate.DEBUG is False


def test_the_production_settings_need_the_app_password(monkeypatch) -> None:
    """Verify the production settings refuse to load without DB_APP_PASSWORD,
    rather than connect with none.
    """
    from django.core.exceptions import ImproperlyConfigured

    monkeypatch.setenv("DB_APP_PASSWORD", "")
    with pytest.raises(ImproperlyConfigured, match="DB_APP_PASSWORD"):
        importlib.reload(importlib.import_module("config.settings.production"))


def test_gunicorn_stops_a_request_before_a_read_counts_as_interrupted(
    monkeypatch,
) -> None:
    """Verify Gunicorn listens on port 8000 with 3 workers and a 300-second
    timeout by default, below the 10 minutes of ENG-02 6, and takes both
    from the environment.
    """
    monkeypatch.delenv("GUNICORN_WORKERS", raising=False)
    monkeypatch.delenv("GUNICORN_TIMEOUT", raising=False)
    config = importlib.reload(importlib.import_module("config.gunicorn"))
    assert (config.bind, config.workers, config.timeout) == ("0.0.0.0:8000", 3, 300)
    assert config.timeout < READ_TIME_LIMIT.total_seconds()
    config = load(
        "config.gunicorn", monkeypatch, GUNICORN_WORKERS="5", GUNICORN_TIMEOUT="120"
    )
    assert (config.workers, config.timeout) == (5, 120)


def test_check_deploy_warns_only_about_https() -> None:
    """Verify manage.py check --deploy with the production settings reports
    only the HTTPS warnings, which don't apply while the application runs
    locally over plain HTTP (ENG-01 6.4).
    """
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy"],
        cwd=ROOT,
        env={
            **without_settings(),
            **DUMMY,
            "DEBUG": "False",
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
        },
        capture_output=True,
        text=True,
    )
    warnings = sorted(
        set(part.split(")")[0] for part in result.stderr.split("(security.")[1:])
    )
    assert warnings == ["W004", "W008", "W012", "W016"], result.stderr


@pytest.fixture
def compose(tmp_path) -> dict:
    """compose.yaml as Docker Compose reads it, with made-up passwords in
    place of .env's, or skip when Docker isn't installed.
    """
    if shutil.which("docker") is None:
        pytest.skip("Docker isn't installed.")
    env_file = tmp_path / "test.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in DUMMY.items()))
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=without_settings(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def test_the_app_and_database_are_reachable_from_this_machine_only(compose) -> None:
    """Verify Compose publishes the app's and the database's ports on
    127.0.0.1 only (ENG-01 6.4).
    """
    for name, target in (("app", 8000), ("db", 5432)):
        (port,) = compose[name]["ports"]
        assert (port["host_ip"], port["target"]) == ("127.0.0.1", target)


def test_each_service_gets_only_its_own_role_s_password(compose) -> None:
    """Verify migrate runs as the owner with the migrate settings and app as
    ids_app with the production settings, and neither gets the other's
    password or the database superuser's.
    """
    app, migrate = compose["app"]["environment"], compose["migrate"]["environment"]
    assert app["DJANGO_SETTINGS_MODULE"] == "config.settings.production"
    assert "DB_APP_PASSWORD" in app
    assert "DB_OWNER_PASSWORD" not in app and "POSTGRES_PASSWORD" not in app
    assert migrate["DJANGO_SETTINGS_MODULE"] == "config.settings.migrate"
    assert "DB_OWNER_PASSWORD" in migrate
    assert "DB_APP_PASSWORD" not in migrate and "POSTGRES_PASSWORD" not in migrate
    assert app["DB_HOST"] == migrate["DB_HOST"] == "db"


def test_app_starts_after_migrate_and_stores_files_on_the_uploads_volume(
    compose,
) -> None:
    """Verify migrate waits for a healthy database, runs the migrations and
    the grants, and app starts only once it has finished without an error,
    with UPLOADS_DIR on the uploads volume.
    """
    migrate, app = compose["migrate"], compose["app"]
    assert migrate["depends_on"]["db"]["condition"] == "service_healthy"
    assert (
        "migrate --noinput && python manage.py apply_grants" in migrate["command"][-1]
    )
    assert app["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert app["environment"]["UPLOADS_DIR"] == "/uploads"
    (volume,) = app["volumes"]
    assert (volume["type"], volume["source"], volume["target"]) == (
        "volume",
        "uploads",
        "/uploads",
    )


def test_the_image_leaves_out_env_and_the_client_s_data() -> None:
    """Verify .dockerignore keeps .env, the real export and the backups out
    of the application image (ENG-01 6.4).
    """
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for path in (".env", "real-export/", "backups/"):
        assert path in ignored
