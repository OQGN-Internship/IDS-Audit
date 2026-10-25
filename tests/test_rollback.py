"""Rollback: the latest approved upload stops counting, and every figure goes
back to what it was before it (ENG-02 10.3).
"""

import datetime
import io
import json
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import commit_upload
from ids_audit.imports.models import SkippedRow, Upload
from ids_audit.imports.rollback import (
    RollbackRefused,
    actual_spend_change,
    roll_back_upload,
)
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.imports.states import WrongState
from ids_audit.ledger.models import CcDeptMap, GlCiMap, Posting, PostingResolved
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from tests.factories import commit, make_report, make_upload, make_user
from tests.files import HEADER, LINE, line, total_row, xlsx_file
from tests.roles import as_app

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status
GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")
CC = ("Cost Center", "Cost Center Name", "Department Code", "Department Name")

# July and August, with both mappings, and a row skipped for its decimals.
FIRST = xlsx_file(
    {
        "Spend": [
            HEADER,
            LINE,
            line(DocumentNo="2", Pstng_Date="10.08.2025", Amount_in_LC="300.000"),
            line(DocumentNo="3", Amount_in_LC="1.2345"),
            total_row("1301.2345"),
        ],
        "GL": [GL, ("610100", "Software", "2101", "Licences")],
        "CC": [CC, ("4520", "Enterprise applications", "APP", "Applications")],
    }
)
# August again, which replaces the first file's August, and September, with
# 610100 moved to another budget line and 4520 to another department.
SECOND = xlsx_file(
    {
        "Spend": [
            HEADER,
            line(DocumentNo="4", Pstng_Date="12.08.2025", Amount_in_LC="700.000"),
            line(DocumentNo="5", Pstng_Date="05.09.2025", Amount_in_LC="50.000"),
            total_row("750.000"),
        ],
        "GL": [GL, ("610100", "Software", "2401", "Services")],
        "CC": [CC, ("4520", "Enterprise applications", "INF", "Infrastructure")],
    }
)


def approved(data: bytes, report: Report) -> Upload:
    """Receive, stage and approve a file in the report, as its creator."""
    upload = receive_file(report, report.created_by, "spend.xlsx", data)
    upload = stage_upload(upload, upload.uploaded_by)
    return commit_upload(upload, upload.uploaded_by)


def report_totals(report: Report) -> dict:
    """Return the report's entry in manage.py report_totals (ENG-01 7.2)."""
    out = io.StringIO()
    call_command("report_totals", stdout=out)
    (entry,) = [
        e for e in json.loads(out.getvalue())["reports"] if e["report"] == report.pk
    ]
    return entry


def resolved(report: Report) -> list[tuple]:
    """Return every row of posting_resolved for the report, in id order."""
    return list(
        PostingResolved.objects.filter(report=report)
        .order_by("pk")
        .values_list("pk", "amount", "ci_code", "dept_code", "is_outside_ids")
    )


def test_rolling_back_restores_every_figure_exactly() -> None:
    """Verify, after a second file that replaced a month and moved both
    mappings, its rollback returns posting_resolved and every report_totals
    figure to what they were before it was approved.
    """
    report = make_report()
    approved(FIRST, report)
    figures, rows = report_totals(report), resolved(report)
    second = approved(SECOND, report)
    assert report_totals(report) != figures
    roll_back_upload(second, make_user(), "Wrong mapping")
    assert report_totals(report) == figures
    assert resolved(report) == rows


def test_a_rollback_is_recorded_and_keeps_every_row() -> None:
    """Verify the upload is rolled back by the user with the time, one event
    keeps the reason, and its postings, mapping rows and skipped rows stay.
    """
    report = make_report()
    upload = approved(FIRST, report)
    user = make_user()
    roll_back_upload(upload, user, "  Wrong file  ")
    upload.refresh_from_db()
    assert (upload.status, upload.rolled_back_by) == (Status.ROLLED_BACK, user)
    assert upload.rolled_back_at is not None
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_ROLLED_BACK)
    assert (event.user, event.report, event.upload, event.details) == (
        user,
        report,
        upload,
        {"reason": "Wrong file"},
    )
    assert Posting.objects.filter(upload=upload).count() == 2
    assert GlCiMap.objects.filter(upload=upload).count() == 1
    assert CcDeptMap.objects.filter(upload=upload).count() == 1
    assert SkippedRow.objects.filter(upload=upload).count() == 1
    assert queries.totals(report).total == Decimal("0.000")


