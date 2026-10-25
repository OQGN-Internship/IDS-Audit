"""Shared test setup (ENG-01 7.5)."""

from pathlib import Path

import pytest
from django.db import connection, transaction

from ids_audit.imports.models import Upload
from tests.factories import make_upload

GRANTS_SQL = Path(__file__).resolve().parents[1] / "sql" / "grants.sql"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run the browser tests last, keeping every other test's order.

    Playwright keeps an event loop running in the main thread from its first
    test to the end of the session, and Django refuses database work in a
    thread with one, so a database test after it would fail.
    """
    items.sort(key=lambda item: "page" in getattr(item, "fixturenames", ()))


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker) -> None:
    """Apply sql/grants.sql to the test database, once it's migrated.

    Default privileges belong to one database, so they never reach the test
    database. This gives the tests the same grants as the application.
    """
    with django_db_blocker.unblock(), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(GRANTS_SQL.read_text(encoding="utf-8"))


@pytest.fixture
def upload(db) -> Upload:
    """An upload, with the user, report and ruleset it needs."""
    return make_upload()


@pytest.fixture
def uploads_dir(settings, tmp_path: Path) -> Path:
    """An empty folder for received files, set as UPLOADS_DIR (ENG-01 7.5).

    tmp_path is a new folder for each test, so no file reaches the working copy.
    """
    folder = tmp_path / "uploads"
    folder.mkdir()
    settings.UPLOADS_DIR = str(folder)
    return folder
