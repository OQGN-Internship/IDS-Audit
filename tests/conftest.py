"""Shared test setup (ENG-01 7.5)."""

from pathlib import Path

import pytest
from django.db import connection, transaction

from ids_audit.imports.models import Upload
from tests.factories import make_upload

GRANTS_SQL = Path(__file__).resolve().parents[1] / "sql" / "grants.sql"


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
