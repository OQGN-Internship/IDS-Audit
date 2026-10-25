"""Stage: reading an upload's stored file and saving what reading found
(ENG-01 4 and 5.1, ENG-02 2, 6, 7.3, 9 and 11.1).
"""

import datetime
import hashlib
import threading
from decimal import Decimal

import pytest
from django.db import connection, transaction
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ids_audit import audit
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import SkippedRow, StagingRow, Upload
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.imports.states import WrongState
from ids_audit.imports.storage import store_file
from ids_audit.ledger.models import CcDeptMap, GlCiMap
from ids_audit.pipeline.results import (
    ANOTHER_CURRENCY,
    MORE_THAN_3_DECIMALS,
    UNREADABLE_CODE,
)
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports.models import Report, Ruleset
from tests.factories import add_posting, commit, make_report, make_upload, roll_back
from tests.files import HEADER, LINE, csv_file, line, total_row, xlsx_file
from tests.roles import as_app

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status

# Two lines and a total that reconciles: 1000.000 and a 250.500 credit.
ARABIC = "تجديد ترخيص"
CREDIT = line(
    DocumentNo="5100000102",
    Pstng_Date="20.08.2025",
    Cost_Ctr="0000003100",
    Amount_in_LC="250.500-",
)
TWO_LINES = csv_file(line(Text=ARABIC), CREDIT, total_row("749.500"))

GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")
CC = ("Cost Center", "Cost Center Name", "Department Code", "Department Name")
# A workbook with both mapping sheets, and a cost center that can't be read.
WITH_MAPPINGS = xlsx_file(
    {
        "Spend": [HEADER, LINE, total_row("1000.000")],
        "GL": [
            GL,
            ("610100", "Software licences", "2101", "Software and licences"),
            ("650200", "Conferences", "", ""),
        ],
        "CC": [
            CC,
            ("4520", "Enterprise applications", "APP", "Applications"),
            ("#N/A", "Unknown", "APP", ""),
        ],
    }
)


def received(data: bytes, report: Report | None = None) -> Upload:
    """Receive a file as a new upload at reading, in a new report unless
    given one.
    """
    report = report or make_report()
    return receive_file(report, report.created_by, "spend.csv", data)


def staged(data: bytes, report: Report | None = None) -> Upload:
    """Receive a file and stage it, as its uploader."""
    upload = received(data, report)
    return stage_upload(upload, upload.uploaded_by)


def spend_fields(row: StagingRow) -> dict:
    """Every field of a staging row but its id, by column."""
    return {
        field.attname: getattr(row, field.attname)
        for field in StagingRow._meta.concrete_fields
        if not field.primary_key
    }


def nothing_saved(upload: Upload) -> bool:
    """Return True when no row and none of reading's figures were saved."""
    upload.refresh_from_db()
    figures = (
        upload.column_layout,
        upload.period_start,
        upload.period_end,
        upload.control_total,
        upload.rows_read,
        upload.rows_new,
        upload.rows_duplicate,
        upload.rows_skipped,
        upload.rows_replaced,
        upload.preview,
        upload.reconciliation_difference,
    )
    rows = (StagingRow, SkippedRow, GlCiMap, CcDeptMap)
    return (
        all(figure is None for figure in figures)
        and upload.control_total_source == ""
        and not any(model.objects.exists() for model in rows)
    )


# A file read in full


def test_each_clean_row_is_staged_field_by_field() -> None:
    """Verify every clean row becomes one staging row with every field as
    cleaned, Arabic text and credits included, not marked a duplicate.
    """
    upload = staged(TWO_LINES)
    first, second = StagingRow.objects.order_by("row_number")
    assert spend_fields(first) == {
        "report_id": upload.report_id,
        "upload_id": upload.pk,
        "sheet": "",
        "row_number": 2,
        "company_code": "1000",
        "fiscal_year": 2025,
        "document_number": "5100000101",
        "line_item": "2",
        "line_key": "1000-2025-5100000101-2",
        "posting_date": datetime.date(2025, 7, 8),
        "gl_code": "610100",
        "cost_center_code": "4520",
        "value_type": "",
        "is_actual": True,
        "amount": Decimal("1000.000"),
        "currency": "OMR",
        "item_text": ARABIC,
        "is_duplicate": False,
    }
    assert (second.row_number, second.cost_center_code) == (3, "3100")
    assert str(second.amount) == "-250.500"


