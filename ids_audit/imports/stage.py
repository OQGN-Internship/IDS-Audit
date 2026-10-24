"""Stage: reading an upload's stored file and saving what reading found, in one
transaction (ENG-01 4, Build order, and 5.1 steps 3 and 4; ENG-02 6).

Only imports/ writes uploads and staging rows (ENG-01 4, rule 2).
"""

import dataclasses
import datetime
from collections.abc import Sequence
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Count, Sum

from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import SkippedRow, StagingRow, Upload
from ids_audit.imports.states import lock_upload, move
from ids_audit.imports.storage import read_stored_file
from ids_audit.ledger.models import CcDeptMap, GlCiMap, PostingResolved
from ids_audit.pipeline.columns import NeedsColumns
from ids_audit.pipeline.formats import UnknownFormat
from ids_audit.pipeline.read import read_file
from ids_audit.pipeline.readers import FileUnreadable
from ids_audit.pipeline.reconciliation import amount_text, reconcile
from ids_audit.pipeline.results import CleanRow, ReadResult, raw_values
from ids_audit.reports.models import Report

Status = Upload.Status
Action = AuditEvent.Action

# The row figures (ENG-02 11.1), which upload_awaiting_approval keeps.
ROW_FIGURES = (
    "rows_read",
    "rows_new",
    "rows_duplicate",
    "rows_skipped",
    "rows_replaced",
)


def stage_upload(upload: Upload, user: User) -> Upload:
    """Read an upload at reading with its ruleset version's settings, and save
    what reading found, all in one transaction (ENG-02 6).

    A file read in full is staged and moves to awaiting_approval. One missing a
    required column moves to needs_columns, and one that can't be read to
    failed, each saving nothing else. An upload that isn't at reading is
    refused with WrongState. user is whoever started the read (ENG-01 6.3).
    """
    with transaction.atomic():
        upload = lock_upload(upload, Status.READING)
        try:
            data = read_stored_file(upload.file_sha256)
            result = read_file(data, upload.ruleset.settings)
        except NeedsColumns as stop:
            problems = [
                {"sheet": p.sheet, "field": p.field, "columns": list(p.columns)}
                for p in stop.problems
            ]
            move(
                upload,
                Status.NEEDS_COLUMNS,
                Action.UPLOAD_NEEDS_COLUMNS,
                user,
                {"problems": problems},
            )
            return upload
        # The request refuses an unknown format before an upload exists
        # (ENG-02 6), from Phase 2. Until then such a file fails here.
        except (FileUnreadable, UnknownFormat) as error:
            upload.error = str(error)
            move(
                upload,
                Status.FAILED,
                Action.UPLOAD_FAILED,
                user,
                {"error": upload.error},
            )
            return upload

        save_rows(upload, result)
        save_figures(upload, result)
        move(
            upload,
            Status.AWAITING_APPROVAL,
            Action.UPLOAD_AWAITING_APPROVAL,
            user,
            {
                **{name: getattr(upload, name) for name in ROW_FIGURES},
                "reconciliation_difference": (
                    None
                    if upload.reconciliation_difference is None
                    else amount_text(upload.reconciliation_difference)
                ),
            },
        )
    return upload


def save_rows(upload: Upload, result: ReadResult) -> None:
    """Save the staging rows, skipped rows and mapping rows, each tagged with
    the upload (ENG-02 6, What's kept).
    """
    copy_staging_rows(upload, result.clean_rows)
    SkippedRow.objects.bulk_create(
        SkippedRow(
            upload=upload,
            sheet=row.sheet,
            row_number=row.row_number,
            reason=row.reason,
            amount=row.amount,
            raw_values=raw_values(row.cells),
        )
        for row in result.skipped_rows
    )
    GlCiMap.objects.bulk_create(
        GlCiMap(
            upload=upload,
            report_id=upload.report_id,
            gl_code=row.gl_code,
            gl_name=row.gl_name,
            ci_code=row.ci_code,
            ci_name=row.ci_name,
        )
        for row in result.gl_map
    )
    CcDeptMap.objects.bulk_create(
        CcDeptMap(
            upload=upload,
            report_id=upload.report_id,
            cc_code=row.cc_code,
            cc_name=row.cc_name,
            dept_code=row.dept_code,
            dept_name=row.dept_name,
        )
        for row in result.cc_map
    )


def copy_staging_rows(upload: Upload, rows: Sequence[CleanRow]) -> None:
    """Bulk-load the clean rows into staging_row with COPY (ENG-01 5.1, step 4).

    COPY sends every row in one stream, where INSERT would send a statement per
    batch. The columns are the model's, so a field added to the model and not
    given here fails loudly.
    """
    fields = [f for f in StagingRow._meta.concrete_fields if not f.primary_key]
    # Column names come from the model, never from a file.
    columns = ", ".join(connection.ops.quote_name(f.column) for f in fields)
    with connection.cursor() as cursor:
        with cursor.copy(f"COPY staging_row ({columns}) FROM STDIN") as copy:
            for row in rows:
                values = {
                    **{f.name: getattr(row, f.name) for f in dataclasses.fields(row)},
                    # An unclassified value type is staged as not actual
                    # spend, and approval is blocked (ENG-02 5 and 9.3).
                    "is_actual": row.is_actual is True,
                    "report_id": upload.report_id,
                    "upload_id": upload.pk,
                    # Duplicates are marked in line-key mode (ENG-02 7.3).
                    "is_duplicate": False,
                }
                copy.write_row([values[f.attname] for f in fields])


def save_figures(upload: Upload, result: ReadResult) -> None:
    """Set the upload's column layout, period, control total, row figures and
    their amounts, and difference (ENG-02 9 and 11.1). move() saves them.
    """
    reconciliation = reconcile(result)
    skipped_spend = [r for r in result.skipped_rows if r.sheet == result.spend_sheet]
    replaced, replaced_amount = replaced_postings(upload.report, result)

    upload.column_layout = result.column_layout
    upload.period_start = result.period_start
    upload.period_end = result.period_end
    upload.control_total = reconciliation.control_total
    upload.control_total_source = (
        "" if result.control_total is None else Upload.ControlTotalSource.FILE
    )
    upload.rows_read = len(result.clean_rows) + len(skipped_spend)
    upload.rows_new = len(result.clean_rows)
    upload.rows_duplicate = 0
    upload.rows_skipped = len(skipped_spend)
    upload.rows_replaced = replaced
    upload.preview = {
        "amounts": {
            "to_commit": amount_text(reconciliation.staged),
            "duplicates": amount_text(Decimal(0)),
            "skipped": amount_text(reconciliation.skipped),
            "replaced": amount_text(replaced_amount),
        }
    }
    upload.reconciliation_difference = reconciliation.difference


def replaced_postings(report: Report, result: ReadResult) -> tuple[int, Decimal]:
    """Return the number and amount of the postings that count in the file's
    months, which committing it replaces (ENG-02 7.3). None in line-key mode.
    """
    line_key = report.duplicate_mode == Report.DuplicateMode.LINE_KEY
    if line_key or result.period_start is None:
        return 0, Decimal(0)
    found = PostingResolved.objects.filter(
        report=report,
        posting_date__gte=result.period_start.replace(day=1),
        posting_date__lt=first_of_next_month(result.period_end),
    ).aggregate(count=Count("pk"), amount=Sum("amount"))
    return found["count"], found["amount"] or Decimal(0)


def first_of_next_month(day: datetime.date) -> datetime.date:
    """Return the first day of the month after day's."""
    if day.month == 12:
        return datetime.date(day.year + 1, 1, 1)
    return datetime.date(day.year, day.month + 1, 1)
