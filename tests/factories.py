"""Rows for tests to build on, each with every link it needs (ENG-02 2)."""

import datetime
from decimal import Decimal

from django.db.models import Max, Min

from ids_audit.accounts.models import User
from ids_audit.imports.models import Upload
from ids_audit.ledger.models import CcDeptMap, GlCiMap, Posting
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports.models import Report, Ruleset

# Commits are timed in minutes after this, so a test decides their order
# (ENG-02 10.2).
FIRST_COMMIT = datetime.datetime(2026, 7, 1, 9, 0, tzinfo=datetime.UTC)


def make_user(fixed_admin: bool = True) -> User:
    """A new user, with a username no other test user has.

    A fixed administrator unless fixed_admin is False, since until Phase 4
    adds roles nobody else can act on a report (ENG-01 6.2).
    """
    number = User.objects.count() + 1
    return User.objects.create(
        username=f"user{number}",
        full_name=f"Test User {number}",
        is_fixed_admin=fixed_admin,
    )


# A password every validator accepts (ENG-01 6.1).
PASSWORD = "plain-tulip-harbour-61"


def make_admin(must_change_password: bool = False) -> User:
    """A new fixed administrator who signs in with PASSWORD, and has already
    chosen it unless must_change_password.
    """
    user = make_user()
    user.must_change_password = must_change_password
    user.set_password(PASSWORD)
    user.save()
    return user


def make_report(mapping_rule: str = "latest") -> Report:
    """A report with its ruleset version 1, created by a new user.

    It's written row by row, not by create_report, so tests of other parts
    don't depend on it.
    """
    user = make_user()
    report = Report.objects.create(name=f"Report of {user.username}", created_by=user)
    Ruleset.objects.create(
        report=report,
        version=1,
        settings={**DEFAULT_SETTINGS, "mapping_rule": mapping_rule},
        created_by=user,
    )
    return report


def make_upload(
    report: Report | None = None,
    number: int | None = None,
    status: str = Upload.Status.READING,
) -> Upload:
    """An upload, read with ruleset version 1, in a new report unless given one.

    It's numbered after the report's last upload unless given a number.
    """
    report = report or make_report()
    number = number or report.uploads.count() + 1
    return Upload.objects.create(
        report=report,
        number=number,
        file_sha256=f"{number:064d}",
        original_name=f"file{number}.xlsx",
        size_bytes=1,
        uploaded_by=report.created_by,
        ruleset=report.rulesets.get(version=1),
        status=status,
    )


def commit(
    upload: Upload,
    minute: int,
    mode: str = Report.DuplicateMode.REPLACE_BY_PERIOD,
) -> Upload:
    """Mark an upload committed, `minute` minutes after FIRST_COMMIT.

    Add its postings first: its period is their first and last date, as reading
    sets it. The report's first commit sets its duplicate mode (ENG-02 10.1).
    """
    dates = Posting.objects.filter(upload=upload).aggregate(
        first=Min("posting_date"), last=Max("posting_date")
    )
    upload.period_start, upload.period_end = dates["first"], dates["last"]
    upload.status = Upload.Status.COMMITTED
    upload.decided_by = upload.uploaded_by
    upload.decided_at = FIRST_COMMIT + datetime.timedelta(minutes=minute)
    upload.save()
    if not upload.report.duplicate_mode:
        upload.report.duplicate_mode = mode
        upload.report.save()
    return upload


def roll_back(upload: Upload) -> Upload:
    """Mark a committed upload rolled back. Its rows all stay (ENG-02 10.3)."""
    upload.status = Upload.Status.ROLLED_BACK
    upload.rolled_back_by = upload.uploaded_by
    upload.rolled_back_at = upload.decided_at + datetime.timedelta(seconds=30)
    upload.save()
    return upload


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


def add_posting(
    upload: Upload,
    posting_date: datetime.date,
    amount: str,
    gl_code: str = "610100",
    cost_center_code: str = "4510",
) -> Posting:
    """A posting from the upload. The amount is text, so it never passes
    through a float.
    """
    fields = spend_line(upload, Decimal(amount))
    fields.update(
        posting_date=posting_date, gl_code=gl_code, cost_center_code=cost_center_code
    )
    return Posting.objects.create(**fields)


def map_gl(
    upload: Upload, gl_code: str, ci_code: str, ci_name: str | None = None
) -> GlCiMap:
    """A row of the upload's GL mapping. An empty ci_code lists the GL account
    without a commitment item.
    """
    return GlCiMap.objects.create(
        upload=upload,
        report=upload.report,
        gl_code=gl_code,
        gl_name=f"GL {gl_code}",
        ci_code=ci_code,
        ci_name=f"CI {ci_code}" if ci_name is None else ci_name,
    )


def map_cc(upload: Upload, cc_code: str, dept_code: str) -> CcDeptMap:
    """A row of the upload's cost center mapping."""
    return CcDeptMap.objects.create(
        upload=upload,
        report=upload.report,
        cc_code=cc_code,
        cc_name=f"CC {cc_code}",
        dept_code=dept_code,
        dept_name=f"Dept {dept_code}",
    )
