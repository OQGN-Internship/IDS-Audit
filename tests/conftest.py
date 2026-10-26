"""Shared test setup (ENG-01 7.5)."""

import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from ids_audit.imports.commit import commit_upload
from ids_audit.imports.models import Upload
from ids_audit.imports.services import create_report, receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.reports.models import Report
from tests.factories import make_upload, make_user


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
    database. This gives the tests the same grants as the application, with
    the command the migrate job runs.
    """
    with django_db_blocker.unblock():
        call_command("apply_grants", stdout=io.StringIO())


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


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_report(db, uploads_dir) -> Report:
    """A report holding the sample file, received, read and approved by a
    fixed administrator, as a user does it (ENG-02 12.3).
    """
    user = make_user()
    report = create_report("Sample", user)
    upload = receive_file(
        report, user, "sample.xlsx", (FIXTURES_DIR / "sample.xlsx").read_bytes()
    )
    commit_upload(stage_upload(upload, user), user)
    return report


@pytest.fixture
def sample_expected() -> dict:
    """The sample file's expected result (ENG-02 12.3)."""
    with open(FIXTURES_DIR / "sample.expected.json", encoding="utf-8") as file:
        return json.load(file)
