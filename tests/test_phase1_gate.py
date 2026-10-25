"""The Phase 1 gate (ENG-01 4, Build order): the sample file through every
step, receive, stage, commit and add up, then its semicolon copy uploaded
after it, each compared with its expected result (ENG-02 12.2 and 12.3).

Every figure is reached through the imports/ functions and models and through
reports/queries.py, by way of manage.py report_totals, never by calling
pipeline/ directly. The other rows and the exception lists come from the
upload's preview (ENG-01 4, ENG-02 11.2).
"""

import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command

from ids_audit.imports.commit import commit_upload
from ids_audit.imports.models import SkippedRow, Upload
from ids_audit.imports.services import create_report, receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from tests.factories import make_user

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_XLSX = "sample.xlsx"
SAMPLE_CSV = "sample_semicolon.csv"


def expected(fixture: str) -> dict:
    """Return a fixture's expected result, without its file name and the
    upload it describes.
    """
    path = FIXTURES_DIR / f"{Path(fixture).stem}.expected.json"
    with open(path, encoding="utf-8") as file:
        result = json.load(file)
    for key in ("fixture", "scenario"):
        del result[key]
    return result


def upload_and_stage(report: Report, user, fixture: str) -> Upload:
    """Receive a fixture into the report and stage it, as the user."""
    data = (FIXTURES_DIR / fixture).read_bytes()
    upload = receive_file(report, user, fixture, data)
    upload = stage_upload(upload, user)
    upload.refresh_from_db()
    assert upload.status == Upload.Status.AWAITING_APPROVAL
    return upload


def staged_figures(upload: Upload) -> dict:
    """Return the figures Stage saved for the upload, named as in the expected
    results (ENG-02 11.1 and 12.3).
    """
    # The spend sheet is the one with an amount column (ENG-02 4.4).
    (spend_sheet,) = [
        sheet for sheet in upload.column_layout.values() if "amount" in sheet["columns"]
    ]
    amounts = upload.preview["amounts"]
    exceptions = {e["type"]: e["items"] for e in upload.preview["exceptions"]}
    return {
        "header_row": spend_sheet["header_row"],
        "rows": {
            "to_commit": upload.rows_new,
            "skipped": upload.rows_skipped,
            "other": len(exceptions["other_row"]),
        },
        "period": {
            "first_posting": upload.period_start.isoformat(),
            "last_posting": upload.period_end.isoformat(),
        },
        "reconciliation": {
            "control_total": str(upload.control_total),
            "to_commit": amounts["to_commit"],
            "skipped": amounts["skipped"],
            "difference": str(upload.reconciliation_difference),
        },
        "skipped_rows": [
            {"row": row.row_number, "reason": row.reason}
            for row in SkippedRow.objects.filter(upload=upload).order_by("pk")
        ],
        # Named as in the expected results (ENG-02 12.3).
        "exceptions": {
            "missing_from_gl_mapping": by_code(exceptions["missing_from_gl_map"]),
            "listed_without_ci": by_code(exceptions["listed_without_ci"]),
            "outside_ids": by_code(exceptions["outside_ids"]),
            "other_rows": [i["rows"][0][1] for i in exceptions["other_row"]],
        },
    }


def by_code(items: list[dict]) -> dict:
    """Return an exception's items as the expected results list them: each
    code's amount and row numbers.
    """
    return {
        i["code"]: {"amount": i["amount"], "rows": [row for _, row in i["rows"]]}
        for i in items
    }


def report_figures(report: Report) -> dict:
    """Return the report's actual spend and its groups, as manage.py
    report_totals prints them from reports/queries.py.
    """
    out = io.StringIO()
    call_command("report_totals", stdout=out)
    (printed,) = [
        entry
        for entry in json.loads(out.getvalue())["reports"]
        if entry["report"] == report.pk
    ]
    keys = ("actual_spend", "by_gl", "by_ci", "by_department", "by_month")
    return {key: printed[key] for key in keys}


def test_the_sample_file_gives_its_expected_figures() -> None:
    """Verify sample.xlsx, as the first upload into an empty report, stages
    and commits to every figure in sample.expected.json.
    """
    user = make_user()
    report = create_report("IDS 2026", user)
    upload = upload_and_stage(report, user, SAMPLE_XLSX)
    figures = staged_figures(upload)
    commit_upload(upload, user)
    figures |= report_figures(report)
    assert figures == expected(SAMPLE_XLSX)
    # The first upload into an empty report adds all the report's spend.
    assert upload.preview["actual_spend"] == figures["actual_spend"]


def test_the_semicolon_copy_uploaded_after_it_gives_its_expected_figures() -> None:
    """Verify sample_semicolon.csv, uploaded after sample.xlsx into the same
    report, replaces every posting of the workbook, gives the same figures
    with the workbook's mappings, and leaves the report total equal to the
    previous total minus the replaced postings plus the new ones (ENG-02 7.3,
    8.1 and 10.3).
    """
    user = make_user()
    report = create_report("IDS 2026", user)
    commit_upload(upload_and_stage(report, user, SAMPLE_XLSX), user)
    before = queries.totals(report).total

    upload = upload_and_stage(report, user, SAMPLE_CSV)
    figures = staged_figures(upload)
    figures["replaced"] = {
        "rows": upload.rows_replaced,
        "amount": upload.preview["amounts"]["replaced"],
    }
    commit_upload(upload, user)
    figures |= report_figures(report)
    assert figures == expected(SAMPLE_CSV)

    replaced = Decimal(figures["replaced"]["amount"])
    new = Decimal(figures["reconciliation"]["to_commit"])
    assert queries.totals(report).total == before - replaced + new
