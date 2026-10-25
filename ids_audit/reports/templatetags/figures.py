"""How amounts, dates, times and months are written on every screen
(ENG-03 2.4): {% load figures %}, then {{ value|amount }} and the others.

Amounts are formatted from Decimal, or from the exact text a preview keeps,
so no amount passes through a float or is rounded.
"""

import datetime
from decimal import Decimal

from django import template
from django.utils import timezone
from django.utils.html import format_html

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


@register.filter
def share(value: Decimal | None) -> str:
    """Write a share, kept as a fraction such as 0.124, as 12.4%, or – when
    there's none (ENG-03 2.2).
    """
    return "–" if value is None else f"{value * 100:.1f}%"


def shown(cell) -> str:
    """Write a table cell as its kind says (ENG-03 2.4). An exact amount, such
    as a skipped row's, is written as read, never rounded.
    """
    value = cell.value
    if cell.kind == "amount":
        return amount(value)
    if cell.kind == "exact":
        return "" if value is None else f"{value:,f}"
    if cell.kind == "share":
        return share(value)
    if cell.kind == "date":
        return day(value)
    return "" if value is None else str(value)


@register.simple_tag
def td(cell) -> str:
    """Write a table cell, escaped, with its link if it has one. Item text has
    its direction set automatically, so Arabic reads right to left (ENG-03
    6.2).
    """
    if cell.kind == "item":
        return format_html('<td class="item" dir="auto">{}</td>', shown(cell))
    if cell.link:
        return format_html(
            '<td class="{}"><a href="{}">{}</a></td>', cell.kind, cell.link, shown(cell)
        )
    return format_html('<td class="{}">{}</td>', cell.kind, shown(cell))