def test_the_upload_keeps_reading_s_figures() -> None:
    """Verify the upload moves to awaiting_approval with its column layout,
    period, control total from the file, row figures, their amounts and a
    difference of 0.000 (ENG-02 9 and 11.1).
    """
    upload = staged(TWO_LINES)
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert upload.column_layout == {
        "": {
            "header_row": 1,
            "columns": {
                "company_code": "A",
                "gl_code": "B",
                "document_number": "C",
                "line_item": "D",
                "posting_date": "E",
                "fiscal_year": "F",
                "cost_center_code": "G",
                "item_text": "H",
                "amount": "I",
                "currency": "J",
            },
        }
    }
    assert (upload.period_start, upload.period_end) == (
        datetime.date(2025, 7, 8),
        datetime.date(2025, 8, 20),
    )
    assert (upload.control_total, upload.control_total_source) == (
        Decimal("749.500"),
        Upload.ControlTotalSource.FILE,
    )
    figures = [getattr(upload, name) for name in ("rows_read", "rows_new")]
    figures += [upload.rows_duplicate, upload.rows_skipped, upload.rows_replaced]
    assert figures == [2, 2, 0, 0, 0]
    assert upload.preview == {
        "amounts": {
            "to_commit": "749.500",
            "duplicates": "0.000",
            "skipped": "0.000",
            "replaced": "0.000",
        }
    }
    assert str(upload.reconciliation_difference) == "0.000"
    assert upload.error == ""


def test_reading_writes_one_awaiting_approval_event() -> None:
    """Verify the move writes upload_awaiting_approval for the user who started
    the read, keeping the row figures and the difference (ENG-01 6.3).
    """
    upload = staged(TWO_LINES)
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_AWAITING_APPROVAL)
    assert (event.user, event.report, event.upload) == (
        upload.uploaded_by,
        upload.report,
        upload,
    )
    assert event.details == {
        "rows_read": 2,
        "rows_new": 2,
        "rows_duplicate": 0,
        "rows_skipped": 0,
        "rows_replaced": 0,
        "reconciliation_difference": "0.000",
    }
    assert AuditEvent.objects.count() == 2  # upload_received, then this one


def test_skipped_rows_are_kept_with_their_cells_and_exact_amount() -> None:
    """Verify each skipped spend row is saved with its reason, its amount
    exactly as read and its cells as text, counts in rows_skipped, and only
    the ones in OMR count in the skipped amount (ENG-02 2, 9.2 and 11.1).
    """
    rows = [
        LINE,
        line(DocumentNo="5100000102", Text=" ERP ", Amount_in_LC="12.3456"),
        line(DocumentNo="5100000103", Amount_in_LC="50.000", LCurr="USD"),
    ]
    upload = staged(csv_file(*rows, total_row("1012.3456")))
    upload.refresh_from_db()
    four_decimals, usd = SkippedRow.objects.order_by("row_number")
    assert (four_decimals.upload, four_decimals.sheet) == (upload, "")
    assert (four_decimals.row_number, four_decimals.reason) == (3, MORE_THAN_3_DECIMALS)
    assert str(four_decimals.amount) == "12.3456"
    assert four_decimals.raw_values == list(rows[1])
    assert (usd.row_number, usd.reason, usd.amount) == (
        4,
        ANOTHER_CURRENCY,
        Decimal("50.000"),
    )
    assert (upload.rows_read, upload.rows_new, upload.rows_skipped) == (3, 1, 2)
    assert upload.preview == {
        "amounts": {
            "to_commit": "1000.000",
            "duplicates": "0.000",
            "skipped": "12.3456",
            "replaced": "0.000",
        }
    }
    assert str(upload.reconciliation_difference) == "0.000"


def test_mapping_rows_are_saved_and_a_skipped_one_isn_t_a_spend_row() -> None:
    """Verify both mappings are saved for the upload and report, a GL account
    listed without a commitment item included, and a skipped mapping row is
    saved but counts in none of the row figures (ENG-02 5 and 11.1).
    """
    upload = staged(WITH_MAPPINGS)
    upload.refresh_from_db()
    gl_rows = GlCiMap.objects.order_by("gl_code").values_list(
        "upload", "report", "gl_code", "gl_name", "ci_code", "ci_name"
    )
    assert list(gl_rows) == [
        (
            upload.pk,
            upload.report_id,
            "610100",
            "Software licences",
            "2101",
            "Software and licences",
        ),
        (upload.pk, upload.report_id, "650200", "Conferences", "", ""),
    ]
    cc_rows = CcDeptMap.objects.values_list(
        "upload", "report", "cc_code", "cc_name", "dept_code", "dept_name"
    )
    assert list(cc_rows) == [
        (
            upload.pk,
            upload.report_id,
            "4520",
            "Enterprise applications",
            "APP",
            "Applications",
        )
    ]
    skipped = SkippedRow.objects.get()
    assert (skipped.sheet, skipped.row_number, skipped.reason) == (
        "CC",
        3,
        UNREADABLE_CODE,
    )
    assert (skipped.amount, skipped.raw_values) == (None, ["#N/A", "Unknown", "APP"])
    assert (upload.rows_read, upload.rows_new, upload.rows_skipped) == (1, 1, 0)


