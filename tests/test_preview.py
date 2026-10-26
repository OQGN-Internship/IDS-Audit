"""The preview's parts that depend on the report, worked out by approving the
upload for a moment (ENG-02 8.3, 9.1, 11.1 and 11.2). The parts that come from
the file alone are in test_preview_file.py.
"""

from decimal import Decimal

import pytest

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import commit_upload
from ids_audit.imports.models import Upload
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.ledger.models import Posting, PostingResolved
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports import queries
from ids_audit.reports.models import Report, Ruleset
from tests.factories import make_report
from tests.files import HEADER, LINE, csv_file, line, total_row, xlsx_file

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")
CC = ("Cost Center", "Cost Center Name", "Department Code", "Department Name")
RULE = {"actual": ["Actual"], "not_actual": ["Commitment"]}

# A first file: 610100 mapped, 640200 missing from the GL mapping and on 3100,
# outside IDS, and 650200 listed without a commitment item.
NO_BUDGET_LINE = line(
    **{"G/L Acct": "640200"},
    DocumentNo="2",
    Pstng_Date="10.08.2025",
    Cost_Ctr="3100",
    Amount_in_LC="300.000",
)
WITHOUT_CI = line(
    **{"G/L Acct": "650200"},
    DocumentNo="3",
    Pstng_Date="11.08.2025",
    Amount_in_LC="200.000",
)
FIRST_GL = [("610100", "Software", "2101", "Licences"), ("650200", "Travel", "", "")]
FIRST_CC = [("4520", "Enterprise applications", "APP", "Applications")]


def workbook(rows: list[tuple], gl=(), cc=()) -> bytes:
    """Return a workbook with a spend sheet of the rows, and each mapping
    sheet given.
    """
    sheets = {"Spend": [HEADER, *rows]}
    if gl:
        sheets["GL"] = [GL, *gl]
    if cc:
        sheets["CC"] = [CC, *cc]
    return xlsx_file(sheets)


FIRST = workbook(
    [LINE, NO_BUDGET_LINE, WITHOUT_CI, total_row("1500.000")], FIRST_GL, FIRST_CC
)
# A second file in September, so it replaces nothing.
SEPTEMBER = line(DocumentNo="9", Pstng_Date="05.09.2025")


def staged(data: bytes, report: Report, **received) -> Upload:
    """Receive and stage a file in the report, as its creator."""
    upload = receive_file(report, report.created_by, "spend.xlsx", data, **received)
    upload = stage_upload(upload, upload.uploaded_by)
    upload.refresh_from_db()
    return upload


def approve(upload: Upload) -> Upload:
    """Approve an upload as its uploader."""
    return commit_upload(upload, upload.uploaded_by)


def exceptions(upload: Upload) -> dict[str, dict]:
    """Return the upload's preview exceptions by type."""
    return {e["type"]: e for e in upload.preview["exceptions"]}


def blocking(upload: Upload) -> list[str]:
    """Return the types of the upload's exceptions that block approval."""
    return [t for t, e in exceptions(upload).items() if e["blocks_approval"]]


def with_rule(report: Report) -> Report:
    """Give the report a ruleset version 2 with an actual-spend rule."""
    Ruleset.objects.create(
        report=report,
        version=2,
        settings={**DEFAULT_SETTINGS, "actual_spend_rule": RULE},
        created_by=report.created_by,
    )
    return report


# A first upload


def test_a_first_upload_shows_what_it_adds_to_an_empty_report() -> None:
    """Verify the actual spend inside and outside IDS, the report before and
    after, the mappings used, and the codes with no budget line or outside
    IDS, each with its rows and amount.
    """
    upload = staged(FIRST, make_report())
    preview = upload.preview
    assert preview["committed_uploads"] == []
    assert preview["months_replaced"] == ["2025-07", "2025-08"]
    assert preview["actual_spend"] == {
        "total": "1500.000",
        "inside_ids": "1200.000",
        "outside_ids": "300.000",
    }
    assert (preview["not_actual"], preview["value_types"]) == ("0.000", [])
    assert preview["mappings"] == {"gl": 1, "cc": 1}
    assert preview["report_actual_spend"] == {"before": "0.000", "after": "1500.000"}
    found = exceptions(upload)
    assert list(found) == ["missing_from_gl_map", "listed_without_ci", "outside_ids"]
    assert found["missing_from_gl_map"] == {
        "type": "missing_from_gl_map",
        "blocks_approval": False,
        "amount": "300.000",
        "items": [{"rows": [["Spend", 3]], "amount": "300.000", "code": "640200"}],
    }
    assert found["listed_without_ci"]["items"] == [
        {"rows": [["Spend", 4]], "amount": "200.000", "code": "650200"}
    ]
    assert found["outside_ids"]["items"] == [
        {"rows": [["Spend", 3]], "amount": "300.000", "code": "3100"}
    ]


