"""Every report figure, added up by PostgreSQL over posting_resolved
(ENG-03 2). No view, template or chart adds amounts itself.

Every function takes the report and the filters (ENG-03 3.1), and those
behind a drill-down also take its keys (6.1), so a figure and the rows behind
it are always chosen by the same conditions. Each runs a fixed number of
queries, however many postings the report has (ENG-03 10).
"""

import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.postgres.aggregates import ArrayAgg
from django.db import models
from django.db.models import (
    Count,
    Exists,
    F,
    Func,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Sum,
    Window,
)
from django.db.models.functions import TruncMonth

from ids_audit.imports.models import SkippedRow, Upload
from ids_audit.ledger.models import CcDeptMap, GlCiMap, PostingResolved
from ids_audit.reports.models import Report

# What a sum over no rows gives, which SQL returns as NULL.
ZERO = Decimal("0.000")

# The catch-all groups' keys are the values their addresses use (ENG-03 2.3
# and 6.1). A clean code is never lower case, so neither is ever a code.
NONE = "none"
OUTSIDE = "outside"
NO_BUDGET_LINE = "No budget line"
NO_DEPARTMENT = "No department"
OUTSIDE_IDS = "Outside IDS"
# Catch-alls come after every code: No budget line or No department, then
# Outside IDS (ENG-03 4).
CATCH_ALL_ORDER = {NONE: 1, OUTSIDE: 2}

# The overview shows this many GL accounts, and one row for the others.
TOP_GL_ACCOUNTS = 10

Status = Upload.Status
CiStatus = PostingResolved.CiStatus


@dataclass(frozen=True)
class Filters:
    """The two filters every figure takes (ENG-03 3.1). The defaults are all
    years, with outside IDS included.
    """

    year: int | None = None
    exclude_outside: bool = False


@dataclass(frozen=True)
class Keys:
    """The keys of the group a drill-down opens (ENG-03 6.1). A key left as
    None doesn't narrow the rows.

    ci is a commitment item or NONE, dept a department, NONE or OUTSIDE, month
    and through the first day of a month, and upload a committed upload's
    number.
    """

    gl: str | None = None
    ci: str | None = None
    cc: str | None = None
    dept: str | None = None
    month: datetime.date | None = None
    through: datetime.date | None = None
    upload: int | None = None


ALL = Filters()
NO_KEYS = Keys()


@dataclass(frozen=True)
class Totals:
    """A report's actual spend in all, inside IDS and outside IDS, and its
    lines (ENG-03 2.2).
    """

    total: Decimal
    inside_ids: Decimal
    outside_ids: Decimal
    lines: int


@dataclass(frozen=True)
class Group:
    """One group's actual spend and lines (ENG-03 2.3).

    key is the group's code, a catch-all's NONE or OUTSIDE, or the first day
    of its month. share is its part of the total under the same filters, as a
    fraction such as 0.124, or None when there's none (2.2). running is a
    month's running total.
    """

    key: str | datetime.date
    name: str
    amount: Decimal
    lines: int
    share: Decimal | None = None
    running: Decimal | None = None


class WindowSum(Func):
    """SUM(...) OVER (...): a sum over the groups of a grouped query.

    Django refuses an aggregate of an aggregate, so this is a plain function
    that only a Window uses. Over the whole query it gives each group the
    total it's a share of, and ordered by month, the running total.
    """

    function = "SUM"
    window_compatible = True
    output_field = models.DecimalField(max_digits=30, decimal_places=3)


def actual_spend(
    report: Report, filters: Filters = ALL, keys: Keys = NO_KEYS
) -> QuerySet[PostingResolved]:
    """Return the rows every figure starts from (ENG-03 2.1): the report's
    postings that count, which posting_resolved already chooses, that are
    actual spend and match the filters and keys.
    """
    rows = PostingResolved.objects.filter(report=report, is_actual=True)
    if filters.year is not None:
        rows = rows.filter(posting_date__year=filters.year)
    if filters.exclude_outside:
        rows = rows.filter(is_outside_ids=False)
    return rows.filter(matching(keys))