def add_version(report: Report, version: int, actual_spend_rule) -> None:
    """Give the report a ruleset version with this actual-spend rule."""
    Ruleset.objects.create(
        report=report,
        version=version,
        settings={**DEFAULT_SETTINGS, "actual_spend_rule": actual_spend_rule},
        created_by=report.created_by,
    )


def test_rows_are_read_with_the_upload_s_ruleset_version() -> None:
    """Verify the upload is read with the settings of the ruleset version it
    was received under, not one made after it, and an unclassified value type
    is staged as not actual spend (ENG-02 3 and 5).
    """
    report = make_report()
    add_version(report, 2, {"actual": ["Actual"], "not_actual": ["Commitment"]})
    rows = [
        (*line(DocumentNo=f"510000010{n}"), value_type)
        for n, value_type in enumerate(["Actual", "Commitment", "Plan"])
    ]
    upload = received(csv_file(*rows, header=(*HEADER, "Value Type")), report)
    # Made after the upload was received, so it isn't the upload's version.
    add_version(report, 3, None)
    stage_upload(upload, upload.uploaded_by)
    staged_rows = StagingRow.objects.order_by("row_number")
    assert [(r.value_type, r.is_actual) for r in staged_rows] == [
        ("Actual", True),
        ("Commitment", False),
        ("Plan", False),
    ]


def test_a_file_with_no_total_row_has_no_control_total_or_difference() -> None:
    """Verify a file with no total row is staged with no control total, no
    source and no difference, which stays empty until one is entered (9.2).
    """
    upload = staged(csv_file(LINE))
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert (upload.control_total, upload.control_total_source) == (None, "")
    assert upload.reconciliation_difference is None
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_AWAITING_APPROVAL)
    assert event.details["reconciliation_difference"] is None


@pytest.mark.parametrize(
    ("written", "difference"), [("1000.001", "0.001"), ("999.999", "-0.001")]
)
def test_a_difference_is_saved_as_worked_out(written, difference) -> None:
    """Verify a total that's off by one baisa is saved as a difference of
    that baisa, in the upload and in its event (ENG-02 9.2).
    """
    upload = staged(csv_file(LINE, total_row(written)))
    upload.refresh_from_db()
    assert str(upload.reconciliation_difference) == difference
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_AWAITING_APPROVAL)
    assert event.details["reconciliation_difference"] == difference


# The postings a file replaces (ENG-02 7.3)


@pytest.mark.parametrize(
    ("mode", "count", "amount"),
    [
        pytest.param(Report.DuplicateMode.REPLACE_BY_PERIOD, 2, "110.000", id="period"),
        pytest.param(Report.DuplicateMode.LINE_KEY, 0, "0.000", id="line key"),
    ],
)
def test_the_postings_the_file_replaces_are_counted(mode, count, amount) -> None:
    """Verify a file dated in July and August 2025 replaces the postings that
    count from 1 July to 31 August, and none in line-key mode. A rolled-back
    upload's postings don't count, so they're not replaced.
    """
    report = make_report()
    first = make_upload(report)
    for day, posted in [
        (datetime.date(2025, 6, 30), "1.000"),
        (datetime.date(2025, 7, 1), "10.000"),
        (datetime.date(2025, 8, 31), "100.000"),
        (datetime.date(2025, 9, 1), "1000.000"),
    ]:
        add_posting(first, day, posted)
    commit(first, minute=1, mode=mode)
    second = make_upload(report)
    add_posting(second, datetime.date(2025, 7, 15), "5000.000")
    roll_back(commit(second, minute=2))

    upload = staged(TWO_LINES, report)
    upload.refresh_from_db()
    assert upload.rows_replaced == count
    assert upload.preview["amounts"]["replaced"] == amount


def test_a_file_ending_in_december_replaces_up_to_31_december() -> None:
    """Verify the file's last month runs to 31 December, and January of the
    next year isn't replaced.
    """
    report = make_report()
    first = make_upload(report)
    add_posting(first, datetime.date(2025, 12, 31), "7.000")
    add_posting(first, datetime.date(2026, 1, 1), "9.000")
    commit(first, minute=1)
    upload = staged(csv_file(line(Pstng_Date="15.12.2025")), report)
    upload.refresh_from_db()
    assert (upload.rows_replaced, upload.preview["amounts"]["replaced"]) == (
        1,
        "7.000",
    )


