"""Cleaning one cell's value, field by field (ENG-02 5).

Each function takes a cell as a reader gives it (readers.py) and returns the
clean value. An empty cell gives "" or None, and a value that can't be cleaned
raises ValueError: it's never guessed. Which reason a row is skipped with is
decided by the caller, from which field failed.

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

import datetime
import re
from collections.abc import Mapping
from decimal import Decimal

from ids_audit.pipeline.readers import Cell

# A clean code holds only these, after trimming (ENG-02 5).
CODE = re.compile(r"[A-Za-z0-9._-]+")
DIGITS = re.compile(r"[0-9]+")

# Amounts are kept to 3 decimal places (baisa), and numeric(18,3) holds at most
# 15 digits before the decimal point.
BAISA = Decimal("0.001")
TOO_LARGE = Decimal(10) ** 15

# Spaces that may separate thousands: a plain space, a no-break space and a
# narrow no-break space, which Excel and SAP use in some notations.
SPACES = "   "

# The characters that may separate thousands, for each decimal separator.
THOUSANDS = {".": "," + SPACES, ",": "." + SPACES}

# Excel counts days from 30 December 1899, and this range of day numbers runs
# from 1954 to 2119, so a number in it in a date column is a date (ENG-02 5).
EXCEL_EPOCH = datetime.date(1899, 12, 30)
SERIAL_RANGE = range(20000, 80001)

# dd.mm.yyyy, dd/mm/yyyy and dd-mm-yyyy (or month first), with one separator
# used twice, and yyyy-mm-dd.
DAY_MONTH_YEAR = re.compile(r"([0-9]{1,2})([./-])([0-9]{1,2})\2([0-9]{4}|[0-9]{2})")
YEAR_MONTH_DAY = re.compile(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})")


def cell_text(cell: Cell) -> str:
    """Return a cell as trimmed text. A number is written in its shortest form,
    without ".0" when it's whole, and a date as yyyy-mm-dd.
    """
    if isinstance(cell, str):
        return cell.strip()
    if isinstance(cell, float):
        return str(int(cell)) if cell.is_integer() else repr(cell)
    if isinstance(cell, datetime.date | datetime.time):
        return cell.isoformat()
    return str(cell)


def clean_code(cell: Cell) -> str:
    """Return a code as text: trimmed, upper-cased, and without leading zeros if
    it's all digits. "" for an empty cell.

    A number Excel stored as 4510.0 becomes "4510". Anything else that isn't
    letters, digits, "-", "_" and ".", or a number that isn't whole, raises
    ValueError.
    """
    if isinstance(cell, bool):
        raise ValueError("a true or false cell isn't a code")
    if isinstance(cell, float):
        if not cell.is_integer():
            raise ValueError("a number that isn't whole isn't a code")
        cell = int(cell)
    if isinstance(cell, int):
        text = str(cell)
    elif isinstance(cell, str):
        text = cell.strip()
    else:
        raise ValueError("a date or time isn't a code")
    if text == "":
        return ""
    if not CODE.fullmatch(text):
        raise ValueError(f"{text!r} isn't a code")
    if DIGITS.fullmatch(text):
        return text.lstrip("0") or "0"
    return text.upper()


def clean_whole_number(cell: Cell) -> int | None:
    """Return a fiscal year or period as a whole number, or None for an empty
    cell. 2026.0 becomes 2026, and "003" becomes 3.
    """
    if isinstance(cell, bool):
        raise ValueError("a true or false cell isn't a number")
    if isinstance(cell, int):
        return cell
    if isinstance(cell, float):
        if not cell.is_integer():
            raise ValueError("the number isn't whole")
        return int(cell)
    if isinstance(cell, str):
        text = cell.strip()
        if text == "":
            return None
        if DIGITS.fullmatch(text):
            return int(text)
    raise ValueError(f"{cell!r} isn't a whole number")


def clean_amount(cell: Cell, decimal_separator: str) -> Decimal | None:
    """Return an amount exactly as written, or None for an empty cell.

    It isn't rounded: whether it has more than 3 decimal places is the caller's
    check, since a skipped row keeps its amount as read (ENG-02 5).
    """
    if isinstance(cell, bool):
        raise ValueError("a true or false cell isn't an amount")
    if isinstance(cell, int):
        amount = Decimal(cell)
    elif isinstance(cell, float):
        # Through the shortest text that gives the same float, so 1234.5 is
        # 1234.5 and never 1234.499999999999886...
        amount = Decimal(repr(cell))
    elif isinstance(cell, str):
        if cell.strip() == "":
            return None
        amount = parse_text_amount(cell, decimal_separator)
    else:
        raise ValueError("a date or time isn't an amount")
    if not amount.is_finite() or abs(amount) >= TOO_LARGE:
        raise ValueError("the amount doesn't fit numeric(18,3)")
    # -0.000 is 0.000.
    return abs(amount) if amount == 0 else amount


def parse_text_amount(text: str, decimal_separator: str) -> Decimal:
    """Read an amount written as text, such as "1,234.500-", "(300.000)" or
    "OMR 1,200", with the given decimal separator (ENG-02 5).
    """
    # Currency letters at either end go, with the spaces beside them.
    text = re.sub(r"^[A-Za-z]+|[A-Za-z]+$", "", text.strip()).strip(SPACES)
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip(SPACES)
    elif text.endswith("-"):
        negative, text = True, text[:-1].rstrip(SPACES)
    elif text.startswith("-"):
        negative, text = True, text[1:].lstrip(SPACES)
    if not AMOUNT_PATTERNS[decimal_separator].fullmatch(text):
        raise ValueError(f"{text!r} isn't an amount")
    digits = re.sub(f"[{re.escape(THOUSANDS[decimal_separator])}]", "", text)
    amount = Decimal(digits.replace(decimal_separator, "."))
    return -amount if negative else amount


def amount_pattern(decimal_separator: str) -> re.Pattern[str]:
    """Return the pattern a text amount must match once its sign is gone:
    digits, grouped by threes with one thousands separator used throughout or
    not grouped at all, then the decimal separator and digits, if any.
    """
    group = f"[{re.escape(THOUSANDS[decimal_separator])}]"
    decimals = re.escape(decimal_separator)
    return re.compile(
        rf"(?:[0-9]{{1,3}}(?P<g>{group})[0-9]{{3}}(?:(?P=g)[0-9]{{3}})*|[0-9]+)"
        rf"(?:{decimals}[0-9]+)?"
    )


AMOUNT_PATTERNS = {separator: amount_pattern(separator) for separator in THOUSANDS}


def has_more_than_3_decimals(amount: Decimal) -> bool:
    """Return True when the amount needs a fourth decimal place, not counting
    zeros at the end: 12.3456 does, and 12.3450 doesn't.
    """
    return amount != amount.quantize(BAISA)


def clean_date(cell: Cell, date_order: str) -> datetime.date | None:
    """Return a posting date, or None for an empty cell (ENG-02 5).

    A date cell is used without its time of day. A number between 20000 and
    80000, or text of digits in that range, is an Excel day number. Text is
    dd.mm.yyyy, dd/mm/yyyy or dd-mm-yyyy in the ruleset's date_order, or
    yyyy-mm-dd. A two-digit year means 20yy.
    """
    if isinstance(cell, datetime.datetime):
        return cell.date()
    if isinstance(cell, datetime.date):
        return cell
    # True and False are 1 and 0 here, which aren't Excel day numbers.
    if isinstance(cell, int | float):
        return excel_day(int(cell))
    if not isinstance(cell, str):
        raise ValueError("a time isn't a date")
    text = cell.strip()
    if text == "":
        return None
    if DIGITS.fullmatch(text):
        return excel_day(int(text))
    if match := DAY_MONTH_YEAR.fullmatch(text):
        first, _, second, year = match.groups()
        day, month = (first, second) if date_order == "day_first" else (second, first)
        full_year = int(year) + 2000 if len(year) == 2 else int(year)
        return datetime.date(full_year, int(month), int(day))
    if match := YEAR_MONTH_DAY.fullmatch(text):
        year, month, day = match.groups()
        return datetime.date(int(year), int(month), int(day))
    raise ValueError(f"{text!r} isn't a date")


def excel_day(number: int) -> datetime.date:
    """Return the date of an Excel day number, such as 46082 for 1 March 2026."""
    if number not in SERIAL_RANGE:
        raise ValueError(f"{number} isn't an Excel day number")
    return EXCEL_EPOCH + datetime.timedelta(days=number)


def date_from_period(fiscal_year: int | None, period: int | None) -> datetime.date:
    """Return the date for a fiscal year and period, used when a file has no
    date column. Periods 1 to 12 are the first day of the month, and 13 to 16,
    SAP's special periods, are 31 December.
    """
    if fiscal_year is None or period is None:
        raise ValueError("the fiscal year or the period is missing")
    if 1 <= period <= 12:
        return datetime.date(fiscal_year, period, 1)
    if 13 <= period <= 16:
        return datetime.date(fiscal_year, 12, 31)
    raise ValueError(f"{period} isn't a period")


def classify_value_type(value_type: str, rule: Mapping | None) -> bool | None:
    """Return True for actual spend, False for spend that isn't actual, and
    None for a value the ruleset's actual_spend_rule doesn't list (ENG-02 5).
    With the rule None, every row is actual spend.
    """
    if rule is None:
        return True
    value = value_type.strip().lower()
    if value in {listed.strip().lower() for listed in rule["actual"]}:
        return True
    if value in {listed.strip().lower() for listed in rule["not_actual"]}:
        return False
    return None
