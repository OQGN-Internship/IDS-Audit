"""Duplicates: lines already in the report, in line-key mode, and choosing the
mode on a report's first approval (ENG-02 5, 7.3 and 10.1).
"""

from decimal import Decimal

import pytest

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import ApprovalRefused, commit_upload
from ids_audit.imports.models import StagingRow, Upload
from ids_audit.imports.rollback import roll_back_upload
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.ledger.models import Posting, PostingResolved
from ids_audit.pipeline.duplicates import find_duplicates, line_key_offered
from ids_audit.pipeline.read import read_file
from ids_audit.pipeline.reconciliation import reconcile
from ids_audit.pipeline.results import (
    NO_LINE_KEY,
    UNREADABLE_AMOUNT,
    repeats_row,
)
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from tests.factories import make_report, roll_back
from tests.files import LINE, csv_file, line, total_row

Status = Upload.Status
LINE_KEY = Report.DuplicateMode.LINE_KEY
REPLACE_BY_PERIOD = Report.DuplicateMode.REPLACE_BY_PERIOD

# The tests that read and write the database, with a folder for files.
on_database = pytest.mark.usefixtures("db", "uploads_dir")

# Document 5100000102's line, in the first file and again in the second, with
# another amount.
SECOND = line(DocumentNo="5100000102", Amount_in_LC="200.000")
SECOND_CHANGED = line(DocumentNo="5100000102", Amount_in_LC="250.000")
THIRD = line(DocumentNo="5100000103", Amount_in_LC="300.000")
NO_DOCUMENT = line(DocumentNo="", Amount_in_LC="40.000")

FIRST_FILE = csv_file(LINE, SECOND, total_row("1200.000"))
# LINE again with its amount, SECOND with another, and one new line.
OVERLAPPING = csv_file(LINE, SECOND_CHANGED, THIRD, total_row("1550.000"))


def read(data: bytes, need_line_key: bool):
    """Read a file with the default settings."""
    return read_file(data, DEFAULT_SETTINGS, need_line_key=need_line_key)


# Reading in line-key mode (ENG-02 5)


def test_a_row_without_a_full_line_key_is_skipped_in_line_key_mode() -> None:
    """Verify a row with no document number is skipped as No line key with its
    amount, counts in reconciliation, and the file still reconciles.
    """
    result = read(csv_file(LINE, NO_DOCUMENT, total_row("1040.000")), True)
    assert [row.row_number for row in result.clean_rows] == [2]
    (skipped,) = result.skipped_rows
    assert (skipped.row_number, skipped.reason) == (3, NO_LINE_KEY)
    assert (skipped.amount, skipped.in_reconciliation) == (Decimal("40.000"), True)
    assert reconcile(result).difference == Decimal("0.000")


def test_the_same_row_is_staged_outside_line_key_mode() -> None:
    """Verify replace by period accepts a row without a full line key."""
    result = read(csv_file(LINE, NO_DOCUMENT, total_row("1040.000")), False)
    assert [row.row_number for row in result.clean_rows] == [2, 3]
    assert result.skipped_rows == ()


def test_an_earlier_reason_wins_over_no_line_key() -> None:
    """Verify a row with an unreadable amount and no document number gets the
    reason that comes first in ENG-02 5's table.
    """
    no_amount = line(DocumentNo="", Amount_in_LC="12,34.5")
    result = read(csv_file(LINE, no_amount, total_row("1000.000")), True)
    assert [row.reason for row in result.skipped_rows] == [UNREADABLE_AMOUNT]


def test_a_repeated_line_key_is_still_skipped_as_a_repeat() -> None:
    """Verify a row with a full line key repeating an earlier one keeps that
    reason in line-key mode (ENG-02 7.2).
    """
    result = read(csv_file(LINE, LINE, total_row("2000.000")), True)
    assert [row.reason for row in result.skipped_rows] == [repeats_row(2)]


# The duplicate rules (ENG-02 7.3)


