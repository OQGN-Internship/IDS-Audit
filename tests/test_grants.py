"""ids_app's rights on the insert-only tables (ENG-01 6.3, Tested by).

Each test switches to ids_app with SET LOCAL ROLE, so the database refuses a
statement as it would refuse the application (ENG-01 7.5). Every refusal has
a matching test that an allowed statement works, so a refusal can't come
from missing grants.
"""

from collections.abc import Callable

import pytest
from django.db import connection, models
from django.db.utils import DataError, ProgrammingError

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import SkippedRow, StagingRow, Upload
from ids_audit.ledger.models import CcDeptMap, GlCiMap, Posting
from ids_audit.reports.models import Ruleset
from tests.factories import spend_line
from tests.roles import INSUFFICIENT_PRIVILEGE, as_app

# An unsaved row for each insert-only table, linked to the upload.
NEW_ROW: dict[str, Callable[[Upload], models.Model]] = {
    "audit_event": lambda u: AuditEvent(
        action=AuditEvent.Action.UPLOAD_RECEIVED, report=u.report, upload=u
    ),
    "posting": lambda u: Posting(**spend_line(u)),
    "gl_ci_map": lambda u: GlCiMap(
        upload=u, report=u.report, gl_code="610100", ci_code="CI01"
    ),
    "cc_dept_map": lambda u: CcDeptMap(
        upload=u, report=u.report, cc_code="3100", dept_code="D01"
    ),
    "skipped_row": lambda u: SkippedRow(
        upload=u, row_number=9, reason="invalid date", raw_values={}
    ),
    "ruleset": lambda u: Ruleset(
        report=u.report, version=2, settings={}, created_by=u.uploaded_by
    ),
}
INSERT_ONLY = list(NEW_ROW)


@pytest.mark.parametrize("table", INSERT_ONLY)
def test_ids_app_can_insert_into_each_insert_only_table(
    table: str, upload: Upload
) -> None:
    """Verify ids_app can INSERT a row into each insert-only table."""
    row = NEW_ROW[table](upload)
    with as_app():
        row.save()
    assert type(row).objects.filter(pk=row.pk).exists()


@pytest.mark.parametrize(
    "statement",
    ["UPDATE {table} SET id = id", "DELETE FROM {table}", "TRUNCATE {table}"],
)
@pytest.mark.parametrize("table", INSERT_ONLY)
def test_ids_app_cannot_change_or_remove_rows_in_each_insert_only_table(
    table: str, statement: str, upload: Upload
) -> None:
    """Verify UPDATE, DELETE and TRUNCATE as ids_app are each refused."""
    NEW_ROW[table](upload).save()  # As ids_owner, so there's a row to change.
    with pytest.raises(ProgrammingError) as refused, as_app() as cursor:
        cursor.execute(statement.format(table=table))
    assert refused.value.__cause__.sqlstate == INSUFFICIENT_PRIVILEGE


def test_ids_app_can_update_an_upload(upload: Upload) -> None:
    """Verify the UPDATE refused above works on a table that isn't insert-only."""
    with as_app() as cursor:
        cursor.execute("UPDATE upload SET status = 'failed' WHERE id = %s", [upload.pk])
    upload.refresh_from_db()
    assert upload.status == Upload.Status.FAILED


def test_ids_app_can_delete_staging_rows(upload: Upload) -> None:
    """Verify the DELETE refused above works on staging_row, as ENG-02 6 needs."""
    StagingRow.objects.create(**spend_line(upload))
    with as_app() as cursor:
        cursor.execute("DELETE FROM staging_row WHERE upload_id = %s", [upload.pk])
    assert not StagingRow.objects.exists()


def test_as_app_switches_to_ids_app_and_back(db) -> None:
    """Verify statements inside as_app() run as ids_app, and statements after it
    as ids_owner again, whether the statement inside worked or failed.

    It touches no table, so it doesn't depend on the grants it helps test.
    """

    def current_user() -> str:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            return cursor.fetchone()[0]

    with as_app():
        assert current_user() == "ids_app"
    assert current_user() == "ids_owner"
    with pytest.raises(DataError), as_app() as cursor:
        cursor.execute("SELECT 1 / 0")
    assert current_user() == "ids_owner"
