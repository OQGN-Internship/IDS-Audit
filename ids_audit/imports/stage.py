"""Stage: reading an upload's stored file and saving what reading found, in one
transaction (ENG-01 4, Build order, and 5.1 steps 3 and 4; ENG-02 6).

Only imports/ writes uploads and staging rows (ENG-01 4, rule 2).
"""

import dataclasses
from collections.abc import Mapping, Sequence
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Count, Sum

from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import SkippedRow, StagingRow, Upload
from ids_audit.imports.preview import report_preview
from ids_audit.imports.states import lock_upload, move
from ids_audit.imports.storage import read_stored_file
from ids_audit.ledger.models import CcDeptMap, GlCiMap, PostingResolved
from ids_audit.pipeline.columns import Choice, NeedsColumns
from ids_audit.pipeline.duplicates import find_duplicates, line_key_offered
from ids_audit.pipeline.formats import UnknownFormat
from ids_audit.pipeline.preview import first_of_next_month
from ids_audit.pipeline.read import read_file
from ids_audit.pipeline.readers import FileUnreadable
from ids_audit.pipeline.reconciliation import amount_text, reconcile
from ids_audit.pipeline.results import CleanRow, ReadResult, raw_values
from ids_audit.reports.models import Report

Status = Upload.Status
Action = AuditEvent.Action

# The row figures (ENG-02 11.1), which upload_awaiting_approval keeps.
# The staging rows already in the report, by their place in the file, with
# the amount of the posting each repeats (ENG-02 7.3).
Duplicates = dict[tuple[str, int], Decimal]

ROW_FIGURES = (
    "rows_read",
    "rows_new",
    "rows_duplicate",
    "rows_skipped",
    "rows_replaced",
)


def stage_upload(upload: Upload, user: User, choices: Sequence[Choice] = ()) -> Upload:
    """Read an upload at reading with its ruleset version's settings, and save
    what reading found, all in one transaction (ENG-02 6).

    A file read in full is staged and moves to awaiting_approval. One missing a
    required column moves to needs_columns, and one that can't be read to
    failed, each saving nothing else. An upload that isn't at reading is
    refused with WrongState. user is whoever started the read (ENG-01 6.3).
    choices are columns just picked, used before those the report's committed
    uploads picked (ENG-02 4.5).
    """
    with transaction.atomic():
        upload = lock_upload(upload, Status.READING)
        line_key_mode = upload.report.duplicate_mode == Report.DuplicateMode.LINE_KEY
        try:
            data = read_stored_file(upload.file_sha256)
            result = read_file(
                data,
                upload.ruleset.settings,
                need_line_key=line_key_mode,
                choices=[*choices, *saved_choices(upload.report)],
                mapping_only=upload.kind == Upload.Kind.MAPPING,
            )
        except NeedsColumns as stop:
            problems = [
                {"sheet": p.sheet, "field": p.field, "columns": list(p.columns)}
                for p in stop.problems
            ]
            headers = {sheet: list(header) for sheet, header in stop.headers.items()}
            move(
                upload,
                Status.NEEDS_COLUMNS,
                Action.UPLOAD_NEEDS_COLUMNS,
                user,
                {"problems": problems, "headers": headers},
            )
            return upload
        # receive_file refuses an unknown format before an upload exists
        # (ENG-02 6), so only an upload received before it did fails here.
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

        duplicates = (
            find_duplicates(result.clean_rows, counted_line_keys(upload.report))
            if line_key_mode
            else {}
        )
        save_rows(upload, result, duplicates)
        save_figures(upload, result, duplicates)
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


