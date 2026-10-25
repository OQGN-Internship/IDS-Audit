"""Picking columns for an upload stopped at needs_columns, and the choice
saved for the report's next file with the same header (ENG-02 4.5 and 6).
"""

import pytest

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import commit_upload
from ids_audit.imports.decisions import reject_upload
from ids_audit.imports.models import StagingRow, Upload
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import pick_columns, stage_upload
from ids_audit.imports.states import WrongState
from ids_audit.reports.models import Report
from tests.factories import make_report, make_upload, make_user
from tests.files import HEADER, LINE, csv_file, line, total_row, xlsx_file
from tests.roles import as_app

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status
Action = AuditEvent.Action

# The amount's header is one no name matches, so the file needs its column:
# column I.
BETRAG = (*HEADER[:8], "Betrag", HEADER[9])
FILE = csv_file(LINE, line(DocumentNo="2"), total_row("2000.000"), header=BETRAG)
LATER = csv_file(line(DocumentNo="3"), total_row("1000.000"), header=BETRAG)
# Without the posting date as well: column E.
TWO_MISSING = (*HEADER[:4], "Buchung", *HEADER[5:8], "Betrag", HEADER[9])


def staged(data: bytes, report: Report | None = None) -> Upload:
    """Receive and stage a file, in a new report unless given one."""
    report = report or make_report()
    upload = receive_file(report, report.created_by, "spend.csv", data)
    upload = stage_upload(upload, upload.uploaded_by)
    upload.refresh_from_db()
    return upload


def events(upload: Upload) -> list[str]:
    """Return the upload's audit actions in order."""
    return list(
        AuditEvent.objects.filter(upload=upload)
        .order_by("pk")
        .values_list("action", flat=True)
    )


def test_a_stopped_upload_keeps_its_problems_and_header() -> None:
    """Verify the needs_columns event keeps each problem and the header of
    each sheet with one, which a pick is saved against.
    """
    upload = staged(FILE)
    assert upload.status == Status.NEEDS_COLUMNS
    event = AuditEvent.objects.get(action=Action.UPLOAD_NEEDS_COLUMNS)
    assert event.details == {
        "problems": [{"sheet": "", "field": "amount", "columns": []}],
        "headers": {"": list(BETRAG)},
    }


def test_picking_the_column_reads_the_file_to_awaiting_approval() -> None:
    """Verify the pick moves the upload back to reading and reads it again,
    in one go: it awaits approval with its rows, its layout keeps the pick,
    and both moves are on record.
    """
    upload = staged(FILE)
    user = make_user()
    pick_columns(upload, user, {"": {"amount": "I"}})
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert (upload.rows_new, upload.reconciliation_difference) == (2, 0)
    assert StagingRow.objects.filter(upload=upload).count() == 2
    layout = upload.column_layout[""]
    assert (layout["columns"]["amount"], layout["picked"]) == ("I", {"amount": "I"})
    assert events(upload) == [
        "upload_received",
        "upload_needs_columns",
        "upload_columns_picked",
        "upload_awaiting_approval",
    ]
    picked = AuditEvent.objects.get(action=Action.UPLOAD_COLUMNS_PICKED)
    assert (picked.user, picked.details) == (user, {"columns": {"": {"amount": "I"}}})


def test_a_pick_that_leaves_a_problem_stops_the_upload_again() -> None:
    """Verify picking the amount but not the posting date stops the upload at
    needs_columns again, with the problem left.
    """
    data = csv_file(LINE, total_row("1000.000"), header=TWO_MISSING)
    upload = staged(data)
    pick_columns(upload, make_user(), {"": {"amount": "I"}})
    upload.refresh_from_db()
    assert upload.status == Status.NEEDS_COLUMNS
    assert events(upload)[-2:] == ["upload_columns_picked", "upload_needs_columns"]
    last = AuditEvent.objects.filter(action=Action.UPLOAD_NEEDS_COLUMNS).latest("pk")
    assert last.details["problems"] == [
        {"sheet": "", "field": "posting_date", "columns": []}
    ]
    # Only the field left needs a column: the amount picked before is kept.
    pick_columns(upload, make_user(), {"": {"posting_date": "E"}})
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    last = AuditEvent.objects.filter(action=Action.UPLOAD_COLUMNS_PICKED).latest("pk")
    assert last.details == {"columns": {"": {"amount": "I", "posting_date": "E"}}}
    assert upload.column_layout[""]["picked"] == {"amount": "I", "posting_date": "E"}