# Stopping at needs_columns or failed


@pytest.mark.parametrize(
    ("header", "problem"),
    [
        pytest.param(
            tuple(name if name != "Amount in LC" else "Note" for name in HEADER),
            {"sheet": "", "field": "amount", "columns": []},
            id="missing",
        ),
        pytest.param(
            tuple(name if name != "LCurr" else "Amount" for name in HEADER),
            {"sheet": "", "field": "amount", "columns": ["I", "J"]},
            id="two columns",
        ),
    ],
)
def test_a_column_problem_stops_at_needs_columns(header, problem) -> None:
    """Verify a missing required field, or a field two columns match, moves
    the upload to needs_columns with nothing else saved, and its event keeps
    the problems (ENG-02 4.5 and 6, ENG-01 6.3).
    """
    upload = staged(csv_file(LINE, header=header))
    assert upload.status == Status.NEEDS_COLUMNS
    assert nothing_saved(upload)
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_NEEDS_COLUMNS)
    assert (event.user, event.upload) == (upload.uploaded_by, upload)
    assert event.details == {"problems": [problem]}


@pytest.mark.parametrize(
    ("data", "error"),
    [
        pytest.param(
            b"Name;Value\r\nx;1\r\n",
            "The file has no spend sheet",
            id="no spend sheet",
        ),
        pytest.param(b"PK\x03\x04 not a workbook", "couldn't be opened", id="damaged"),
    ],
)
def test_a_file_that_can_t_be_read_fails_with_its_error(data, error) -> None:
    """Verify a file that can't be read moves to failed with its error, saving
    nothing else, and its event keeps the error (ENG-02 6, ENG-01 6.3).
    """
    assert_failed_with(staged(data), error)


def test_a_stored_file_of_unknown_format_fails() -> None:
    """Verify an upload whose stored file isn't an accepted format fails with
    the reason. receive_file refuses such a file, so the upload is made by hand,
    as one received before it did would be.
    """
    data = b"%PDF-1.4\n"
    file_sha256 = hashlib.sha256(data).hexdigest()
    store_file(file_sha256, data)
    upload = make_upload()
    Upload.objects.filter(pk=upload.pk).update(file_sha256=file_sha256)
    assert_failed_with(
        stage_upload(upload, upload.uploaded_by), "HTML, XML, MHTML or PDF"
    )


def assert_failed_with(upload: Upload, error: str) -> None:
    """Check the upload failed with the error, saving nothing else, and its
    one upload_failed event keeps the error and its user.
    """
    assert upload.status == Status.FAILED
    upload.refresh_from_db()
    assert error in upload.error
    assert nothing_saved(upload)
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_FAILED)
    assert (event.user, event.upload, event.details) == (
        upload.uploaded_by,
        upload,
        {"error": upload.error},
    )


# Refusals and the one transaction


@pytest.mark.parametrize("status", [s for s in Status.values if s != Status.READING])
def test_an_upload_not_at_reading_is_refused(status) -> None:
    """Verify reading an upload in any other state is refused before its file
    is read, and changes nothing (ENG-02 6).
    """
    upload = received(TWO_LINES)
    Upload.objects.filter(pk=upload.pk).update(status=status)
    events = AuditEvent.objects.count()
    with pytest.raises(WrongState, match="Upload 1 is .*, not reading"):
        stage_upload(upload, upload.uploaded_by)
    upload.refresh_from_db()
    assert upload.status == status
    assert nothing_saved(upload)
    assert AuditEvent.objects.count() == events


@pytest.mark.django_db(transaction=True)
def test_a_second_read_waits_for_the_first_and_is_refused() -> None:
    """Verify reading locks the upload's row. Read A holds the lock and moves
    the upload on, so a second read waits, then finds it no longer at reading
    and is refused. Without the lock it would see reading and stage every row
    a second time.

    Each thread has its own database connection, so this test commits for real
    and Django empties the tables after it.
    """
    upload = received(TWO_LINES)
    locked, release = threading.Event(), threading.Event()
    outcome = {}

    def read_a() -> None:
        try:
            with transaction.atomic():
                Upload.objects.select_for_update().get(pk=upload.pk)
                Upload.objects.filter(pk=upload.pk).update(
                    status=Status.AWAITING_APPROVAL
                )
                locked.set()
                release.wait(timeout=10)
        finally:
            connection.close()

    def read_b() -> None:
        try:
            stage_upload(upload, upload.uploaded_by)
        except Exception as error:
            # Kept for the asserts below.
            outcome["error"] = error
        finally:
            connection.close()

    a = threading.Thread(target=read_a)
    a.start()
    assert locked.wait(timeout=10)
    b = threading.Thread(target=read_b)
    b.start()
    b.join(timeout=1)
    waited = b.is_alive()
    release.set()
    a.join(timeout=10)
    b.join(timeout=10)
    assert waited, "the second read didn't wait for read A"
    assert isinstance(outcome.get("error"), WrongState), outcome
    assert not StagingRow.objects.exists()


