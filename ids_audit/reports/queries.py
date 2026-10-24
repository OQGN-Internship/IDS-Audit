"""Every report figure, added up by PostgreSQL over posting_resolved
(ENG-03 2). No view, template or chart adds amounts itself.

Phase 1 builds the total and the groups in ENG-03 2.3, for its gate. Phase 3
adds the filters, the screens' other figures, the FAQ and drill-down.
"""

import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Max, Q, QuerySet, Sum
from django.db.models.functions import TruncMonth

from ids_audit.ledger.models import PostingResolved
from ids_audit.reports.models import Report

# What a sum over no rows gives, which SQL returns as NULL.
ZERO = Decimal("0.000")


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

    key is the group's code, or the first day of its month. It's None for the
    catch-all group, No budget line or Outside IDS, so it's never taken for a
    code.
    """

    key: str | datetime.date | None
    name: str
    amount: Decimal
    lines: int


def actual_spend(report: Report) -> QuerySet[PostingResolved]:
    """Return the rows every figure starts from (ENG-03 2.1): the report's
    postings that count, which posting_resolved already chooses, and that are
    actual spend.
    """
    return PostingResolved.objects.filter(report=report, is_actual=True)


def totals(report: Report) -> Totals:
    """Return the report's actual spend in all, inside and outside IDS."""
    found = actual_spend(report).aggregate(
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


def by_gl_account(report: Report) -> list[Group]:
    """Return the actual spend by GL account. Every posting has one, so there's
    no catch-all.
    """
    rows = actual_spend(report).values("gl_code").annotate(**measures("gl_name"))
    return in_order(
        Group(r["gl_code"], r["name"], r["amount"], r["lines"]) for r in rows
    )


def by_budget_line(report: Report) -> list[Group]:
    """Return the actual spend by budget line. A GL account missing from the
    GL mapping, or listed without a commitment item, has an empty ci_code
    (ENG-02 8.4), so its postings make No budget line.
    """
    rows = actual_spend(report).values("ci_code").annotate(**measures("ci_name"))
    return in_order(
        Group(r["ci_code"] or None, r["name"], r["amount"], r["lines"]) for r in rows
    )


def by_department(report: Report) -> list[Group]:
    """Return the actual spend by department, with Outside IDS for the cost
    centers outside it (ENG-02 8.2).

    Grouping by is_outside_ids too keeps Outside IDS apart from a cost center
    inside IDS that the mapping gives no department code.
    """
    rows = (
        actual_spend(report)
        .values("is_outside_ids", "dept_code")
        .annotate(**measures("dept_name"))
    )
    return in_order(
        Group(
            None if r["is_outside_ids"] else r["dept_code"],
            r["name"],
            r["amount"],
            r["lines"],
        )
        for r in rows
    )


def by_month(report: Report) -> list[Group]:
    """Return the actual spend by month of posting date. A month's name, such
    as Mar 2026, is written from its key where it's shown (ENG-03 2.3).
    """
    rows = (
        actual_spend(report)
        .annotate(month=TruncMonth("posting_date"))
        .values("month")
        .annotate(**measures(None))
    )
    return in_order(Group(r["month"], "", r["amount"], r["lines"]) for r in rows)


def measures(name: str | None) -> dict:
    """Return what each group shows: its name, its actual spend and its lines,
    each worked out by PostgreSQL.

    One code's postings all get their name from the same mapping, unless the
    own_upload rule gives them several, so Max picks the one name, or the name
    that sorts last (ENG-03 2.3).
    """
    found = {"amount": Sum("amount"), "lines": Count("pk")}
    if name is not None:
        found["name"] = Max(name)
    return found


def in_order(groups: Iterable[Group]) -> list[Group]:
    """Return the groups in key order, with the catch-all last."""
    return sorted(groups, key=lambda group: (group.key is None, group.key or ""))
