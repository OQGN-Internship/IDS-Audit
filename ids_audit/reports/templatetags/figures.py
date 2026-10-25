"""How amounts, dates, times and months are written on every screen
(ENG-03 2.4): {% load figures %}, then {{ value|amount }} and the others.

Amounts are formatted from Decimal, or from the exact text a preview keeps,
so no amount passes through a float or is rounded.
"""

import datetime
from decimal import Decimal

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def amount(value: Decimal | str | None) -> str:
    """Write an amount in OMR to 3 decimal places, with a comma for thousands
    and a minus for negatives: -1,250.000. Empty for no amount.
    """
    if value is None or value == "":
        return ""
    return f"{Decimal(value):,.3f}"


@register.filter
def day(value: datetime.date | None) -> str:
    """Write a date day first, as SAP does: 03.04.2026."""
    return "" if value is None else f"{value:%d.%m.%Y}"


@register.filter
def moment(value: datetime.datetime | None) -> str:
    """Write a time in Muscat time: 03.04.2026 14:05."""
    return "" if value is None else f"{timezone.localtime(value):%d.%m.%Y %H:%M}"


@register.filter
def month(value: str) -> str:
    """Write a month kept as 2026-03 as Mar 2026 (ENG-03 2.3)."""
    return f"{datetime.date.fromisoformat(value + '-01'):%b %Y}"


@register.filter
def places(rows: list) -> str:
    """Write an exception item's rows, pairs of sheet and row number, by sheet:
    "Transactions: rows 12, 15". A delimited text file's sheet has no name.
    """
    by_sheet: dict[str, list[str]] = {}
    for sheet, number in rows:
        by_sheet.setdefault(sheet, []).append(str(number))
    return "; ".join(
        f"{sheet + ': ' if sheet else ''}row{'s' if len(numbers) > 1 else ''} "
        + ", ".join(numbers)
        for sheet, numbers in by_sheet.items()
    )