def test_the_confirmation_figure_is_the_actual_spend_once_rolled_back() -> None:
    """Verify actual_spend_change gives the actual spend now and after, the
    after equal to the figure once rolled back, and changes nothing itself.
    """
    report = make_report()
    approved(FIRST, report)
    second = approved(SECOND, report)
    before, after = actual_spend_change(second)
    assert (before, after) == (Decimal("1750.000"), Decimal("1300.000"))
    second.refresh_from_db()
    assert second.status == Status.COMMITTED
    assert queries.totals(report).total == before
    roll_back_upload(second, make_user(), "Wrong mapping")
    assert queries.totals(report).total == after


def test_an_older_upload_can_t_be_rolled_back_before_a_newer_one() -> None:
    """Verify only the most recently committed upload rolls back, and a
    refusal changes nothing and writes no event.
    """
    report = make_report()
    first = approved(FIRST, report)
    approved(SECOND, report)
    with pytest.raises(RollbackRefused, match="Only upload 2"):
        roll_back_upload(first, make_user(), "Wrong file")
    first.refresh_from_db()
    assert first.status == Status.COMMITTED
    assert not AuditEvent.objects.filter(action="upload_rolled_back").exists()


def test_the_latest_is_by_the_order_of_commits() -> None:
    """Verify of two uploads committed in the opposite order to their numbers,
    the one committed last is the latest, and of two committed at the same
    time, the higher number (ENG-02 10.2).
    """
    report = make_report()
    one, two = make_upload(report), make_upload(report)
    commit(two, minute=1)
    commit(one, minute=2)
    with pytest.raises(RollbackRefused, match="Only upload 1"):
        roll_back_upload(two, make_user(), "Wrong file")
    roll_back_upload(one, make_user(), "Wrong file")

    other = make_report()
    three, four = make_upload(other), make_upload(other)
    commit(three, minute=5)
    commit(four, minute=5)
    with pytest.raises(RollbackRefused, match="Only upload 2"):
        roll_back_upload(three, make_user(), "Wrong file")


@pytest.mark.parametrize(
    "status", [Status.READING, Status.NEEDS_COLUMNS, Status.AWAITING_APPROVAL]
)
def test_a_rollback_waits_for_an_open_upload(status) -> None:
    """Verify a rollback is refused while the report has an open upload."""
    report = make_report()
    upload = approved(FIRST, report)
    make_upload(report, status=status)
    with pytest.raises(RollbackRefused, match="Upload 2 is still open"):
        roll_back_upload(upload, make_user(), "Wrong file")
    upload.refresh_from_db()
    assert upload.status == Status.COMMITTED


def test_an_interrupted_read_doesn_t_hold_up_a_rollback() -> None:
    """Verify a read left at reading more than 10 minutes is cleared first."""
    report = make_report()
    upload = approved(FIRST, report)
    stuck = make_upload(report)
    old = timezone.now() - datetime.timedelta(minutes=11)
    Upload.objects.filter(pk=stuck.pk).update(uploaded_at=old)
    roll_back_upload(upload, make_user(), "Wrong file")
    stuck.refresh_from_db()
    assert stuck.status == Status.FAILED


@pytest.mark.parametrize("reason", ["", "  "])
def test_a_rollback_without_a_reason_is_refused(reason) -> None:
    """Verify an empty reason changes nothing."""
    upload = approved(FIRST, make_report())
    with pytest.raises(ValueError, match="A reason is needed"):
        roll_back_upload(upload, make_user(), reason)
    upload.refresh_from_db()
    assert upload.status == Status.COMMITTED


@pytest.mark.parametrize("status", [Status.REJECTED, Status.FAILED, Status.ROLLED_BACK])
def test_only_a_committed_upload_rolls_back(status) -> None:
    """Verify an upload that isn't committed is refused with WrongState."""
    upload = make_upload(status=status)
    with pytest.raises(WrongState):
        roll_back_upload(upload, make_user(), "Wrong file")


def test_a_rolled_back_file_can_be_uploaded_again() -> None:
    """Verify the same bytes are received again once rolled back (ENG-02 7.1)."""
    report = make_report()
    roll_back_upload(approved(FIRST, report), make_user(), "Approved too soon")
    assert receive_file(report, report.created_by, "again.xlsx", FIRST).number == 2


def test_rolling_back_works_as_ids_app() -> None:
    """Verify ids_app has every right a rollback and its figure need."""
    upload = approved(FIRST, make_report())
    with as_app():
        actual_spend_change(upload)
        roll_back_upload(upload, make_user(), "Wrong file")
    upload.refresh_from_db()
    assert upload.status == Status.ROLLED_BACK