def fail(*args, **kwargs) -> None:
    """Stand in for audit.record, to make the last step of reading fail."""
    raise RuntimeError("the audit log is down")


def test_a_failure_part_way_leaves_nothing_staged(monkeypatch) -> None:
    """Verify reading runs in one transaction: when its last step fails, no
    row and no figure is saved and the upload stays at reading (ENG-02 6).
    """
    upload = received(WITH_MAPPINGS)
    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError):
        stage_upload(upload, upload.uploaded_by)
    assert nothing_saved(upload)
    assert upload.status == Status.READING


def test_ids_app_can_stage_a_file() -> None:
    """Verify every write reading makes is allowed for ids_app: the COPY into
    staging_row, the insert-only skipped and mapping rows, the upload and the
    audit event (ENG-01 6.3).
    """
    upload = received(WITH_MAPPINGS)
    with as_app():
        stage_upload(upload, upload.uploaded_by)
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert StagingRow.objects.count() == 1
    assert (GlCiMap.objects.count(), CcDeptMap.objects.count()) == (2, 1)
    assert SkippedRow.objects.count() == 1


# The whole-file property (ENG-02 12.1)

GL_CODES = ["0000610100", "620200", "#N/A"]
DATES = ["08.07.2025", "15.01.2026", "31.02.2026"]


def sap_amount(amount: Decimal) -> str:
    """Write an amount as SAP does, with commas between thousands and a
    trailing minus.
    """
    text = format(abs(amount), ",f")
    return f"{text}-" if amount < 0 else text


@st.composite
def spend_files(draw) -> tuple[bytes, list[int]]:
    """Return a random file in OMR with readable amounts, and its spend rows.

    Spend rows are mixed with subtotals and blank rows. Some are skipped for an
    unreadable code, an invalid date, 4 decimals or a repeated document, which
    repeats the line key. The total row holds the exact sum of the spend rows.
    """
    rows, spend_rows, amounts = [], [], []
    for _ in range(draw(st.integers(0, 20))):
        kind = draw(st.sampled_from(["spend", "spend", "spend", "subtotal", "blank"]))
        if kind == "blank":
            rows.append(("",) * len(HEADER))
            continue
        places = draw(st.sampled_from([0, 2, 3, 3, 4]))
        amount = draw(st.decimals(min_value=-(10**9), max_value=10**9, places=places))
        if kind == "subtotal":
            rows.append(("",) * 7 + ("Subtotal", sap_amount(amount), "OMR"))
            continue
        cells = {
            "G/L Acct": draw(st.sampled_from(GL_CODES)),
            "Cost Ctr": draw(st.sampled_from(["0000004510", "3100"])),
            "DocumentNo": f"51{draw(st.integers(1, 15)):08d}",
            "Pstng Date": draw(st.sampled_from(DATES)),
            "Amount in LC": sap_amount(amount),
        }
        rows.append(line(**cells))
        spend_rows.append(len(rows) + 1)  # the header is row 1
        amounts.append(amount)
    rows.append(total_row(sap_amount(sum(amounts, Decimal(0)))))
    return csv_file(*rows), spend_rows


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(file=spend_files())
def test_every_spend_row_is_staged_or_skipped_and_the_file_reconciles(file) -> None:
    """Verify every spend row of a random file ends up exactly once as a staging
    row or a skipped row, the row figures add up, and the file reconciles to
    0.000 (ENG-02 9 and 11.1).
    """
    data, spend_rows = file
    # Each example runs in a savepoint that's rolled back, so the next one
    # starts from an empty report.
    with transaction.atomic():
        upload = staged(data)
        upload.refresh_from_db()
        found = [
            *StagingRow.objects.values_list("row_number", flat=True),
            *SkippedRow.objects.values_list("row_number", flat=True),
        ]
        assert sorted(found) == spend_rows
        assert upload.rows_read == len(spend_rows)
        assert upload.rows_new + upload.rows_duplicate + upload.rows_skipped == len(
            spend_rows
        )
        assert str(upload.reconciliation_difference) == "0.000"
        transaction.set_rollback(True)
