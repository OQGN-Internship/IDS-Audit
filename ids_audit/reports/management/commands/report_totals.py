"""manage.py report_totals: every report's totals as JSON (ENG-01 7.2).

The backup saves its output as totals.json, and a restore compares a new run
with it, so the same data must always print the same text.
"""

import json
from decimal import Decimal

from django.core.management.base import BaseCommand

from ids_audit.reports import queries
from ids_audit.reports.models import Report


class Command(BaseCommand):
    help = "Print every report's totals as JSON, from reports/queries.py."

    def handle(self, *args, **options) -> None:
        reports = [report_totals(report) for report in Report.objects.order_by("pk")]
        self.stdout.write(json.dumps({"reports": reports}, indent=2, sort_keys=True))


def report_totals(report: Report) -> dict:
    """Return one report's totals, named as in the golden files' expected
    results (ENG-02 12.3), with the default filters (ENG-03 3.1).
    """
    totals = queries.totals(report)
    return {
        "report": report.pk,
        "name": report.name,
        "actual_spend": {
            "total": text(totals.total),
            "inside_ids": text(totals.inside_ids),
            "outside_ids": text(totals.outside_ids),
        },
        "lines": totals.lines,
        "by_gl": amounts(queries.by_gl_account(report)),
        "by_ci": amounts(queries.by_budget_line(report)),
        "by_department": amounts(queries.by_department(report)),
        "by_month": {
            group.key.strftime("%Y-%m"): text(group.amount)
            for group in queries.by_month(report)
        },
    }


def amounts(groups: list[queries.Group]) -> dict[str, str]:
    """Return each group's actual spend by its key. A catch-all's key is none
    or outside, as in the golden files and the addresses (ENG-03 2.3).
    """
    return {group.key: text(group.amount) for group in groups}


def text(amount: Decimal) -> str:
    """Write an amount with exactly three decimal places, never as a float."""
    return f"{amount:.3f}"