def test_line_key_mode_is_offered_only_when_every_row_has_a_full_key() -> None:
    """Verify the offer needs rows to commit, each with a full line key."""
    assert line_key_offered(read(FIRST_FILE, False).clean_rows)
    with_gap = csv_file(LINE, NO_DOCUMENT, total_row("1040.000"))
    assert not line_key_offered(read(with_gap, False).clean_rows)
    assert not line_key_offered(())


def test_a_row_is_a_duplicate_when_its_key_counts_already() -> None:
    """Verify each row whose key is a counted posting's is found by its place,
    with that posting's amount, and a row with no key never is.
    """
    rows = read(csv_file(LINE, THIRD, NO_DOCUMENT), False).clean_rows
    counted = {rows[0].line_key: Decimal("999.000"), "": Decimal("1.000")}
    assert find_duplicates(rows, counted) == {("", 2): Decimal("999.000")}


# Staging against the report (ENG-02 7.3, 9.2 and 11.1)


def received(data: bytes, report: Report) -> Upload:
    """Receive a file into the report, as its creator."""
    return receive_file(report, report.created_by, "spend.csv", data)


def staged(data: bytes, report: Report) -> Upload:
    """Receive and stage a file in the report, as its creator."""
    upload = received(data, report)
    upload = stage_upload(upload, upload.uploaded_by)
    upload.refresh_from_db()
    return upload


def committed(data: bytes, report: Report, mode: str | None = None) -> Upload:
    """Receive, stage and approve a file, choosing the mode if it's given."""
    upload = staged(data, report)
    return commit_upload(upload, upload.uploaded_by, duplicate_mode=mode)


def line_key_report() -> Report:
    """A report in line-key mode, with FIRST_FILE committed."""
    report = make_report()
    committed(FIRST_FILE, report, LINE_KEY)
    return Report.objects.get(pk=report.pk)


@on_database
def test_rows_already_in_the_report_are_marked_and_left_out_of_the_figures() -> None:
    """Verify the overlapping file's first two rows are duplicates, their
    amount is apart from the rows to commit, and the file still reconciles.
    """
    upload = staged(OVERLAPPING, line_key_report())
    marked = dict(
        StagingRow.objects.filter(upload=upload).values_list(
            "row_number", "is_duplicate"
        )
    )
    assert marked == {2: True, 3: True, 4: False}
    figures = (upload.rows_read, upload.rows_new, upload.rows_duplicate)
    assert figures == (3, 1, 2)
    assert upload.preview["amounts"]["to_commit"] == "300.000"
    assert upload.preview["amounts"]["duplicates"] == "1250.000"
    assert (upload.rows_replaced, upload.preview["amounts"]["replaced"]) == (
        0,
        "0.000",
    )
    assert upload.reconciliation_difference == Decimal("0.000")


@on_database
def test_approving_adds_only_the_new_lines() -> None:
    """Verify only the new line becomes a posting, the posting whose amount
    differs keeps its own, and the total grows by the new line only.
    """
    report = line_key_report()
    upload = staged(OVERLAPPING, report)
    commit_upload(upload, upload.uploaded_by)
    assert list(
        Posting.objects.filter(upload=upload).values_list("row_number", flat=True)
    ) == [4]
    second = PostingResolved.objects.get(report=report, document_number="5100000102")
    assert second.amount == Decimal("200.000")
    assert queries.totals(report).total == Decimal("1500.000")


@on_database
def test_a_rolled_back_posting_doesn_t_make_a_duplicate() -> None:
    """Verify only postings that count are matched (ENG-02 7.3)."""
    report = line_key_report()
    roll_back(Upload.objects.get(report=report, number=1))
    upload = staged(OVERLAPPING, report)
    assert (upload.rows_new, upload.rows_duplicate) == (3, 0)


@on_database
def test_replace_by_period_marks_no_duplicates() -> None:
    """Verify the same lines in a report in replace-by-period mode are staged
    to commit, and replace the postings in their months instead.
    """
    report = make_report()
    committed(FIRST_FILE, report)
    upload = staged(OVERLAPPING, report)
    assert (upload.rows_new, upload.rows_duplicate, upload.rows_replaced) == (3, 0, 2)
    assert not StagingRow.objects.filter(upload=upload, is_duplicate=True).exists()


