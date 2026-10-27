"""Reading the filters and drill-down keys from a screen's address, and
writing them back into the addresses it links to (ENG-03 3.1 and 6.1).

A value outside its form, a key given twice, or a key the screen doesn't take
is refused with 400 Bad Request. The app only builds valid addresses, so a
wrong one has been typed or edited.
"""

import datetime
import re
from dataclasses import fields
from urllib.parse import urlencode

from django.core.exceptions import BadRequest
from django.http import QueryDict

from ids_audit.pipeline.cleaning import clean_code
from ids_audit.reports.queries import ALL, NO_KEYS, NONE, OUTSIDE, Filters, Keys

FILTER_KEYS = ("year", "exclude_outside")
# The keys of a drill-down to source rows, and its page (ENG-03 6.1).
ROW_KEYS = ("gl", "ci", "cc", "dept", "month", "through", "upload", "page")

YEAR = re.compile(r"[1-9][0-9]{3}")
MONTH = re.compile(r"([1-9][0-9]{3})-(0[1-9]|1[0-2])")
WHOLE = re.compile(r"[1-9][0-9]*")


def read(query: QueryDict, extra: tuple[str, ...] = ()) -> dict[str, str]:
    """Return the address's values by key, refusing a key given twice or one
    that isn't a filter or one of the screen's own keys.
    """
    allowed = set(FILTER_KEYS) | set(extra)
    unknown = set(query) - allowed
    if unknown:
        raise BadRequest(f"Not a key of this screen: {', '.join(sorted(unknown))}.")
    twice = [key for key in query if len(query.getlist(key)) > 1]
    if twice:
        raise BadRequest(f"Given twice: {', '.join(sorted(twice))}.")
    return {key: query[key] for key in query}


def filters_from(values: dict[str, str]) -> Filters:
    """Return the filters an address gives, each left out taking its default."""
    year = values.get("year", "all")
    if year != "all" and not YEAR.fullmatch(year):
        raise BadRequest("The year is four digits, or all.")
    exclude = values.get("exclude_outside", "0")
    if exclude not in ("0", "1"):
        raise BadRequest("exclude_outside is 0 or 1.")
    return Filters(
        year=None if year == "all" else int(year), exclude_outside=exclude == "1"
    )


def code(value: str, catch_alls: tuple[str, ...] = ()) -> str:
    """Return a code given as cleaned (ENG-02 5), or one of the catch-all
    values the key takes, refusing anything else.
    """
    if value in catch_alls:
        return value
    try:
        cleaned = clean_code(value)
    except ValueError:
        cleaned = None
    if not value or cleaned != value:
        raise BadRequest(f"{value!r} isn't a code as cleaned.")
    return value


def month(value: str) -> datetime.date:
    """Return the first day of a month given as 2026-03."""
    found = MONTH.fullmatch(value)
    if not found:
        raise BadRequest("A month is written as 2026-03.")
    return datetime.date(int(found[1]), int(found[2]), 1)


def whole(value: str) -> int:
    """Return a whole number from 1."""
    if not WHOLE.fullmatch(value):
        raise BadRequest(f"{value!r} isn't a whole number from 1.")
    return int(value)


def keys_from(values: dict[str, str]) -> Keys:
    """Return the drill-down keys an address gives (ENG-03 6.1)."""
    if "month" in values and "through" in values:
        raise BadRequest("month and through can't be given together.")
    found = {}
    if "gl" in values:
        found["gl"] = code(values["gl"])
    if "ci" in values:
        found["ci"] = code(values["ci"], (NONE,))
    if "cc" in values:
        found["cc"] = code(values["cc"])
    if "dept" in values:
        found["dept"] = code(values["dept"], (NONE, OUTSIDE))
    for key in ("month", "through"):
        if key in values:
            found[key] = month(values[key])
    if "upload" in values:
        found["upload"] = whole(values["upload"])
    return Keys(**found)


def page_from(values: dict[str, str]) -> int:
    """Return the page of source rows asked for, from 1."""
    return whole(values["page"]) if "page" in values else 1


def key_values(keys: Keys) -> dict[str, str]:
    """Return the drill-down keys given, as they're written in an address."""
    values: dict[str, str] = {}
    for field in fields(Keys):
        value = getattr(keys, field.name)
        if isinstance(value, datetime.date):
            values[field.name] = f"{value:%Y-%m}"
        elif value is not None:
            values[field.name] = str(value)
    return values


def query(filters: Filters = ALL, keys: Keys = NO_KEYS, **extra: object) -> str:
    """Return the query string for the filters, keys and any other values,
    such as ?gl=610100&year=2026. A filter at its default is left out.
    """
    values = key_values(keys)
    values.update({key: str(value) for key, value in extra.items()})
    if filters.year is not None:
        values["year"] = str(filters.year)
    if filters.exclude_outside:
        values["exclude_outside"] = "1"
    return f"?{urlencode(values)}" if values else ""


def tags(filters: Filters, outside_applies: bool = True) -> list[str]:
    """Return each filter in words, as a screen's tags show them, such as
    "Year: 2026" and "Outside IDS: excluded" (ENG-03 3.1).
    """
    year = "all" if filters.year is None else str(filters.year)
    if not outside_applies:
        outside = "included, since this filter doesn't apply here"
    elif filters.exclude_outside:
        outside = "excluded"
    else:
        outside = "included"
    return [f"Year: {year}", f"Outside IDS: {outside}"]


def in_words(filters: Filters, outside_applies: bool = True) -> str:
    """Return the filters as one line, such as "Year: 2026. Outside IDS:
    excluded.", as an export's Source sheet states them (ENG-03 3.1).
    """
    return ". ".join(tags(filters, outside_applies)) + "."


def keys_in_words(keys: Keys) -> str:
    """Return the keys that chose a list of source rows, such as "GL account
    610100, cost center 4510" (ENG-03 6.2).
    """
    parts = []
    if keys.gl is not None:
        parts.append(f"GL account {keys.gl}")
    if keys.ci == NONE:
        parts.append("No budget line")
    elif keys.ci is not None:
        parts.append(f"budget line {keys.ci}")
    if keys.cc is not None:
        parts.append(f"cost center {keys.cc}")
    if keys.dept == OUTSIDE:
        parts.append("Outside IDS")
    elif keys.dept == NONE:
        parts.append("No department")
    elif keys.dept is not None:
        parts.append(f"department {keys.dept}")
    if keys.month is not None:
        parts.append(f"{keys.month:%b %Y}")
    if keys.through is not None:
        parts.append(f"every month up to {keys.through:%b %Y}")
    if keys.upload is not None:
        parts.append(f"upload {keys.upload}")
    if not parts:
        return "All actual spend"
    text = ", ".join(parts)
    return text[0].upper() + text[1:]
