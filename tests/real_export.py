"""Checking an export against its expected result (ENG-02 12.2).

The real-export test runs these on every export in real-export/. They're kept
apart so their own tests can run them on the sample file, in a folder of its
own, since the real export is never in the repository.
"""

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import commit_upload
from ids_audit.imports.models import SkippedRow, Upload
from ids_audit.imports.services import change_ruleset, create_report, receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.reports.management.commands.report_totals import report_totals
from tests.factories import make_user

REAL_EXPORT_DIR = Path(__file__).resolve().parents[1] / "real-export"
# Set to 1, a missing expected result fails the test instead of skipping it.
REQUIRE = "IDS_REQUIRE_REAL_EXPORT"
EXPECTED = ".expected.json"
EXPORT_SUFFIXES = (".xlsx", ".xls", ".csv", ".txt")
# The figures an expected result may give, keyed as in report_totals.
FIGURES = ("actual_spend", "by_gl", "by_ci", "by_department", "by_month")
KEYS = ("source", "settings", "control_total", "skipped_rows", *FIGURES)


def expected_results(folder: Path) -> list[Path]:
    """Return the folder's expected results, in name order."""
    return sorted(folder.glob(f"*{EXPECTED}"))


def is_export(path: Path) -> bool:
    """Return True for a file the folder treats as an export."""
    return path.is_file() and path.suffix.lower() in EXPORT_SUFFIXES


def exports_without_expected(folder: Path) -> list[str]:
    """Return the name of each export with no expected result beside it."""
    named = {path.name[: -len(EXPECTED)] for path in expected_results(folder)}
    return sorted(
        path.name
        for path in folder.iterdir()
        if is_export(path) and path.stem not in named
    )


def skip_or_fail(folder: Path) -> None:
    """Skip the real-export test for a folder with no expected result, naming
    the folder and what to put there, or fail it when REQUIRE is set.
    """
    reason = (
        f"{folder} holds no expected result (*{EXPECTED}), so no real export "
        "is checked. Put the export there with SAP's totals in a file named "
        f"after it (ENG-02 12.2), and set {REQUIRE}=1 to make this a failure."
    )
    if os.environ.get(REQUIRE) == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def load(expected_path: Path) -> dict:
    """Return an expected result, with any number written without quotes
    read exactly, never as a float.
    """
    with open(expected_path, encoding="utf-8") as file:
        return json.load(file, parse_float=Decimal, parse_int=Decimal)


def check(expected_path: Path) -> list[str]:
    """Upload the export an expected result is named after into a new report,
    as ENG-02 12.2 says, and return each way it differs from the expected
    result. Empty when it gives every figure.
    """
    expected = load(expected_path)
    differences = [
        f"The expected result has a key it doesn't use: {key}."
        for key in expected
        if key not in KEYS
    ]
    if "source" not in expected:
        differences.append("The expected result doesn't say its source.")
    stem = expected_path.name[: -len(EXPECTED)]
    exports = [
        path
        for path in expected_path.parent.iterdir()
        if is_export(path) and path.stem == stem
    ]
    if len(exports) != 1:
        return [*differences, f"There must be one export named {stem}."]

    admin = make_user()
    # Named after the new user too, so each check gets a report of its own.
    report = create_report(f"Real export {stem} ({admin.username})", admin)
    if expected.get("settings"):
        change_ruleset(report, admin, expected["settings"])
    total = expected.get("control_total")
    upload = receive_file(
        report,
        admin,
        exports[0].name,
        exports[0].read_bytes(),
        control_total=None if total is None else Decimal(total),
    )
    upload = stage_upload(upload, admin)
    upload.refresh_from_db()
    if upload.status != Upload.Status.AWAITING_APPROVAL:
        return [*differences, stopped(upload)]
    blocking = [e for e in upload.preview["exceptions"] if e["blocks_approval"]]
    if blocking:
        return [
            *differences,
            *(f"Approval is blocked: {e['type']}, {e['amount']}." for e in blocking),
        ]
    skipped = [
        {"row": row.row_number, "reason": row.reason}
        for row in SkippedRow.objects.filter(upload=upload).order_by("pk")
    ]
    listed = [
        {"row": int(row["row"]), "reason": row["reason"]}
        for row in expected.get("skipped_rows", [])
    ]
    if skipped != listed:
        differences.append(f"Rows skipped: {skipped}. Rows listed: {listed}.")

    commit_upload(upload, admin)
    totals = report_totals(report)
    for key in FIGURES:
        for code, amount in expected.get(key, {}).items():
            found = totals[key].get(code)
            if found is None or Decimal(found) != Decimal(amount):
                differences.append(
                    f"{key} {code}: the report gives {found or 'nothing'}, "
                    f"the expected result {amount}."
                )
    return differences


def stopped(upload: Upload) -> str:
    """Say where an upload stopped before awaiting approval, and why."""
    if upload.status == Upload.Status.NEEDS_COLUMNS:
        event = AuditEvent.objects.filter(
            upload=upload, action=AuditEvent.Action.UPLOAD_NEEDS_COLUMNS
        ).last()
        return f"The upload needs columns: {event.details['problems']}."
    return f"The upload stopped at {upload.status}: {upload.error}"