def matching(keys: Keys) -> Q:
    """Return the condition the keys of a drill-down put on the rows
    (ENG-03 6.1).
    """
    found = Q()
    if keys.gl is not None:
        found &= Q(gl_code=keys.gl)
    if keys.ci == NONE:
        # ci_code is empty unless the posting's GL account is mapped (ENG-02 8.4).
        found &= Q(ci_code="")
    elif keys.ci is not None:
        found &= Q(ci_code=keys.ci)
    if keys.cc is not None:
        found &= Q(cost_center_code=keys.cc)
    if keys.dept == OUTSIDE:
        found &= Q(is_outside_ids=True)
    elif keys.dept == NONE:
        found &= Q(is_outside_ids=False, dept_code="")
    elif keys.dept is not None:
        found &= Q(dept_code=keys.dept)
    if keys.month is not None:
        found &= Q(
            posting_date__gte=keys.month, posting_date__lt=next_month(keys.month)
        )
    if keys.through is not None:
        found &= Q(posting_date__lt=next_month(keys.through))
    if keys.upload is not None:
        found &= Q(upload__number=keys.upload)
    return found


def next_month(first: datetime.date) -> datetime.date:
    """Return the first day of the month after the one starting on first."""
    return (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def totals(report: Report, filters: Filters = ALL, keys: Keys = NO_KEYS) -> Totals:
    """Return the actual spend in all, inside and outside IDS, and its lines."""
    found = actual_spend(report, filters, keys).aggregate(
        total=Sum("amount"),
        inside_ids=Sum("amount", filter=Q(is_outside_ids=False)),
        outside_ids=Sum("amount", filter=Q(is_outside_ids=True)),
        lines=Count("pk"),
    )
    return Totals(
        total=found["total"] or ZERO,
        inside_ids=found["inside_ids"] or ZERO,
        outside_ids=found["outside_ids"] or ZERO,
        lines=found["lines"],
    )


def share_of(amount: Decimal, total: Decimal | None) -> Decimal | None:
    """Return amount's share of total as a fraction rounded half up to 0.001,
    one decimal place of a percentage, or None when the total is zero or
    negative (ENG-03 2.2).

    The thousandths are divided out exactly, so no rounding happens before
    the one the rule asks for.
    """
    if total is None or total <= 0:
        return None
    thousandths, remainder = divmod(abs(amount) * 1000, total)
    if remainder * 2 >= total:
        thousandths += 1
    share = thousandths / 1000
    return -share if amount < 0 else share


def measures(name: str | None) -> dict:
    """Return what each group shows, each worked out by PostgreSQL: its actual
    spend, its lines, the total of every group, for its share, and its name.

    One code's postings all get their name from the same mapping, unless the
    own_upload rule gives them several, so Max picks the one name, or the name
    that sorts last (ENG-03 2.3).
    """
    found = {
        "amount": Sum("amount"),
        "lines": Count("pk"),
        "total": Window(WindowSum(F("amount"))),
    }
    if name is not None:
        found["name"] = Max(name)
    return found


def by_gl_account(
    report: Report, filters: Filters = ALL, keys: Keys = NO_KEYS
) -> list[Group]:
    """Return the actual spend by GL account. Every posting has one, so there's
    no catch-all.
    """
    rows = (
        actual_spend(report, filters, keys)
        .values("gl_code")
        .annotate(**measures("gl_name"))
    )
    return in_order(
        Group(r["gl_code"], r["name"], r["amount"], r["lines"], share(r)) for r in rows
    )


def by_budget_line(
    report: Report, filters: Filters = ALL, keys: Keys = NO_KEYS
) -> list[Group]:
    """Return the actual spend by budget line. A GL account missing from the
    GL mapping, or listed without a commitment item, has an empty ci_code
    (ENG-02 8.4), so its postings make No budget line.
    """
    rows = (
        actual_spend(report, filters, keys)
        .values("ci_code")
        .annotate(**measures("ci_name"))
    )
    return in_order(
        Group(
            r["ci_code"] or NONE,
            r["name"] if r["ci_code"] else NO_BUDGET_LINE,
            r["amount"],
            r["lines"],
            share(r),
        )
        for r in rows
    )


def by_department(
    report: Report, filters: Filters = ALL, keys: Keys = NO_KEYS
) -> list[Group]:
    """Return the actual spend by department, with Outside IDS for the cost
    centers outside it (ENG-02 8.2), and No department for those inside it
    that the mapping gives no department code (ENG-03 2.3).
    """
    rows = (
        actual_spend(report, filters, keys)
        .values("is_outside_ids", "dept_code")
        .annotate(**measures("dept_name"))
    )
    return in_order(
        Group(
            department_key(r["is_outside_ids"], r["dept_code"]),
            department_name(r["is_outside_ids"], r["dept_code"], r["name"]),
            r["amount"],
            r["lines"],
            share(r),
        )
        for r in rows
    )


def by_month(
    report: Report, filters: Filters = ALL, keys: Keys = NO_KEYS
) -> list[Group]:
    """Return the actual spend by month of posting date, oldest first, each
    with its name, such as Mar 2026, and the running total from the first
    month the filters include (ENG-03 2.2).
    """
    rows = (
        actual_spend(report, filters, keys)
        .annotate(month=TruncMonth("posting_date"))
        .values("month")
        .annotate(amount=Sum("amount"), lines=Count("pk"))
        # A window in the same annotate() as the sums would be grouped by too.
        .annotate(running=Window(WindowSum(F("amount")), order_by=F("month").asc()))
        .order_by("month")
    )
    return [
        Group(
            r["month"],
            f"{r['month']:%b %Y}",
            r["amount"],
            r["lines"],
            None,
            r["running"],
        )
        for r in rows
    ]


def share(row: dict) -> Decimal | None:
    """Return a group's share of the total its row carries."""
    return share_of(row["amount"], row["total"])


def department_key(is_outside_ids: bool, dept_code: str) -> str:
    """Return a department group's key: OUTSIDE, its code, or NONE."""
    if is_outside_ids:
        return OUTSIDE
    return dept_code or NONE


def department_name(is_outside_ids: bool, dept_code: str, name: str) -> str:
    """Return the name a department group shows."""
    if is_outside_ids:
        return OUTSIDE_IDS
    return name if dept_code else NO_DEPARTMENT


def in_order(groups: Iterable[Group]) -> list[Group]:
    """Return the groups in key order, with the catch-alls last."""
    return sorted(groups, key=lambda g: (CATCH_ALL_ORDER.get(g.key, 0), g.key))


def highest_first(groups: Iterable[Group]) -> list[Group]:
    """Return groups as a screen lists them: by actual spend, highest first,
    then by code, with the catch-alls last (ENG-03 4).
    """
    return sorted(
        groups, key=lambda g: (CATCH_ALL_ORDER.get(g.key, 0), -g.amount, g.key)
    )


# The screens' lists (ENG-03 4)


@dataclass(frozen=True)
class GlAccount:
    """A row of the GL accounts screen (ENG-03 4).

    budget_line is the commitment item, NONE, or "" when own_upload counts
    the account under several, which budget_lines counts (2.3).
    """

    key: str
    name: str
    budget_line: str
    budget_line_name: str
    budget_lines: int
    amount: Decimal
    share: Decimal | None
    lines: int
    cost_centers: int


def gl_accounts(report: Report, filters: Filters = ALL) -> list[GlAccount]:
    """Return each GL account with its budget line, actual spend, share, lines
    and number of cost centers, highest first.
    """
    rows = (
        actual_spend(report, filters)
        .values("gl_code")
        .annotate(
            **measures("gl_name"),
            budget_lines=Count("ci_code", distinct=True),
            one_ci=Max("ci_code"),
            one_ci_name=Max("ci_name"),
            cost_centers=Count("cost_center_code", distinct=True),
        )
    )
    found = []
    for r in rows:
        if r["budget_lines"] > 1:
            ci, ci_name = "", f"Several ({r['budget_lines']})"
        elif r["one_ci"]:
            ci, ci_name = r["one_ci"], r["one_ci_name"]
        else:
            ci, ci_name = NONE, NO_BUDGET_LINE
        found.append(
            GlAccount(
                r["gl_code"],
                r["name"],
                ci,
                ci_name,
                r["budget_lines"],
                r["amount"],
                share(r),
                r["lines"],
                r["cost_centers"],
            )
        )
    return sorted(found, key=lambda a: (-a.amount, a.key))


@dataclass(frozen=True)
class TopGlAccounts:
    """The overview's GL accounts: the largest, and the others in one row,
    which is None when there are no others (ENG-03 4).
    """

    largest: list[Group]
    others: Group | None
    others_count: int


def top_gl_accounts(report: Report, filters: Filters = ALL) -> TopGlAccounts:
    """Return the 10 largest GL accounts, and the actual spend of every
    posting outside them, added up by PostgreSQL.
    """
    groups = highest_first(by_gl_account(report, filters))
    largest = groups[:TOP_GL_ACCOUNTS]
    if len(groups) <= TOP_GL_ACCOUNTS:
        return TopGlAccounts(largest, None, 0)
    outside_largest = ~Q(gl_code__in=[g.key for g in largest])
    # Named apart from the field, which a later Sum("amount") would mistake
    # for this aggregate.
    found = actual_spend(report, filters).aggregate(
        others=Sum("amount", filter=outside_largest),
        others_lines=Count("pk", filter=outside_largest),
        accounts=Count("gl_code", distinct=True, filter=outside_largest),
        total=Sum("amount"),
    )
    others = Group(
        "",
        f"All other GL accounts ({found['accounts']})",
        found["others"],
        found["others_lines"],
        share_of(found["others"], found["total"]),
    )
    return TopGlAccounts(largest, others, found["accounts"])


@dataclass(frozen=True)
class CostCenter:
    """A cost center's actual spend under one department (ENG-03 2.3): a cost
    center under several departments has a row for each.
    """

    key: str
    name: str
    dept: str
    dept_name: str
    amount: Decimal
    lines: int


def by_cost_center(
    report: Report, filters: Filters = ALL, keys: Keys = NO_KEYS
) -> list[CostCenter]:
    """Return the actual spend by cost center and department, highest first."""
    rows = (
        actual_spend(report, filters, keys)
        .values("cost_center_code", "is_outside_ids", "dept_code")
        .annotate(
            name=Max("cc_name"),
            department=Max("dept_name"),
            amount=Sum("amount"),
            lines=Count("pk"),
        )
    )
    found = [
        CostCenter(
            r["cost_center_code"],
            r["name"],
            department_key(r["is_outside_ids"], r["dept_code"]),
            department_name(r["is_outside_ids"], r["dept_code"], r["department"]),
            r["amount"],
            r["lines"],
        )
        for r in rows
    ]
    return sorted(found, key=lambda c: (-c.amount, c.key, c.dept))


@dataclass(frozen=True)
class BudgetLine:
    """A row of the budget lines screen: a commitment item, or No budget line,
    with the GL accounts counted under it (ENG-03 4).
    """

    key: str
    name: str
    gl_accounts: list[str]
    amount: Decimal
    lines: int


def budget_lines(report: Report, filters: Filters = ALL) -> list[BudgetLine]:
    """Return each budget line with its GL accounts, highest first, and No
    budget line last.
    """
    rows = (
        actual_spend(report, filters)
        .values("ci_code")
        .annotate(
            name=Max("ci_name"),
            gl_codes=ArrayAgg("gl_code", distinct=True, order_by="gl_code"),
            amount=Sum("amount"),
            lines=Count("pk"),
        )
    )
    found = [
        BudgetLine(
            r["ci_code"] or NONE,
            r["name"] if r["ci_code"] else NO_BUDGET_LINE,
            r["gl_codes"],
            r["amount"],
            r["lines"],
        )
        for r in rows
    ]
    return sorted(
        found, key=lambda b: (CATCH_ALL_ORDER.get(b.key, 0), -b.amount, b.key)
    )


@dataclass(frozen=True)
class GlBudgetLine:
    """A GL account under one budget line, with its actual spend (ENG-03 7.2,
    Q3): a GL account counted under several has a row for each.
    """

    gl: str
    gl_name: str
    ci: str
    ci_name: str
    amount: Decimal
    lines: int


def gl_budget_lines(report: Report, filters: Filters = ALL) -> list[GlBudgetLine]:
    """Return each GL account with each budget line it's counted under, by GL
    account, then by budget line with No budget line last.
    """
    rows = (
        actual_spend(report, filters)
        .values("gl_code", "ci_code")
        .annotate(
            name=Max("gl_name"),
            budget_line=Max("ci_name"),
            amount=Sum("amount"),
            lines=Count("pk"),
        )
    )
    found = [
        GlBudgetLine(
            r["gl_code"],
            r["name"],
            r["ci_code"] or NONE,
            r["budget_line"] if r["ci_code"] else NO_BUDGET_LINE,
            r["amount"],
            r["lines"],
        )
        for r in rows
    ]
    return sorted(found, key=lambda g: (g.gl, CATCH_ALL_ORDER.get(g.ci, 0), g.ci))


def name_of(report: Report, field: str, keys: Keys) -> str:
    """Return the name the mapping in force gives the code the keys choose,
    such as a GL account's gl_name, from all of its actual spend whatever the
    filters, or "" when it has none.
    """
    return actual_spend(report, ALL, keys).aggregate(name=Max(field))["name"] or ""


def years(report: Report) -> list[int]:
    """Return each year in which the report has actual spend, newest first,
    for the year filter's choices (ENG-03 3.1).
    """
    return [
        day.year for day in actual_spend(report).dates("posting_date", "year", "DESC")
    ]


# Drill-down to source rows (ENG-03 6)

ROWS_PER_PAGE = 100


def source_rows(
    report: Report, filters: Filters = ALL, keys: Keys = NO_KEYS
) -> QuerySet[PostingResolved]:
    """Return the postings behind a figure, with their uploads, by posting
    date, then upload number, then sheet and row number (ENG-03 6.2).
    """
    return (
        actual_spend(report, filters, keys)
        .select_related("upload")
        .order_by("posting_date", "upload__number", "sheet", "row_number", "pk")
    )


# Which data a screen shows (ENG-03 3.2 and 9.2)


def latest_committed(report: Report) -> Upload | None:
    """Return the most recently committed upload, which the as-of line names,
    in the order of commits (ENG-02 10.2), or None.
    """
    return (
        report.uploads.filter(status=Status.COMMITTED)
        .select_related("decided_by")
        .order_by("-decided_at", "-number")
        .first()
    )


def awaiting_approval(report: Report) -> Upload | None:
    """Return the upload awaiting approval, if there is one (ENG-02 6)."""
    return report.uploads.filter(status=Status.AWAITING_APPROVAL).first()


def waiting(report: Report) -> Upload | None:
    """Return the upload waiting for someone, awaiting approval or needing its
    columns picked, if there is one. A report has at most one open upload
    (ENG-02 6).
    """
    return (
        report.uploads.filter(
            status__in=(Status.NEEDS_COLUMNS, Status.AWAITING_APPROVAL)
        )
        .order_by("number")
        .first()
    )


@dataclass(frozen=True)
class CommittedUpload:
    """A committed upload, with the lines and actual spend it still counts
    under the filters (ENG-03 9.2).
    """

    upload: Upload
    lines: int
    amount: Decimal


def committed_uploads(report: Report, filters: Filters = ALL) -> list[CommittedUpload]:
    """Return every committed upload in the order of commits (ENG-02 10.2),
    with what it still counts: none for one whose months a later file
    replaced.
    """
    counted = {
        r["upload"]: r
        for r in actual_spend(report, filters)
        .values("upload")
        .annotate(amount=Sum("amount"), lines=Count("pk"))
    }
    uploads = (
        report.uploads.filter(status=Status.COMMITTED)
        .select_related("decided_by", "ruleset")
        .order_by("decided_at", "number")
    )
    return [
        CommittedUpload(
            upload,
            counted.get(upload.pk, {}).get("lines", 0),
            counted.get(upload.pk, {}).get("amount", ZERO),
        )
        for upload in uploads
    ]


# The exceptions screen (ENG-03 8)


@dataclass(frozen=True)
class Unmatched:
    """A GL account with no budget line, or a cost center outside IDS or with
    no department, and its actual spend.

    reason is a GL account's ci_status, and gl_accounts the GL accounts a cost
    center outside IDS used.
    """

    key: str
    name: str
    reason: str
    amount: Decimal
    lines: int
    gl_accounts: list[str]


def no_budget_line(report: Report, filters: Filters = ALL) -> list[Unmatched]:
    """Return each GL account with no budget line, those missing from the GL
    mapping first, then those listed without a commitment item, each part
    highest first.
    """
    rows = (
        actual_spend(report, filters)
        .exclude(ci_status=CiStatus.MAPPED)
        .values("gl_code", "ci_status")
        .annotate(name=Max("gl_name"), amount=Sum("amount"), lines=Count("pk"))
    )
    order = [CiStatus.MISSING_FROM_GL_MAP, CiStatus.LISTED_WITHOUT_CI]
    found = [
        Unmatched(r["gl_code"], r["name"], r["ci_status"], r["amount"], r["lines"], [])
        for r in rows
    ]
    return sorted(found, key=lambda u: (order.index(u.reason), -u.amount, u.key))


def outside_ids(report: Report, filters: Filters = ALL) -> list[Unmatched]:
    """Return each cost center outside IDS, highest first, with the GL
    accounts it used.
    """
    rows = (
        actual_spend(report, filters)
        .filter(is_outside_ids=True)
        .values("cost_center_code")
        .annotate(
            amount=Sum("amount"),
            lines=Count("pk"),
            gl_codes=ArrayAgg("gl_code", distinct=True, order_by="gl_code"),
        )
    )
    found = [
        Unmatched(r["cost_center_code"], "", "", r["amount"], r["lines"], r["gl_codes"])
        for r in rows
    ]
    return sorted(found, key=lambda u: (-u.amount, u.key))


def no_department(report: Report, filters: Filters = ALL) -> list[Unmatched]:
    """Return each cost center inside IDS that its mapping gives no department
    code, highest first.
    """
    rows = (
        actual_spend(report, filters)
        .filter(is_outside_ids=False, dept_code="")
        .values("cost_center_code")
        .annotate(name=Max("cc_name"), amount=Sum("amount"), lines=Count("pk"))
    )
    found = [
        Unmatched(r["cost_center_code"], r["name"], "", r["amount"], r["lines"], [])
        for r in rows
    ]
    return sorted(found, key=lambda u: (-u.amount, u.key))


@dataclass(frozen=True)
class UnmatchedTotals:
    """The totals of the three unmatched lists, how much of the first is also
    in another, and how much is in any of them, counted once (ENG-03 7.2, Q8).
    """

    no_budget_line: Decimal
    outside_ids: Decimal
    no_department: Decimal
    in_both: Decimal
    in_all: Decimal


def unmatched_totals(report: Report, filters: Filters = ALL) -> UnmatchedTotals:
    """Return the unmatched lists' totals, each added up by PostgreSQL."""
    no_ci = ~Q(ci_status=CiStatus.MAPPED)
    # dept_code is empty both outside IDS and for a cost center with no
    # department code (ENG-02 8.4).
    no_dept = Q(dept_code="")
    found = actual_spend(report, filters).aggregate(
        no_budget_line=Sum("amount", filter=no_ci),
        outside_ids=Sum("amount", filter=Q(is_outside_ids=True)),
        no_department=Sum("amount", filter=Q(is_outside_ids=False, dept_code="")),
        in_both=Sum("amount", filter=no_ci & no_dept),
        in_all=Sum("amount", filter=no_ci | no_dept),
    )
    return UnmatchedTotals(**{name: value or ZERO for name, value in found.items()})


@dataclass(frozen=True)
class MapSpec:
    """One of the two mappings, as the changes between files compare them:
    its table, its code and value fields, the value's name, and the posting
    field the code matches.
    """

    model: type[models.Model]
    code: str
    value: str
    value_name: str
    posting_code: str


GL_MAP = MapSpec(GlCiMap, "gl_code", "ci_code", "ci_name", "gl_code")
CC_MAP = MapSpec(CcDeptMap, "cc_code", "dept_code", "dept_name", "cost_center_code")


@dataclass(frozen=True)
class MappedBy:
    """What one committed upload's mapping gave a code, and that upload's
    actual spend on the code that still counts.
    """

    upload: Upload
    value: str
    value_name: str
    amount: Decimal
    lines: int


@dataclass(frozen=True)
class MappingChange:
    """A code whose budget line or department differs between committed
    uploads' mappings, with each upload whose mapping lists it, in the order
    of commits (ENG-03 8).
    """

    code: str
    uploads: list[MappedBy]


def changed_codes(report: Report, spec: MapSpec) -> QuerySet:
    """Return, as a subquery, the codes whose value differs between the
    mappings of the report's committed uploads.
    """
    return (
        spec.model.objects.filter(
            upload__report=report, upload__status=Status.COMMITTED
        )
        .values(spec.code)
        .annotate(values=Count(spec.value, distinct=True))
        .filter(values__gt=1)
        .values(spec.code)
    )


def mapping_changes(
    report: Report, spec: MapSpec, filters: Filters = ALL
) -> list[MappingChange]:
    """Return each code whose value differs between the mappings of the
    report's committed uploads, by code.
    """
    committed = report.uploads.filter(status=Status.COMMITTED)
    changed = changed_codes(report, spec)
    rows = (
        spec.model.objects.filter(upload__in=committed)
        .filter(**{f"{spec.code}__in": changed})
        .select_related("upload")
        .order_by(spec.code, "upload__decided_at", "upload__number")
    )
    spend = {
        (r["upload"], r[spec.posting_code]): r
        for r in actual_spend(report, filters)
        .filter(**{f"{spec.posting_code}__in": changed})
        .values("upload", spec.posting_code)
        .annotate(amount=Sum("amount"), lines=Count("pk"))
    }
    found: dict[str, list[MappedBy]] = {}
    for row in rows:
        code = getattr(row, spec.code)
        counted = spend.get((row.upload_id, code), {})
        found.setdefault(code, []).append(
            MappedBy(
                row.upload,
                getattr(row, spec.value),
                getattr(row, spec.value_name),
                counted.get("amount", ZERO),
                counted.get("lines", 0),
            )
        )
    return [MappingChange(code, uploads) for code, uploads in found.items()]


def mapping_changes_total(
    report: Report, spec: MapSpec, filters: Filters = ALL
) -> tuple[Decimal, int]:
    """Return the actual spend still counting, and its lines, over every row of
    the changes between files: each upload's spend on a changed code its
    mapping lists, added up by PostgreSQL.
    """
    listed = spec.model.objects.filter(
        upload=OuterRef("upload"), **{spec.code: OuterRef(spec.posting_code)}
    )
    found = (
        actual_spend(report, filters)
        .filter(**{f"{spec.posting_code}__in": changed_codes(report, spec)})
        .filter(Exists(listed))
        .aggregate(amount=Sum("amount"), lines=Count("pk"))
    )
    return found["amount"] or ZERO, found["lines"]


@dataclass(frozen=True)
class Skipped:
    """The rows of one committed upload skipped for one reason (ENG-03 8)."""

    upload: int
    file: str
    reason: str
    rows: int
    amount: Decimal | None


def skipped(report: Report) -> list[Skipped]:
    """Return each committed upload's skipped rows by reason, in the order of
    commits. They have no posting date, so no filter applies (ENG-03 8).
    """
    rows = (
        SkippedRow.objects.filter(
            upload__report=report, upload__status=Status.COMMITTED
        )
        .values("upload__number", "upload__original_name", "reason")
        .annotate(rows=Count("pk"), amount=Sum("amount"))
        .order_by("upload__decided_at", "upload__number", "reason")
    )
    return [
        Skipped(
            r["upload__number"],
            r["upload__original_name"],
            r["reason"],
            r["rows"],
            r["amount"],
        )
        for r in rows
    ]


@dataclass(frozen=True)
class SkippedTotal:
    """Every committed upload's skipped rows, and their readable amount."""

    rows: int
    amount: Decimal | None


def skipped_total(report: Report) -> SkippedTotal:
    """Return the number and amount of the skipped rows of every committed
    upload, added up by PostgreSQL. The amount is None when none could be
    read.
    """
    found = SkippedRow.objects.filter(
        upload__report=report, upload__status=Status.COMMITTED
    ).aggregate(rows=Count("pk"), amount=Sum("amount"))
    return SkippedTotal(found["rows"], found["amount"])