def test_working_out_the_preview_leaves_nothing_behind() -> None:
    """Verify approving for a moment writes nothing: no posting, the upload
    awaiting approval with no decision, and the report's mode still empty.
    """
    report = make_report()
    upload = staged(FIRST, report)
    assert not Posting.objects.exists()
    assert (upload.status, upload.decided_at) == (
        Upload.Status.AWAITING_APPROVAL,
        None,
    )
    report.refresh_from_db()
    assert report.duplicate_mode == ""
    assert not AuditEvent.objects.filter(action="upload_approved").exists()


def test_a_first_upload_with_no_mapping_sheets_names_no_mapping() -> None:
    """Verify a CSV in an empty report has no mapping to use yet."""
    upload = staged(csv_file(LINE, total_row("1000.000")), make_report())
    assert upload.preview["mappings"] == {"gl": None, "cc": None}


# A cost center listed without a department (ENG-02 8.2 and 11.2)

# 4570 is on the cost center mapping with an empty department code.
NO_DEPARTMENT_CC = [*FIRST_CC, ("4570", "Projects office", "", "")]
ON_4570 = line(
    DocumentNo="4", Pstng_Date="12.08.2025", Cost_Ctr="4570", Amount_in_LC="250.000"
)


def test_a_cost_center_listed_without_a_department_is_its_own_exception() -> None:
    """Verify its rows and actual spend are listed as their own type, inside
    IDS rather than outside it, and that it doesn't block approval.
    """
    data = workbook([LINE, ON_4570, total_row("1250.000")], FIRST_GL, NO_DEPARTMENT_CC)
    upload = staged(data, make_report())
    found = exceptions(upload)
    assert found["listed_without_department"] == {
        "type": "listed_without_department",
        "blocks_approval": False,
        "amount": "250.000",
        "items": [{"rows": [["Spend", 3]], "amount": "250.000", "code": "4570"}],
    }
    assert "outside_ids" not in found
    assert upload.preview["actual_spend"]["outside_ids"] == "0.000"
    assert approve(upload).status == Upload.Status.COMMITTED


def test_the_mapping_in_force_raises_it_for_a_file_without_one() -> None:
    """Verify a CSV, which has no mapping of its own, lists a cost center that
    the mapping in force gives no department, and that a department code
    leaves it out.
    """
    report = make_report()
    first = workbook([LINE, total_row("1000.000")], FIRST_GL, NO_DEPARTMENT_CC)
    approve(staged(first, report))
    later = csv_file(
        line(DocumentNo="8", Pstng_Date="05.09.2025"),
        line(DocumentNo="9", Pstng_Date="06.09.2025", Cost_Ctr="4570"),
        total_row("2000.000"),
    )
    upload = staged(later, report)
    assert exceptions(upload)["listed_without_department"]["items"] == [
        {"rows": [["", 3]], "amount": "1000.000", "code": "4570"}
    ]


# Report actual spend before and after approval (ENG-02 11.1)


def test_the_after_figure_is_the_report_s_actual_spend_once_approved() -> None:
    """Verify, for a file that replaces a month and has rows that aren't
    actual spend, the preview's after figure equals the report's actual spend
    once it's approved.
    """
    report = with_rule(make_report())
    header = (*HEADER, "Value Type")
    july = csv_file(
        (*LINE, "Actual"),
        (*line(DocumentNo="2", Amount_in_LC="50.000"), "Commitment"),
        (*total_row("1050.000"), ""),
        header=header,
    )
    approve(staged(july, report))
    later = csv_file(
        (*line(DocumentNo="3", Amount_in_LC="700.000"), "Actual"),
        (
            *line(DocumentNo="4", Pstng_Date="08.08.2025", Amount_in_LC="300.000"),
            "Actual",
        ),
        (
            *line(DocumentNo="5", Pstng_Date="09.08.2025", Amount_in_LC="20.000"),
            "Commitment",
        ),
        (*total_row("1020.000"), ""),
        header=header,
    )
    upload = staged(later, report)
    assert upload.rows_replaced == 2
    assert upload.preview["report_actual_spend"] == {
        "before": "1000.000",
        "after": "1000.000",
    }
    assert upload.preview["not_actual"] == "20.000"
    # No mapping yet, so all of it is outside IDS, and only actual spend counts.
    assert upload.preview["actual_spend"] == {
        "total": "1000.000",
        "inside_ids": "0.000",
        "outside_ids": "1000.000",
    }
    assert upload.preview["value_types"] == [
        {"value_type": "Actual", "rows": 2, "amount": "1000.000", "class": "actual"},
        {
            "value_type": "Commitment",
            "rows": 1,
            "amount": "20.000",
            "class": "not_actual",
        },
    ]
    approve(upload)
    assert queries.totals(report).total == Decimal("1000.000")


