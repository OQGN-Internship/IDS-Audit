"""Commit: approving an upload, so its postings count (ENG-02 10.1).

Only imports/ writes postings, and only here, inside the one transaction that
runs at approval (ENG-01 4, rule 2).
"""

from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import StagingRow, Upload
from ids_audit.imports.states import lock_upload, move
from ids_audit.ledger.models import Posting
from ids_audit.pipeline.reconciliation import amount_text
from ids_audit.reports.models import Report


class CommitCheckFailed(Exception):
    """The postings written don't match the preview's count and total, so the
    commit is undone (ENG-02 10.1, step 5).
    """


def commit_upload(upload: Upload, user: User) -> Upload:
    """Approve an upload at awaiting_approval, in one transaction: either every
    step happens or none does (ENG-02 10.1).

    Phase 1 leaves out the checks that need Phase 2: the user's access, what
    blocks approval (9.3), and whether the preview is still current (ENG-01 4,
    Build order). An upload not at awaiting_approval is refused with
    WrongState.
    """
    with transaction.atomic():
        # Step 1. A second approval for the same report waits here.
        report = Report.objects.select_for_update().get(pk=upload.report_id)
        # Step 2.
        upload = lock_upload(upload, Upload.Status.AWAITING_APPROVAL)
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
        details = {"postings": written["count"], "amount": amount_text(total)}
        if not report.duplicate_mode:
            # Line-key mode is chosen in the first upload's preview, in Phase 2.
            report.duplicate_mode = Report.DuplicateMode.REPLACE_BY_PERIOD
            report.save(update_fields=["duplicate_mode"])
            details["duplicate_mode"] = report.duplicate_mode
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