@on_database
def test_a_row_without_a_line_key_is_skipped_in_a_line_key_report() -> None:
    """Verify a later file's row with no document number is skipped as No line
    key, and the file still reconciles.
    """
    upload = staged(
        csv_file(THIRD, NO_DOCUMENT, total_row("340.000")), line_key_report()
    )
    assert (upload.rows_read, upload.rows_new, upload.rows_skipped) == (2, 1, 1)
    assert upload.preview["amounts"]["skipped"] == "40.000"
    assert upload.reconciliation_difference == Decimal("0.000")


# Choosing the mode (ENG-02 7.3 and 10.1)


@on_database
def test_the_preview_offers_line_key_mode_on_a_first_upload_with_full_keys() -> None:
    """Verify the offer is saved in the preview: made for a first upload whose
    rows all have line keys, and not for one with a row lacking one, or for a
    report that already has a committed upload.
    """
    assert staged(FIRST_FILE, make_report()).preview["line_key_offered"] is True
    with_gap = csv_file(LINE, NO_DOCUMENT, total_row("1040.000"))
    assert staged(with_gap, make_report()).preview["line_key_offered"] is False
    report = make_report()
    committed(FIRST_FILE, report)
    later = staged(csv_file(THIRD, total_row("300.000")), report)
    assert later.preview["line_key_offered"] is False


@on_database
def test_the_first_approval_can_choose_line_key_mode() -> None:
    """Verify the chosen mode is set on the report and kept in the event."""
    report = make_report()
    upload = committed(FIRST_FILE, report, LINE_KEY)
    report.refresh_from_db()
    assert report.duplicate_mode == LINE_KEY
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_APPROVED)
    assert (event.upload, event.details["duplicate_mode"]) == (upload, "line_key")


@on_database
def test_line_key_mode_isn_t_chosen_for_a_file_not_offered_it() -> None:
    """Verify line-key mode is refused when a row has no line key, and nothing
    is committed.
    """
    report = make_report()
    upload = staged(csv_file(LINE, NO_DOCUMENT, total_row("1040.000")), report)
    with pytest.raises(ApprovalRefused, match="Line-key mode isn't offered"):
        commit_upload(upload, upload.uploaded_by, duplicate_mode=LINE_KEY)
    upload.refresh_from_db()
    report.refresh_from_db()
    assert (upload.status, report.duplicate_mode) == (Status.AWAITING_APPROVAL, "")
    assert not Posting.objects.exists()


@on_database
@pytest.mark.parametrize("mode", [LINE_KEY, REPLACE_BY_PERIOD])
def test_a_later_approval_can_t_choose_a_mode(mode) -> None:
    """Verify the mode is fixed by the first commit: choosing one later is
    refused, even the report's own, and nothing is committed.
    """
    report = make_report()
    committed(FIRST_FILE, report)
    upload = staged(csv_file(THIRD, total_row("300.000")), report)
    with pytest.raises(ApprovalRefused, match="first approval only"):
        commit_upload(upload, upload.uploaded_by, duplicate_mode=mode)
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert not Posting.objects.filter(upload=upload).exists()


@on_database
def test_a_rollback_leaves_the_mode_as_the_first_commit_set_it() -> None:
    """Verify rolling back a report's only commit keeps its mode, so the next
    file isn't offered line-key mode and can't choose it (ENG-02 7.3).
    """
    report = make_report()
    first = committed(FIRST_FILE, report)
    roll_back_upload(first, report.created_by, "Approved too soon")
    report.refresh_from_db()
    assert report.duplicate_mode == REPLACE_BY_PERIOD
    upload = staged(OVERLAPPING, report)
    assert upload.preview["line_key_offered"] is False
    with pytest.raises(ApprovalRefused):
        commit_upload(upload, upload.uploaded_by, duplicate_mode=LINE_KEY)