def test_a_sheet_picked_before_keeps_its_pick_when_another_is_picked() -> None:
    """Verify a workbook needing the spend sheet's amount and the GL mapping's
    commitment item, picked one at a time, keeps the first sheet's pick when
    only the second sheet still has a problem.
    """
    data = xlsx_file(
        {
            "Spend": [BETRAG, LINE, total_row("1000.000")],
            "GL": [
                ("G/L Account", "G/L Account Name", "Budget Posten"),
                ("610100", "Software", "2101"),
            ],
        }
    )
    upload = staged(data)
    pick_columns(upload, make_user(), {"Spend": {"amount": "I"}})
    upload.refresh_from_db()
    assert upload.status == Status.NEEDS_COLUMNS
    pick_columns(upload, make_user(), {"GL": {"ci_code": "C"}})
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert upload.column_layout["Spend"]["picked"] == {"amount": "I"}
    assert upload.column_layout["GL"]["picked"] == {"ci_code": "C"}


@pytest.mark.parametrize(
    "picks",
    [
        pytest.param({}, id="nothing"),
        pytest.param({"": {}}, id="no field"),
        pytest.param({"": {"posting_date": "E"}}, id="a field with no problem"),
        pytest.param({"Spend": {"amount": "I"}}, id="another sheet"),
        pytest.param({"": {"amount": "i"}}, id="not a column"),
    ],
)
def test_a_pick_that_isn_t_for_the_problem_changes_nothing(picks) -> None:
    """Verify a pick for anything but the upload's problems is a ValueError,
    and the upload stays at needs_columns with no new event.
    """
    upload = staged(FILE)
    with pytest.raises(ValueError):
        pick_columns(upload, make_user(), picks)
    upload.refresh_from_db()
    assert upload.status == Status.NEEDS_COLUMNS
    assert events(upload) == ["upload_received", "upload_needs_columns"]


def test_a_column_past_the_end_of_the_rows_reads_as_empty() -> None:
    """Verify a picked column no row reaches gives an empty amount, so each
    row is skipped as Unreadable amount rather than the read failing.
    """
    upload = staged(FILE)
    pick_columns(upload, make_user(), {"": {"amount": "Z"}})
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert (upload.rows_new, upload.rows_skipped) == (0, 2)


@pytest.mark.parametrize(
    "status", [Status.READING, Status.AWAITING_APPROVAL, Status.COMMITTED]
)
def test_only_an_upload_needing_columns_takes_a_pick(status) -> None:
    """Verify every other state is refused with WrongState."""
    with pytest.raises(WrongState):
        pick_columns(make_upload(status=status), make_user(), {"": {"amount": "I"}})


# The choice saved for the report (ENG-02 4.5)


def picked_and_staged(report: Report) -> Upload:
    """Stage FILE in the report and pick its amount column."""
    upload = staged(FILE, report)
    return pick_columns(upload, make_user(), {"": {"amount": "I"}})


def test_a_committed_pick_matches_the_next_file_with_the_same_header() -> None:
    """Verify, once the upload whose column was picked is committed, a later
    file with the same header goes straight to awaiting approval, with the
    pick in its layout.
    """
    report = make_report()
    commit_upload(picked_and_staged(report), make_user())
    later = staged(LATER, report)
    assert later.status == Status.AWAITING_APPROVAL
    assert later.column_layout[""]["picked"] == {"amount": "I"}


def test_a_pick_awaiting_approval_or_rejected_isn_t_reused() -> None:
    """Verify only committed picks are reused: a later file in another report
    with the upload awaiting approval, and the next file after it's rejected,
    each stop at needs_columns.
    """
    report = make_report()
    upload = picked_and_staged(report)
    assert staged(LATER, make_report()).status == Status.NEEDS_COLUMNS
    reject_upload(upload, make_user(), "The amount was the wrong column")
    assert staged(LATER, report).status == Status.NEEDS_COLUMNS


def test_a_header_one_cell_different_isn_t_matched() -> None:
    """Verify the header must be exactly the same, cell for cell."""
    report = make_report()
    commit_upload(picked_and_staged(report), make_user())
    other = (*BETRAG[:9], "Currency")
    different = csv_file(line(DocumentNo="3"), total_row("1000.000"), header=other)
    assert staged(different, report).status == Status.NEEDS_COLUMNS


def test_picking_works_as_ids_app() -> None:
    """Verify ids_app has every right a pick needs, reading the audit log
    included.
    """
    upload = staged(FILE)
    with as_app():
        pick_columns(upload, make_user(), {"": {"amount": "I"}})
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