def test_the_after_figure_leaves_out_duplicates_in_line_key_mode() -> None:
    """Verify the after figure adds only the new lines in line-key mode."""
    report = make_report()
    first = staged(csv_file(LINE, total_row("1000.000")), report)
    commit_upload(first, first.uploaded_by, duplicate_mode="line_key")
    overlapping = csv_file(LINE, SEPTEMBER, total_row("2000.000"))
    upload = staged(overlapping, report)
    assert upload.preview["report_actual_spend"]["after"] == "2000.000"
    assert upload.preview["months_replaced"] == []
    approve(upload)
    assert queries.totals(report).total == Decimal("2000.000")


# Mapping changes (ENG-02 8.3)

# The second file's GL mapping moves 610100 to 2401, adds 620100 and leaves
# out 650200. Its cost center mapping moves 4520 to INF.
SECOND_GL = [("610100", "Software", "2401", "Services"), ("620100", "HW", "2201", "")]
SECOND_CC = [("4520", "Enterprise applications", "INF", "Infrastructure")]
SECOND = workbook([SEPTEMBER, total_row("1000.000")], SECOND_GL, SECOND_CC)


def test_each_mapping_change_shows_the_spend_it_moves() -> None:
    """Verify, under the latest rule, each code whose value changes, with the
    spend approving moves, the part already committed, and this file's rows.
    """
    report = make_report()
    approve(staged(FIRST, report))
    upload = staged(SECOND, report)
    change = exceptions(upload)["mapping_change"]
    assert change["blocks_approval"] is False
    assert change["items"] == [
        {
            "rows": [["Spend", 2]],
            "amount": "2000.000",
            "mapping": "gl",
            "code": "610100",
            "before": "2101",
            "after": "2401",
            "committed": "1000.000",
        },
        {
            "rows": [],
            "amount": "0.000",
            "mapping": "gl",
            "code": "620100",
            "before": None,
            "after": "2201",
            "committed": "0.000",
        },
        {
            "rows": [],
            "amount": "200.000",
            "mapping": "gl",
            "code": "650200",
            "before": "",
            "after": None,
            "committed": "200.000",
        },
        {
            "rows": [["Spend", 2]],
            "amount": "2200.000",
            "mapping": "cc",
            "code": "4520",
            "before": "APP",
            "after": "INF",
            "committed": "1200.000",
        },
    ]
    assert change["amount"] == "4400.000"
    assert upload.preview["mappings"] == {"gl": 2, "cc": 2}


def test_approving_moves_exactly_what_the_preview_said() -> None:
    """Verify, after approval, the committed 610100 posting reports under 2401
    and the 650200 ones have no budget line.
    """
    report = make_report()
    approve(staged(FIRST, report))
    approve(staged(SECOND, report))
    first = PostingResolved.objects.filter(report=report, upload__number=1)
    assert first.get(gl_code="610100").ci_code == "2401"
    assert first.get(gl_code="650200").ci_status == "missing_from_gl_map"


def test_under_own_upload_committed_postings_don_t_move() -> None:
    """Verify, under the own_upload rule, only this file's rows move."""
    report = make_report(mapping_rule="own_upload")
    approve(staged(FIRST, report))
    upload = staged(SECOND, report)
    items = exceptions(upload)["mapping_change"]["items"]
    moved = {(i["mapping"], i["code"]): (i["amount"], i["committed"]) for i in items}
    assert moved == {
        ("gl", "610100"): ("1000.000", "0.000"),
        ("gl", "620100"): ("0.000", "0.000"),
        ("gl", "650200"): ("0.000", "0.000"),
        ("cc", "4520"): ("1000.000", "0.000"),
    }


def test_a_file_without_mapping_sheets_lists_no_change_and_names_the_mapping() -> None:
    """Verify a CSV after the first file uses upload 1's mappings, and lists
    no mapping change (ENG-02 8.3).
    """
    report = make_report()
    approve(staged(FIRST, report))
    upload = staged(csv_file(SEPTEMBER, total_row("1000.000")), report)
    assert upload.preview["mappings"] == {"gl": 1, "cc": 1}
    assert "mapping_change" not in exceptions(upload)


