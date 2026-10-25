"""Rejecting and withdrawing an upload before it counts (ENG-02 6)."""

import pytest

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.decisions import reject_upload, withdraw_upload
from ids_audit.imports.models import SkippedRow, StagingRow, Upload
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.imports.states import WrongState
from ids_audit.ledger.models import Posting
from ids_audit.reports.models import Report
from tests.factories import make_report, make_upload, make_user
from tests.files import HEADER, LINE, csv_file, line, total_row
from tests.roles import as_app

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status
Action = AuditEvent.Action
DECISIONS = [
    pytest.param(reject_upload, Action.UPLOAD_REJECTED, id="reject"),
    pytest.param(withdraw_upload, Action.UPLOAD_WITHDRAWN, id="withdraw"),
]

# Two lines, a row skipped for its decimals, and a total that reconciles.
FILE = csv_file(
    LINE,
    line(DocumentNo="2", Amount_in_LC="250.000"),
    line(DocumentNo="3", Amount_in_LC="1.2345"),
    total_row("1251.2345"),
)
# The same lines with no amount column, so the file stops at needs_columns.
NO_AMOUNT = csv_file(LINE[:8], header=HEADER[:8])


def staged(data: bytes, report: Report | None = None) -> Upload:
    """Receive and stage a file, in a new report unless given one."""
    report = report or make_report()
    upload = receive_file(report, report.created_by, "spend.csv", data)
    upload = stage_upload(upload, upload.uploaded_by)
    upload.refresh_from_db()
    return upload


@pytest.mark.parametrize(("decide", "action"), DECISIONS)
def test_an_upload_awaiting_approval_is_closed_with_its_reason(decide, action) -> None:
    """Verify the upload is rejected by whoever decided, its staging rows go,
    its preview, skipped rows and figures stay, nothing is committed, and one
    event keeps the reason, trimmed.
    """
    upload = staged(FILE)
    preview = upload.preview
    user = make_user()
    decide(upload, user, "  Wrong period  ")
    upload.refresh_from_db()
    assert (upload.status, upload.decided_by) == (Status.REJECTED, user)
    assert upload.decided_at is not None
    assert not StagingRow.objects.filter(upload=upload).exists()
    assert upload.preview == preview
    assert (upload.rows_new, upload.rows_skipped) == (2, 1)
    assert SkippedRow.objects.filter(upload=upload).count() == 1
    assert not Posting.objects.exists()
    event = AuditEvent.objects.get(action=action)
    assert (event.user, event.report, event.upload, event.details) == (
        user,
        upload.report,
        upload,
        {"reason": "Wrong period"},
    )


@pytest.mark.parametrize(("decide", "action"), DECISIONS)
def test_an_upload_needing_columns_can_be_closed(decide, action) -> None:
    """Verify an upload stopped at needs_columns can be rejected as well as
    withdrawn, so a user with approve can clear one its uploader left.
    """
    upload = staged(NO_AMOUNT)
    assert upload.status == Status.NEEDS_COLUMNS
    decide(upload, make_user(), "Left by its uploader")
    upload.refresh_from_db()
    assert upload.status == Status.REJECTED
    assert AuditEvent.objects.filter(action=action, upload=upload).count() == 1


@pytest.mark.parametrize(("decide", "action"), DECISIONS)
@pytest.mark.parametrize("reason", ["", "   "])
def test_a_decision_without_a_reason_is_refused(decide, action, reason) -> None:
    """Verify an empty reason changes nothing and writes no event."""
    upload = staged(FILE)
    with pytest.raises(ValueError, match="A reason is needed"):
        decide(upload, make_user(), reason)
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert StagingRow.objects.filter(upload=upload).exists()
    assert not AuditEvent.objects.filter(action=action).exists()


@pytest.mark.parametrize(("decide", "action"), DECISIONS)
@pytest.mark.parametrize(
    "status",
    [
        Status.READING,
        Status.COMMITTED,
        Status.REJECTED,
        Status.FAILED,
        Status.ROLLED_BACK,
    ],
)
def test_only_an_open_upload_past_reading_can_be_closed(decide, action, status) -> None:
    """Verify every other state is refused with WrongState, changing nothing."""
    upload = make_upload(status=status)
    with pytest.raises(WrongState):
        decide(upload, make_user(), "A reason")
    upload.refresh_from_db()
    assert upload.status == status
    assert not AuditEvent.objects.filter(action=action).exists()


def test_a_rejected_upload_frees_its_report_for_the_next_file() -> None:
    """Verify the report takes a new file once its open upload is rejected."""
    report = make_report()
    reject_upload(staged(FILE, report), make_user(), "Wrong file")
    upload = receive_file(report, report.created_by, "next.csv", FILE)
    assert upload.number == 2


def test_closing_works_as_ids_app() -> None:
    """Verify ids_app has every right a decision needs, deleting staging rows
    included.
    """
    first, second = staged(FILE), staged(FILE)
    with as_app():
        reject_upload(first, make_user(), "Wrong file")
        withdraw_upload(second, make_user(), "Sent too soon")
    assert not StagingRow.objects.exists()
