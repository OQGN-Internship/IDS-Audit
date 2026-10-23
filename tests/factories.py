"""Rows for tests to build on, each with every link it needs (ENG-02 2)."""

import datetime
from decimal import Decimal

from ids_audit.accounts.models import User
from ids_audit.imports.models import Upload
from ids_audit.reports.models import Report, Ruleset


def make_report() -> Report:
    """A report with its ruleset version 1, created by a new user."""
    number = User.objects.count() + 1
    user = User.objects.create(
        username=f"user{number}", full_name=f"Test User {number}"
    )
    report = Report.objects.create(name=f"Test report {number}", created_by=user)
    Ruleset.objects.create(report=report, version=1, settings={}, created_by=user)
    return report


def make_upload(report: Report | None = None, number: int = 1) -> Upload:
    """An upload, read with ruleset version 1, in a new report unless given one."""
    report = report or make_report()
    return Upload.objects.create(
        report=report,
        number=number,
        file_sha256=f"{number:064d}",
        original_name=f"file{number}.xlsx",
        size_bytes=1,
        uploaded_by=report.created_by,
        ruleset=report.rulesets.get(version=1),
    )


def spend_line(upload: Upload, amount: Decimal = Decimal("3456.789")) -> dict:
    """The fields of one spend line, for a posting or a staging row."""
    return {
        "report": upload.report,
        "upload": upload,
        "row_number": 5,
        "posting_date": datetime.date(2025, 7, 1),
        "gl_code": "610100",
        "cost_center_code": "3100",
        "is_actual": True,
        "amount": amount,
        "currency": "OMR",
    }
