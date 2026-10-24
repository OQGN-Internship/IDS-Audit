"""Commit: approving an upload, so its postings count (ENG-01 4, ENG-02 10.1
and 10.2).
"""

import threading
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.utils import timezone

from ids_audit import audit
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import CommitCheckFailed, commit_upload
from ids_audit.imports.models import SkippedRow, StagingRow, Upload
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.imports.states import WrongState
from ids_audit.ledger.models import Posting, PostingResolved
from ids_audit.reports.models import Report
from tests.factories import make_report, make_user
from tests.files import LINE, csv_file, line, total_row
from tests.roles import as_app

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status
REPLACE_BY_PERIOD = Report.DuplicateMode.REPLACE_BY_PERIOD

# Two lines and a total that reconciles, and a row skipped for its decimals.
TWO_LINES = csv_file(
    LINE,
    line(DocumentNo="5100000102", Pstng_Date="20.08.2025", Amount_in_LC="250.500-"),
    line(DocumentNo="5100000103", Amount_in_LC="1.2345"),
    total_row("750.7345"),
)


def awaiting(data: bytes = TWO_LINES, report: Report | None = None) -> Upload:
    """Receive and stage a file, so its upload awaits approval."""
    report = report or make_report()
    upload = receive_file(report, report.created_by, "spend.csv", data)
    return stage_upload(upload, upload.uploaded_by)


def spend_fields(row) -> dict:
    """The fields a posting and a staging row share, by column."""
    return {
        field.attname: getattr(row, field.attname)
        for field in Posting._meta.concrete_fields
        if not field.primary_key
    }


def nothing_committed(upload: Upload) -> bool:
    """Return True when the upload still awaits approval, with its staging rows,
    and no posting, approval or duplicate mode was written.
    """
    upload.refresh_from_db()
    return (
        upload.status == Status.AWAITING_APPROVAL
        and (upload.decided_by, upload.decided_at) == (None, None)
        and StagingRow.objects.filter(upload=upload).count() == 2
        and not Posting.objects.exists()
        and not AuditEvent.objects.filter(action="upload_approved").exists()
        and Report.objects.get(pk=upload.report_id).duplicate_mode == ""
    )


# A commit


def test_each_staging_row_becomes_a_posting_with_every_field() -> None:
    """Verify every staging row is copied to a posting with every field
    unchanged, in the file's order.
    """
    upload = awaiting()
    staged = [spend_fields(row) for row in StagingRow.objects.order_by("id")]
    commit_upload(upload, make_user())
    assert [spend_fields(p) for p in Posting.objects.order_by("id")] == staged
    assert len(staged) == 2


def test_the_upload_is_committed_by_the_user_and_its_staging_rows_go() -> None:
    """Verify the upload is committed with who approved it and when, its
    staging rows are deleted, and its skipped and mapping rows stay (ENG-02 6).
    """
    upload = awaiting()
    approver = make_user()
    before = timezone.now()
    commit_upload(upload, approver)
    upload.refresh_from_db()
    assert upload.status == Status.COMMITTED
    assert upload.decided_by == approver
    assert before <= upload.decided_at <= timezone.now()
    assert not StagingRow.objects.exists()
    assert SkippedRow.objects.filter(upload=upload).count() == 1


def test_the_report_s_first_commit_sets_replace_by_period() -> None:
    """Verify the first commit sets the report's duplicate mode, and its event
    keeps the postings' number and total with that mode (ENG-02 10.1, step 6).
    """
    upload = awaiting()
    approver = make_user()
    commit_upload(upload, approver)
    report = Report.objects.get(pk=upload.report_id)
    assert report.duplicate_mode == REPLACE_BY_PERIOD
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_APPROVED)
    assert (event.user, event.report, event.upload) == (approver, report, upload)
    assert event.details == {
        "postings": 2,
        "amount": "749.500",
        "duplicate_mode": REPLACE_BY_PERIOD,
    }


def test_a_later_commit_leaves_the_duplicate_mode_alone() -> None:
    """Verify a commit in a report whose mode is set keeps that mode, and its
    event names none.
    """
    report = make_report()
    report.duplicate_mode = Report.DuplicateMode.LINE_KEY
    report.save()
    upload = awaiting(report=report)
    commit_upload(upload, make_user())
    report.refresh_from_db()
    assert report.duplicate_mode == Report.DuplicateMode.LINE_KEY
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_APPROVED)
    assert event.details == {"postings": 2, "amount": "749.500"}


