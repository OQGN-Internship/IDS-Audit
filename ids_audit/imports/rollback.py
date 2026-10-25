"""Rollback: an approved upload stops counting, and nothing is deleted
(ENG-02 10.3).
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import Upload
from ids_audit.imports.states import (
    Refused,
    fail_interrupted_reads,
    given_reason,
    lock_upload,
    move,
    open_upload,
)
from ids_audit.reports import queries
from ids_audit.reports.models import Report

Status = Upload.Status


class RollbackRefused(Refused):
    """The upload can't be rolled back now, so nothing changes (ENG-02 10.3)."""


def roll_back_upload(upload: Upload, user: User, reason: str) -> Upload:
    """Roll back the report's most recently committed upload, as a user with
    roll_back, in one transaction (ENG-02 10.3).

    An empty reason is a ValueError. An upload that isn't committed is
    refused with WrongState. One that isn't the latest committed, or in a
    report with an open upload, is refused with RollbackRefused.
    """
    reason = given_reason(reason)
    # So a read that was interrupted doesn't count as an open upload.
    fail_interrupted_reads(upload.report)
    with transaction.atomic():
        # Locks the report's row, as a commit and a new file do, so neither
        # can change the order of commits meanwhile.
        report = Report.objects.select_for_update().get(pk=upload.report_id)
        still_open = open_upload(report)
        if still_open is not None:
            raise RollbackRefused(
                f"Upload {still_open.number} is still open "
                f"({still_open.get_status_display().lower()}). A rollback waits "
                "until it's approved, rejected or withdrawn."
            )
        upload = lock_upload(upload, Status.COMMITTED)
        latest = latest_committed(report)
        if latest.pk != upload.pk:
            raise RollbackRefused(
                f"Only upload {latest.number}, the most recently approved, can be "
                "rolled back. Roll back the newer uploads first, newest first."
            )
        upload.rolled_back_by = user
        upload.rolled_back_at = timezone.now()
        move(
            upload,
            Status.ROLLED_BACK,
            AuditEvent.Action.UPLOAD_ROLLED_BACK,
            user,
            {"reason": reason},
        )
    return upload


def latest_committed(report: Report) -> Upload | None:
    """Return the report's most recently committed upload (ENG-02 10.2)."""
    return (
        report.uploads.filter(status=Status.COMMITTED)
        .order_by("-decided_at", "-number")
        .first()
    )


def actual_spend_change(upload: Upload) -> tuple[Decimal, Decimal]:
    """Return the report's actual spend now and once the upload is rolled
    back, for the confirmation screen (ENG-02 10.3).

    The rollback is made in a savepoint, the figure read through
    reports/queries.py, and the savepoint rolled back, so the figure follows
    exactly the rules the report will.
    """
    with transaction.atomic():
        before = queries.totals(upload.report).total
        Upload.objects.filter(pk=upload.pk).update(status=Status.ROLLED_BACK)
        after = queries.totals(upload.report).total
        transaction.set_rollback(True)
    return before, after