def test_the_same_mapping_again_lists_no_change() -> None:
    """Verify a second file with the first's mappings changes nothing."""
    report = make_report()
    approve(staged(FIRST, report))
    again = workbook([SEPTEMBER, total_row("1000.000")], FIRST_GL, FIRST_CC)
    assert "mapping_change" not in exceptions(staged(again, report))


# Replaced postings outside the file's dates (ENG-02 11.2)


def test_replaced_postings_outside_the_file_s_dates_are_listed() -> None:
    """Verify a file dated 15 to 28 February, after one dated 3 and 20
    February, lists the 3 February posting it replaces, and not the one
    inside its dates.
    """
    report = make_report()
    february = csv_file(
        line(Pstng_Date="03.02.2026", Amount_in_LC="100.000"),
        line(DocumentNo="2", Pstng_Date="20.02.2026", Amount_in_LC="200.000"),
        total_row("300.000"),
    )
    approve(staged(february, report))
    second_half = csv_file(
        line(DocumentNo="3", Pstng_Date="15.02.2026", Amount_in_LC="50.000"),
        line(DocumentNo="4", Pstng_Date="28.02.2026", Amount_in_LC="60.000"),
        total_row("110.000"),
    )
    upload = staged(second_half, report)
    assert exceptions(upload)["replaces_outside_dates"] == {
        "type": "replaces_outside_dates",
        "blocks_approval": False,
        "amount": "100.000",
        "items": [{"rows": [], "amount": "100.000", "upload": 1, "postings": 1}],
    }


def test_line_key_mode_replaces_nothing_outside_the_dates() -> None:
    """Verify the exception never shows in line-key mode."""
    report = make_report()
    first = staged(csv_file(line(Pstng_Date="03.02.2026"), total_row("1000")), report)
    commit_upload(first, first.uploaded_by, duplicate_mode="line_key")
    upload = staged(
        csv_file(line(DocumentNo="2", Pstng_Date="20.02.2026"), total_row("1000")),
        report,
    )
    assert "replaces_outside_dates" not in exceptions(upload)


# The control total typed on the upload form (ENG-02 9.1)


def test_a_typed_total_is_used_for_a_file_with_no_total_row() -> None:
    """Verify the typed total is saved as entered, the file reconciles against
    it, and it's kept in upload_received.
    """
    upload = staged(csv_file(LINE), make_report(), control_total=Decimal("1000.000"))
    assert (upload.control_total, upload.control_total_source) == (
        Decimal("1000.000"),
        Upload.ControlTotalSource.ENTERED,
    )
    assert upload.reconciliation_difference == Decimal("0.000")
    assert blocking(upload) == []
    event = AuditEvent.objects.get(action="upload_received")
    assert event.details["control_total"] == "1000.000"


def test_a_typed_total_that_doesn_t_match_leaves_a_difference() -> None:
    """Verify a typed total short by 0.500 blocks approval."""
    upload = staged(csv_file(LINE), make_report(), control_total=Decimal("999.5"))
    assert exceptions(upload)["difference_not_zero"]["amount"] == "-0.500"


def test_a_file_with_no_total_row_and_none_typed_has_no_control_total() -> None:
    """Verify the blocking exception, and no total in upload_received."""
    upload = staged(csv_file(LINE), make_report())
    assert blocking(upload) == ["no_control_total"]
    event = AuditEvent.objects.get(action="upload_received")
    assert "control_total" not in event.details


def test_the_file_s_total_row_wins_over_a_typed_total() -> None:
    """Verify a typed total is ignored for a file with a total row, and kept
    only in upload_received.
    """
    data = csv_file(LINE, total_row("1000.000"))
    upload = staged(data, make_report(), control_total=Decimal("5.000"))
    assert (upload.control_total, upload.control_total_source) == (
        Decimal("1000.000"),
        Upload.ControlTotalSource.FILE,
    )
    assert blocking(upload) == []
    event = AuditEvent.objects.get(action="upload_received")
    assert event.details["control_total"] == "5.000"


@pytest.mark.parametrize("total", ["12.3456", "Infinity", "1000000000000000"])
def test_a_typed_total_that_isn_t_an_amount_is_refused(total) -> None:
    """Verify a typed total with more than 3 decimal places, or that isn't a
    number numeric(18,3) holds, creates no upload.
    """
    report = make_report()
    with pytest.raises(ValueError, match="control total"):
        receive_file(report, report.created_by, "a.csv", csv_file(LINE), Decimal(total))
    assert not Upload.objects.exists()
