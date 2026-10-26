"""Commit: approving an upload, so its postings count (ENG-02 10.1).

Only imports/ writes postings, and only here, inside the one transaction that
runs at approval (ENG-01 4, rule 2).
"""

from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from ids_audit import audit
from ids_audit.accounts.access import APPROVE, require
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import StagingRow, Upload
from ids_audit.imports.states import Refused, lock_upload, move
from ids_audit.ledger.models import Posting
from ids_audit.pipeline.reconciliation import amount_text
from ids_audit.reports.models import Report

DuplicateMode = Report.DuplicateMode


class ApprovalRefused(Refused):
    """The upload can't be approved as asked, so nothing is written (ENG-02
    10.1). reasons are what approval_refused keeps (ENG-01 6.3).
    """

    PREVIEW_OUT_OF_DATE = "preview_out_of_date"
    MODE_NOT_ALLOWED = "mode_not_allowed"
    COMMIT_CHECK_FAILED = "commit_check_failed"

    def __init__(self, reasons: list[str], message: str) -> None:
        super().__init__(message)
        self.reasons = reasons


class CommitCheckFailed(ApprovalRefused):
    """The postings written don't match the preview's count and total, so the
    commit is undone (ENG-02 10.1, step 5).
    """

    def __init__(self, message: str) -> None:
        super().__init__([self.COMMIT_CHECK_FAILED], message)


def commit_upload(
    upload: Upload, user: User, duplicate_mode: str | None = None
) -> Upload:
    """Approve an upload at awaiting_approval, in one transaction: either every
    step happens or none does (ENG-02 10.1).

    duplicate_mode is the approver's choice of mode on the report's first
    commit, replace by period if it's None (7.3). An upload not at
    awaiting_approval is refused with WrongState. One whose preview lists an
    exception that blocks approval, whose preview is out of date, or that
    can't take the mode chosen is refused with ApprovalRefused, which an
    approval_refused event keeps. A user who no longer holds approve gets the
    access check's 404 or 403, with no event (ENG-01 6.2).
    """
    try:
        return approve(upload, user, duplicate_mode)
    except ApprovalRefused as refusal:
        # Written after the refused transaction is undone, so it's kept.
        audit.record(
            AuditEvent.Action.APPROVAL_REFUSED,
            user=user,
            report=upload.report,
            upload=upload,
            details={"reasons": refusal.reasons, "message": str(refusal)},
        )
        raise


def approve(upload: Upload, user: User, duplicate_mode: str | None) -> Upload:
    """Run the commit's steps in one transaction (ENG-02 10.1)."""
    with transaction.atomic():
        # Step 1. A second approval for the same report waits here.
        report = Report.objects.select_for_update().get(pk=upload.report_id)
        # Step 2. The view checked approve too, before the report was locked.
        require(user, report, APPROVE)
        upload = lock_upload(upload, Upload.Status.AWAITING_APPROVAL)
        preview = upload.preview or {}
        blocking = [
            e["type"] for e in preview.get("exceptions", ()) if e["blocks_approval"]
        ]
        if blocking:
            raise ApprovalRefused(
                blocking,
                "The file can't be approved until nothing blocks it: "
                f"{', '.join(blocking)}.",
            )
        # Step 3.
        if preview.get("committed_uploads") != committed_numbers(report):
            raise ApprovalRefused(
                [ApprovalRefused.PREVIEW_OUT_OF_DATE],
                "An upload was approved or rolled back since this preview was "
                "built. Reject this upload and upload the file again.",
            )
        first_mode = mode_to_set(report, upload, duplicate_mode)
        # Step 4.
        copy_to_postings(upload)
        # Step 5. An upload is committed once, so its postings are the new ones.
        written = Posting.objects.filter(upload=upload).aggregate(
            count=Count("pk"), total=Sum("amount")
        )
        total = written["total"] or Decimal(0)
        expected = (upload.rows_new, Decimal(upload.preview["amounts"]["to_commit"]))
        if (written["count"], total) != expected:
            raise CommitCheckFailed(
                f"The commit wrote {written['count']} postings totalling "
                f"{amount_text(total)}, but the preview shows {expected[0]} "
                f"totalling {amount_text(expected[1])}."
            )
        # Step 6. The time comes from the application's clock after the lock,
        # so a commit that waited is always later than the one it waited for
        # (10.2).
        upload.decided_by = user
        upload.decided_at = timezone.now()
        details = {
            "postings": written["count"],
            "amount": amount_text(total),
            # How many items each exception type listed (ENG-02 11.2).
            "exceptions": {e["type"]: len(e["items"]) for e in preview["exceptions"]},
        }
        if first_mode:
            report.duplicate_mode = first_mode
            report.save(update_fields=["duplicate_mode"])
            details["duplicate_mode"] = first_mode
        move(
            upload,
            Upload.Status.COMMITTED,
            AuditEvent.Action.UPLOAD_APPROVED,
            user,
            details,
        )
        # Step 7.
        StagingRow.objects.filter(upload=upload).delete()
    return upload


def mode_to_set(report: Report, upload: Upload, chosen: str | None) -> str | None:
    """Return the duplicate mode a first commit sets, or None on a later one
    (ENG-02 7.3 and 10.1, step 6). Refuse a mode chosen on a later commit, and
    line-key mode for a file the preview doesn't offer it for.
    """
    if report.duplicate_mode:
        if chosen is not None:
            raise ApprovalRefused(
                [ApprovalRefused.MODE_NOT_ALLOWED],
                "The duplicate mode is chosen on a report's first approval only. "
                f"This report uses {report.get_duplicate_mode_display().lower()}.",
            )
        return None
    mode = DuplicateMode(chosen or DuplicateMode.REPLACE_BY_PERIOD)
    if mode == DuplicateMode.LINE_KEY and not upload.preview.get("line_key_offered"):
        raise ApprovalRefused(
            [ApprovalRefused.MODE_NOT_ALLOWED],
            "Line-key mode isn't offered for this file: some of its rows have no "
            "company code, fiscal year, document number or line item.",
        )
    return mode


def committed_numbers(report: Report) -> list[int]:
    """Return the numbers of the report's committed uploads, in the order of
    commits (ENG-02 10.2), which a preview keeps (11.1).
    """
    return list(
        report.uploads.filter(status=Upload.Status.COMMITTED)
        .order_by("decided_at", "number")
        .values_list("number", flat=True)
    )


def copy_to_postings(upload: Upload) -> None:
    """Copy the upload's staging rows not marked is_duplicate into posting, in
    one statement, keeping the file's order (ENG-02 10.1, step 4).
    """
    fields = [f for f in Posting._meta.concrete_fields if not f.primary_key]
    # Column names come from the model, never from a file. Posting and
    # StagingRow share them through SpendLine.
    columns = ", ".join(connection.ops.quote_name(f.column) for f in fields)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO posting ({columns}) SELECT {columns} FROM staging_row"
            " WHERE upload_id = %s AND NOT is_duplicate ORDER BY id",
            [upload.pk],
        )