def test_postings_count_once_committed() -> None:
    """Verify staged rows aren't in posting_resolved, and the postings are once
    the upload is committed (ENG-02 10.2).
    """
    upload = awaiting()
    assert not PostingResolved.objects.exists()
    commit_upload(upload, make_user())
    amounts = PostingResolved.objects.values_list("amount", flat=True)
    assert sorted(amounts) == [Decimal("-250.500"), Decimal("1000.000")]


def test_a_row_marked_duplicate_isn_t_committed() -> None:
    """Verify a staging row marked is_duplicate is left out, and the check
    compares with the preview's rows to commit (ENG-02 7.3 and 10.1).
    """
    upload = awaiting()
    StagingRow.objects.filter(row_number=3).update(is_duplicate=True)
    # What the preview would show in line-key mode, in Phase 2.
    Upload.objects.filter(pk=upload.pk).update(
        rows_new=1, preview={"amounts": {"to_commit": "1000.000"}}
    )
    commit_upload(upload, make_user())
    assert list(Posting.objects.values_list("row_number", flat=True)) == [2]


# Refusals


@pytest.mark.parametrize(
    "status", [s for s in Status.values if s != Status.AWAITING_APPROVAL]
)
def test_an_upload_not_awaiting_approval_is_refused(status) -> None:
    """Verify committing an upload in any other state is refused, and writes
    nothing (ENG-02 6 and 10.1, step 2).
    """
    upload = awaiting()
    Upload.objects.filter(pk=upload.pk).update(status=status)
    with pytest.raises(WrongState, match="not awaiting approval"):
        commit_upload(upload, make_user())
    upload.refresh_from_db()
    assert upload.status == status
    assert not Posting.objects.exists()
    assert StagingRow.objects.count() == 2


@pytest.mark.parametrize("check", ["total", "count"])
def test_postings_that_don_t_match_the_preview_stop_the_commit(check) -> None:
    """Verify a commit whose postings differ from the preview's total, or from
    its count, is undone whole: no posting, no approval, no event, no
    duplicate mode, and the staging rows stay (ENG-02 10.1, step 5).
    """
    upload = awaiting()
    if check == "total":
        StagingRow.objects.filter(row_number=2).update(amount=Decimal("1000.001"))
    else:
        Upload.objects.filter(pk=upload.pk).update(rows_new=3)
    with pytest.raises(CommitCheckFailed, match="but the preview shows"):
        commit_upload(upload, make_user())
    assert nothing_committed(upload)


def fail(*args, **kwargs) -> None:
    """Stand in for audit.record, to make a step of the commit fail."""
    raise RuntimeError("the audit log is down")


def test_a_failure_part_way_writes_nothing(monkeypatch) -> None:
    """Verify the commit is one transaction: when writing its event fails, no
    posting, approval or duplicate mode is left, and the staging rows stay
    (ENG-02 10.1).
    """
    upload = awaiting()
    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError):
        commit_upload(upload, make_user())
    assert nothing_committed(upload)


def test_ids_app_can_commit() -> None:
    """Verify every write the commit makes is allowed for ids_app: locking the
    report, inserting postings, updating the upload and report, writing the
    event and deleting staging rows (ENG-01 6.3).
    """
    upload = awaiting()
    with as_app():
        commit_upload(upload, make_user())
    upload.refresh_from_db()
    assert upload.status == Status.COMMITTED
    assert Posting.objects.count() == 2
    assert not StagingRow.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_a_commit_that_waited_for_the_lock_is_timed_after_it() -> None:
    """Verify the commit locks the report's row and takes decided_at after the
    lock (ENG-02 10.2). Request A holds the lock; the commit waits, and its
    time is later than the moment A let go.

    Each thread has its own database connection, so this test commits for real
    and Django empties the tables after it.
    """
    upload = awaiting()
    locked, release = threading.Event(), threading.Event()
    outcome = {}

    def request_a() -> None:
        try:
            with transaction.atomic():
                Report.objects.select_for_update().get(pk=upload.report_id)
                locked.set()
                release.wait(timeout=10)
        finally:
            connection.close()

    def approve() -> None:
        try:
            outcome["upload"] = commit_upload(upload, upload.uploaded_by)
        except Exception as error:
            # Kept for the asserts below.
            outcome["error"] = error
        finally:
            connection.close()

    a = threading.Thread(target=request_a)
    a.start()
    assert locked.wait(timeout=10)
    b = threading.Thread(target=approve)
    b.start()
    b.join(timeout=1)
    waited = b.is_alive()
    released_at = timezone.now()
    release.set()
    a.join(timeout=10)
    b.join(timeout=10)
    assert waited, "the commit didn't wait for request A"
    assert "error" not in outcome, outcome.get("error")
    assert outcome["upload"].decided_at >= released_at
