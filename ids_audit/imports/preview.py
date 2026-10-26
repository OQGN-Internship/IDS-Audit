"""The parts of an upload's preview that depend on its report (ENG-02 11).

What approving would change is worked out by approving the upload for a
moment, in a savepoint that's rolled back, and reading the report through
posting_resolved and reports/queries.py as any screen does. So the preview
follows exactly the rules the report will, never a second copy of them
(ENG-02 11.1).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from ids_audit.imports.commit import committed_numbers, copy_to_postings
from ids_audit.imports.models import Upload
from ids_audit.ledger.models import CcDeptMap, GlCiMap, PostingResolved
from ids_audit.pipeline import preview as file_preview
from ids_audit.pipeline.preview import (
    LISTED_WITHOUT_CI,
    LISTED_WITHOUT_DEPARTMENT,
    MAPPING_CHANGE,
    MISSING_FROM_GL_MAP,
    OUTSIDE_IDS,
    REPLACES_OUTSIDE_DATES,
    Place,
    first_of_next_month,
    item,
    occurring,
)
from ids_audit.pipeline.reconciliation import Reconciliation, amount_text
from ids_audit.pipeline.results import CleanRow, ReadResult
from ids_audit.reports import queries
from ids_audit.reports.models import Report

Status = Upload.Status
CiStatus = PostingResolved.CiStatus


@dataclass(frozen=True)
class Mapping:
    """One of the two mappings, as the preview compares them (ENG-02 8.3).

    code and value are the mapping's fields, posting_code the posting's code
    it maps, and resolved the posting_resolved columns its value gives.
    """

    name: str
    model: type[models.Model]
    code: str
    value: str
    posting_code: str
    resolved: tuple[str, str]

    def resolve(self, listed: dict[str, str], code: str) -> tuple:
        """Return what posting_resolved shows for a code under a mapping that
        lists these codes (ENG-02 8.2).
        """
        if self.name == "cc":
            return (False, listed[code]) if code in listed else (True, "")
        if code not in listed:
            return (CiStatus.MISSING_FROM_GL_MAP, "")
        if not listed[code]:
            return (CiStatus.LISTED_WITHOUT_CI, "")
        return (CiStatus.MAPPED, listed[code])


GL = Mapping("gl", GlCiMap, "gl_code", "ci_code", "gl_code", ("ci_status", "ci_code"))
CC = Mapping(
    "cc",
    CcDeptMap,
    "cc_code",
    "dept_code",
    "cost_center_code",
    ("is_outside_ids", "dept_code"),
)

# Each (upload id, code) of the report's postings that count, with the value
# posting_resolved gives them and their actual spend.
Values = dict[tuple[int, str], tuple[tuple, Decimal]]


@dataclass(frozen=True)
class Approved:
    """The report as it would be once the upload is approved."""

    actual_spend: Decimal
    # This upload's postings that are actual spend, in the file's order.
    rows: list[dict]
    values: dict[str, Values]


def report_preview(
    upload: Upload,
    result: ReadResult,
    reconciliation: Reconciliation,
    to_commit: Sequence[CleanRow],
    duplicates: dict[Place, Decimal],
) -> dict:
    """Return the preview's keys other than amounts and line_key_offered
    (ENG-02 11.1). upload's staging rows must already be saved.
    """
    report = upload.report
    line_key_mode = report.duplicate_mode == Report.DuplicateMode.LINE_KEY
    months = []
    if not line_key_mode and result.period_start is not None:
        months = file_preview.months_between(result.period_start, result.period_end)
    in_force = {m.name: mapping_in_force(report, m) for m in (GL, CC)}
    own = {"gl": bool(result.gl_map), "cc": bool(result.cc_map)}
    before = {m.name: values(report, m) for m in (GL, CC)}
    actual_before = queries.totals(report).total
    approved = as_if_approved(upload, result)

    actual, not_actual = file_preview.actual_and_not(to_commit)
    outside = sum(
        (r["amount"] for r in approved.rows if r["is_outside_ids"]), Decimal(0)
    )
    exceptions = file_preview.file_exceptions(
        result, reconciliation, to_commit, duplicates, months
    )
    exceptions += mapped_exceptions(approved.rows)
    # A mapping the upload doesn't have, or that has nothing in force to
    # compare with, lists no changes (ENG-02 8.3). GL accounts come first.
    changes = []
    for mapping, rows in ((GL, result.gl_map), (CC, result.cc_map)):
        if rows and in_force[mapping.name] is not None:
            changes += mapping_changes(
                mapping,
                upload,
                {getattr(r, mapping.code): getattr(r, mapping.value) for r in rows},
                in_force[mapping.name],
                before[mapping.name],
                approved,
            )
    exceptions += occurring(MAPPING_CHANGE, changes)
    if not line_key_mode and result.period_start is not None:
        exceptions += occurring(
            REPLACES_OUTSIDE_DATES, replaced_outside_dates(report, result)
        )

    return {
        "committed_uploads": committed_numbers(report),
        "months_replaced": months,
        "actual_spend": {
            "total": amount_text(actual),
            "inside_ids": amount_text(actual - outside),
            "outside_ids": amount_text(outside),
        },
        "not_actual": amount_text(not_actual),
        "value_types": file_preview.value_types(result, to_commit),
        "mappings": {
            name: upload.number
            if own[name]
            else (in_force[name].number if in_force[name] else None)
            for name in ("gl", "cc")
        },
        "report_actual_spend": {
            "before": amount_text(actual_before),
            "after": amount_text(approved.actual_spend),
        },
        "exceptions": file_preview.in_order(exceptions),
    }


def mapping_in_force(report: Report, mapping: Mapping) -> Upload | None:
    """Return the most recently committed upload of the report that has the
    mapping, whose mapping is in force (ENG-02 8.1 and 10.2), or None.
    """
    return (
        report.uploads.filter(status=Status.COMMITTED)
        .filter(Exists(mapping.model.objects.filter(upload=OuterRef("pk"))))
        .order_by("-decided_at", "-number")
        .first()
    )


def values(report: Report, mapping: Mapping) -> Values:
    """Return what posting_resolved gives each upload's postings for each code
    of the mapping, with their actual spend.
    """
    rows = (
        PostingResolved.objects.filter(report=report)
        .values("upload", mapping.posting_code, *mapping.resolved)
        .annotate(actual=Sum("amount", filter=Q(is_actual=True)))
    )
    return {
        (r["upload"], r[mapping.posting_code]): (
            tuple(r[c] for c in mapping.resolved),
            r["actual"] or Decimal(0),
        )
        for r in rows
    }


def as_if_approved(upload: Upload, result: ReadResult) -> Approved:
    """Approve the upload in a savepoint, read the report, and roll the
    savepoint back (ENG-02 11.1): the commit's steps 4 and 6, without the
    audit event or the report's duplicate mode.
    """
    with transaction.atomic():
        copy_to_postings(upload)
        Upload.objects.filter(pk=upload.pk).update(
            status=Status.COMMITTED,
            decided_at=timezone.now(),
            period_start=result.period_start,
            period_end=result.period_end,
        )
        # A report's first commit also sets its duplicate mode. It isn't set
        # here: with no other upload committed, posting_resolved counts every
        # posting whatever the mode.
        approved = Approved(
            actual_spend=queries.totals(upload.report).total,
            rows=list(
                PostingResolved.objects.filter(upload=upload, is_actual=True)
                .order_by("pk")
                .values(
                    "sheet",
                    "row_number",
                    "amount",
                    "gl_code",
                    "cost_center_code",
                    "ci_status",
                    "is_outside_ids",
                    "dept_code",
                )
            ),
            values={m.name: values(upload.report, m) for m in (GL, CC)},
        )
        transaction.set_rollback(True)
    return approved


def mapped_exceptions(rows: list[dict]) -> list[dict]:
    """Return the exceptions for the upload's actual spend with no budget
    line, outside IDS or with no department, as the report will show them
    (ENG-02 11.2), each code with its rows and amount, in code order.
    """
    found = []
    for kind, code_field, applies in (
        (
            MISSING_FROM_GL_MAP,
            "gl_code",
            lambda r: r["ci_status"] == CiStatus.MISSING_FROM_GL_MAP,
        ),
        (
            LISTED_WITHOUT_CI,
            "gl_code",
            lambda r: r["ci_status"] == CiStatus.LISTED_WITHOUT_CI,
        ),
        (OUTSIDE_IDS, "cost_center_code", lambda r: r["is_outside_ids"]),
        (
            LISTED_WITHOUT_DEPARTMENT,
            "cost_center_code",
            lambda r: not r["is_outside_ids"] and not r["dept_code"],
        ),
    ):
        by_code: dict[str, list[dict]] = {}
        for row in filter(applies, rows):
            by_code.setdefault(row[code_field], []).append(row)
        found += occurring(
            kind,
            [
                item(
                    [(r["sheet"], r["row_number"]) for r in code_rows],
                    sum((r["amount"] for r in code_rows), Decimal(0)),
                    code=code,
                )
                for code, code_rows in sorted(by_code.items())
            ],
        )
    return found


def mapping_changes(
    mapping: Mapping,
    upload: Upload,
    new: dict[str, str],
    in_force: Upload,
    before: Values,
    approved: Approved,
) -> list[dict]:
    """Return an item for each code whose value differs between the upload's
    mapping and the one in force, with the actual spend approving moves and
    the part of it already committed (ENG-02 8.3 and 11.2).

    This file's postings move when the value they get differs from what the
    mapping in force gives them, and committed postings when their value
    differs from what they have now.
    """
    old = dict(
        mapping.model.objects.filter(upload=in_force).values_list(
            mapping.code, mapping.value
        )
    )
    items = []
    for code in sorted(old.keys() | new.keys()):
        if old.get(code) == new.get(code):
            continue
        moved, committed, rows = Decimal(0), Decimal(0), []
        for (upload_id, posting_code), (value, actual) in approved.values[
            mapping.name
        ].items():
            if posting_code != code:
                continue
            if upload_id == upload.pk:
                if mapping.resolve(old, code) == value:
                    continue
                rows = [
                    (r["sheet"], r["row_number"])
                    for r in approved.rows
                    if r[mapping.posting_code] == code
                ]
            elif before.get((upload_id, code), (value,))[0] == value:
                continue
            else:
                committed += actual
            moved += actual
        items.append(
            item(
                rows,
                moved,
                mapping=mapping.name,
                code=code,
                before=old.get(code),
                after=new.get(code),
                committed=amount_text(committed),
            )
        )
    return items


def replaced_outside_dates(report: Report, result: ReadResult) -> list[dict]:
    """Return an item for each earlier upload with postings approving replaces
    that are dated outside the file's own dates (ENG-02 11.2).
    """
    rows = (
        PostingResolved.objects.filter(
            report=report,
            posting_date__gte=result.period_start.replace(day=1),
            posting_date__lt=first_of_next_month(result.period_end),
        )
        .filter(
            Q(posting_date__lt=result.period_start)
            | Q(posting_date__gt=result.period_end)
        )
        .values("upload__number")
        .annotate(postings=Count("pk"), amount=Sum("amount"))
        .order_by("upload__number")
    )
    return [
        item([], r["amount"], upload=r["upload__number"], postings=r["postings"])
        for r in rows
    ]