def pick_columns(
    upload: Upload, user: User, picks: Mapping[str, Mapping[str, str]]
) -> Upload:
    """Move an upload at needs_columns back to reading with the columns the
    user picked, and read it again, all in one transaction (ENG-02 4.5 and 6).

    picks maps each sheet's name to the column letter picked for each field,
    such as {"Spend": {"amount": "M"}}. They add to the columns picked before
    for the upload. A pick for a sheet or field the upload didn't stop for, or
    no pick at all, is a ValueError, and so is a letter that isn't a column,
    once reading meets it. Either way nothing changes. An upload not at
    needs_columns is refused with WrongState.
    """
    with transaction.atomic():
        upload = lock_upload(upload, Status.NEEDS_COLUMNS)
        # Why it stopped, and what was picked before, are kept only in its
        # events (ENG-02 6, What's kept).
        events = AuditEvent.objects.filter(upload=upload).order_by("pk")
        stops = events.filter(action=Action.UPLOAD_NEEDS_COLUMNS)
        needed = {(p["sheet"], p["field"]) for p in stops.last().details["problems"]}
        if not any(picks.values()):
            raise ValueError("Pick a column for at least one field.")
        for sheet, fields in picks.items():
            for field in fields:
                if (sheet, field) not in needed:
                    raise ValueError(f"{field} on sheet {sheet!r} needs no column.")
        headers = {}
        for stop in stops:
            headers.update(stop.details["headers"])
        before = events.filter(action=Action.UPLOAD_COLUMNS_PICKED).last()
        columns = {
            sheet: dict(fields)
            for sheet, fields in (before.details["columns"] if before else {}).items()
        }
        for sheet, fields in picks.items():
            columns.setdefault(sheet, {}).update(fields)
        move(
            upload,
            Status.READING,
            Action.UPLOAD_COLUMNS_PICKED,
            user,
            {"columns": columns},
        )
        choices = [
            Choice(tuple(headers[sheet]), fields) for sheet, fields in columns.items()
        ]
        return stage_upload(upload, user, choices)


def saved_choices(report: Report) -> list[Choice]:
    """Return the columns picked in the report's committed uploads, the most
    recently committed first, so a file with the same header is matched with
    them (ENG-02 4.5 and 10.2).
    """
    layouts = (
        report.uploads.filter(status=Status.COMMITTED)
        .order_by("-decided_at", "-number")
        .values_list("column_layout", flat=True)
    )
    return [
        Choice(tuple(sheet["header"]), sheet["picked"])
        for layout in layouts
        for sheet in (layout or {}).values()
        if sheet.get("picked")
    ]


def counted_line_keys(report: Report) -> dict[str, Decimal]:
    """Return the line key of each of the report's postings that count, with
    its amount (ENG-02 7.3).
    """
    return dict(
        PostingResolved.objects.filter(report=report)
        .exclude(line_key="")
        .values_list("line_key", "amount")
    )


def save_rows(upload: Upload, result: ReadResult, duplicates: Duplicates) -> None:
    """Save the staging rows, skipped rows and mapping rows, each tagged with
    the upload (ENG-02 6, What's kept).
    """
    copy_staging_rows(upload, result.clean_rows, duplicates)
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


def copy_staging_rows(
    upload: Upload, rows: Sequence[CleanRow], duplicates: Duplicates
) -> None:
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
                    # Only in line-key mode (ENG-02 7.3).
                    "is_duplicate": (row.sheet, row.row_number) in duplicates,
                }
                copy.write_row([values[f.attname] for f in fields])


def save_figures(upload: Upload, result: ReadResult, duplicates: Duplicates) -> None:
    """Set the upload's column layout, period, control total, row figures and
    their amounts, difference and preview (ENG-02 9 and 11). move() saves them.
    The staging rows must already be saved.
    """
    # A total typed on the upload form counts only for a file with no total
    # row (ENG-02 9.1).
    entered = (
        upload.control_total
        if upload.control_total_source == Upload.ControlTotalSource.ENTERED
        else None
    )
    reconciliation = reconcile(result, entered)
    to_commit = [
        r for r in result.clean_rows if (r.sheet, r.row_number) not in duplicates
    ]
    duplicate_amount = reconciliation.staged - sum(
        (r.amount for r in to_commit), Decimal(0)
    )
    skipped_spend = [r for r in result.skipped_rows if r.sheet == result.spend_sheet]
    replaced, replaced_amount = replaced_postings(upload.report, result)

    upload.column_layout = result.column_layout
    upload.period_start = result.period_start
    upload.period_end = result.period_end
    upload.control_total = reconciliation.control_total
    if result.control_total is not None:
        upload.control_total_source = Upload.ControlTotalSource.FILE
    upload.rows_read = len(result.clean_rows) + len(skipped_spend)
    upload.rows_new = len(result.clean_rows) - len(duplicates)
    upload.rows_duplicate = len(duplicates)
    upload.rows_skipped = len(skipped_spend)
    upload.rows_replaced = replaced
    upload.preview = {
        "amounts": {
            "to_commit": amount_text(reconciliation.staged - duplicate_amount),
            "duplicates": amount_text(duplicate_amount),
            "skipped": amount_text(reconciliation.skipped),
            "replaced": amount_text(replaced_amount),
        },
        # Chosen when the report's first upload is approved (ENG-02 7.3).
        "line_key_offered": (
            not upload.report.duplicate_mode and line_key_offered(result.clean_rows)
        ),
        **report_preview(upload, result, reconciliation, to_commit, duplicates),
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
