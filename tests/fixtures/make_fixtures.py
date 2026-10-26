# ruff: noqa: E501
# The spend table keeps one posting per line, so it reads like the sheet.
"""Generate the sample SAP export and its expected results (ENG-02 12.3).

Every value here is made up. The script writes the spend lines the way an
SAP FAGLL03 export does, and works out the expected results from the same
values with Decimal, never by reading its own files back.

Run from the repository root:

    uv run python -m tests.fixtures.make_fixtures
"""

import csv
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from itertools import groupby
from pathlib import Path

import xlsxwriter
from xlsxwriter.worksheet import Worksheet

COMPANY_CODE = "1000"
PERIOD_START = date(2025, 7, 1)
PERIOD_END = date(2026, 6, 30)


@dataclass(frozen=True)
class GlAccount:
    """A general-ledger account and its optional commitment item."""

    name: str
    commitment_item: str | None  # None: listed without a commitment item


@dataclass(frozen=True)
class CostCenter:
    """A cost center and its associated department."""

    name: str
    department: str


@dataclass(frozen=True)
class Posting:
    """One spend line, as true values. The writers add SAP's formatting."""

    gl: str  # "610100"; written as 0000610100
    doc_number: str  # "100000045"; written as 0100000045
    line_item: int  # 2; written as 002
    doc_type: str  # KR invoice, KG credit memo, SA journal
    doc_date: date
    posting_date: date  # also gives Year and Period
    cost_center: str  # "4510"; written as 0000004510
    reference: str
    amount: Decimal  # negative for credits
    text: str


COMMITMENT_ITEMS = {
    "2101": "Software and licences",
    "2201": "Hardware",
    "2301": "Telecom and network",
    "2401": "Professional services",
    "2501": "Training and other",
}


# 640200 is left out on purpose: missing from the GL mapping.
GL_ACCOUNTS = {
    "610100": GlAccount("Software licences", "2101"),
    "610200": GlAccount("Software support", "2101"),
    "610300": GlAccount("Cloud services", "2101"),
    "620100": GlAccount("Hardware purchases", "2201"),
    "620200": GlAccount("Hardware maintenance", "2201"),
    "630100": GlAccount("Telecommunications", "2301"),
    "630200": GlAccount("Data lines", "2301"),
    "640100": GlAccount("IT consulting", "2401"),
    "650100": GlAccount("IT training", "2501"),
    "650200": GlAccount("Conferences and travel", None),
    "660100": GlAccount("IT supplies", "2501"),
}


DEPARTMENTS = {
    "APP": "Applications",
    "INF": "Infrastructure",
    "SEC": "Cyber security",
    "GOV": "IT governance",
}

# 3100 and 3200 are left out on purpose: outside IDS.
COST_CENTERS = {
    "4510": CostCenter("Application development", "APP"),
    "4520": CostCenter("Enterprise applications", "APP"),
    "4530": CostCenter("Data center", "INF"),
    "4540": CostCenter("Network", "INF"),
    "4550": CostCenter("Security operations", "SEC"),
    "4560": CostCenter("IT governance office", "GOV"),
}

# fmt: off
_ROWS = [
    # G/L     DocumentNo    Itm Type Doc. date     Posting date  CCtr    Reference    Amount         Text
    ("610100", "5100000101", 2, "KR", "2025-07-03", "2025-07-08", "4520", "INV-70112", "1150000.000", "ERP licence renewal"),
    ("610100", "5100000140", 2, "KR", "2025-09-10", "2025-09-15", "3100", "INV-71530", "8750.500", "Finance reporting licences"),
    ("610100", "5100000123", 2, "KR", "2025-12-28", "2026-01-04", "4510", "INV-88231", "12400.000", "Licence renewal"),
    ("610100", "5100000210", 2, "KG", "2026-01-20", "2026-01-22", "4510", "CN-88231", "-1250.000", "Licence overcharge refund"),
    ("610100", "5100000188", 2, "KR", "2026-02-02", "2026-02-05", "3100", "INV-90417", "2340.250", "Licence true-up"),
    ("610100", "5100000201", 2, "KR", "2026-03-08", "2026-03-10", "4550", "INV-91822", "23456.789", "تجديد ترخيص البرمجيات"),
    ("610200", "5100000110", 2, "KR", "2025-07-20", "2025-07-24", "4520", "INV-70588", "45600.000", "ERP support, Q3"),
    ("610200", "5100000150", 2, "KR", "2025-10-15", "2025-10-20", "4520", "INV-74410", "45600.000", "ERP support, Q4"),
    ("610200", "5100000190", 2, "KR", "2026-01-14", "2026-01-19", "4520", "INV-89012", "45600.000", "ERP support, Q1"),
    ("610200", "5100000195", 2, "KG", "2026-02-10", "2026-02-12", "4520", "CN-89012", "-3800.000", "Service credit, Q1 outage"),
    ("610200", "5100000230", 2, "KR", "2026-04-12", "2026-04-15", "4520", "INV-92761", "45600.000", "ERP support, Q2"),
    ("610300", "5100000123", 2, "KR", "2025-08-10", "2025-08-14", "4510", "INV-71004", "6234.567", "Cloud hosting, August"),
    ("610300", "5100000220", 2, "KR", "2026-05-03", "2026-05-06", "4510", "INV-93350", "6890.120", "Cloud hosting, May"),
    ("620100", "5100000130", 2, "KR", "2025-09-01", "2025-09-03", "4530", "INV-72215", "87500.000", "Server purchase"),
    ("620100", "5100000240", 2, "KR", "2026-04-01", "2026-04-03", "4540", "INV-92001", "31275.400", "Network switches"),
    ("620200", "5100000160", 2, "KR", "2025-11-02", "2025-11-05", "4540", "INV-75520", "2000.000", "صيانة أجهزة الشبكة"),
    ("620200", "5100000250", 2, "KG", "2026-05-18", "2026-05-20", "4540", "CN-75520", "-3500.000", "Maintenance credit, unused hours"),
    ("630100", "5100000115", 2, "KR", "2025-07-28", "2025-07-31", "4540", "INV-70901", "4120.350", "Mobile and fixed lines, July"),
    ("630100", "100000045", 1, "SA", "2025-10-30", "2025-10-30", "4540", "", "1200.000", "Reclass to network"),
    ("630100", "100000045", 2, "SA", "2025-10-30", "2025-10-30", "4530", "", "-1200.000", "Reclass to network"),
    ("630100", "5100000215", 2, "KR", "2026-03-25", "2026-03-29", "4540", "INV-91990", "4388.125", "Mobile and fixed lines, March"),
    ("630200", "5100000170", 2, "KR", "2025-12-01", "2025-12-04", "4530", "INV-77340", "9800.000", "Data line, data center"),
    ("630200", "5100000170", 3, "KR", "2025-12-01", "2025-12-04", "4540", "INV-77340", "5400.000", "Data line, branch offices"),
    ("630200", "5100000260", 2, "KR", "2026-06-08", "2026-06-10", "4540", "INV-94120", "7450.800", "Data lines, June"),
    ("640100", "5100000135", 2, "KR", "2025-09-18", "2025-09-22", "4560", "INV-72870", "18750.000", "IT strategy review"),
    ("640100", "5100000205", 2, "KR", "2026-03-01", "2026-03-04", "4550", "INV-91100", "26400.000", "Penetration test"),
    ("640200", "5100000145", 2, "KR", "2025-10-05", "2025-10-08", "4510", "INV-73990", "15200.000", "Contract developer, September"),
    ("640200", "5100000180", 2, "KR", "2025-12-14", "2025-12-17", "4520", "INV-77980", "14880.000", "Contract developer, November"),
    ("640200", "5100000225", 2, "KR", "2026-05-10", "2026-05-12", "3200", "INV-93010", "870.000", "Contractor access cards"),
    ("650100", "5100000175", 2, "KR", "2025-11-20", "2025-11-24", "4550", "INV-76610", "5600.000", "دورة تدريبية في الأمن السيبراني"),
    ("650100", "5100000245", 2, "KR", "2026-04-20", "2026-04-22", "4560", "INV-92880", "3150.750", "Project management course"),
    ("650200", "5100000155", 2, "KR", "2025-10-22", "2025-10-26", "4560", "INV-74900", "2780.000", "Technology conference"),
    ("650200", "5100000235", 2, "KR", "2026-04-26", "2026-04-29", "4510", "INV-92990", "1945.300", "Developer conference"),
    ("660100", "5100000165", 2, "KR", "2025-11-10", "2025-11-12", "4560", "INV-75800", "640.500", "Printer toner"),
    ("660100", "5100000255", 2, "KR", "2026-06-15", "2026-06-18", "4530", "INV-94400", "1290.000", "Cables and adapters"),
]
# fmt: on


POSTINGS = [
    Posting(
        gl=gl,
        doc_number=doc_number,
        line_item=line_item,
        doc_type=doc_type,
        doc_date=date.fromisoformat(doc_date),
        posting_date=date.fromisoformat(posting_date),
        cost_center=cost_center,
        reference=reference,
        amount=Decimal(amount),
        text=text,
    )
    for (
        gl,
        doc_number,
        line_item,
        doc_type,
        doc_date,
        posting_date,
        cost_center,
        reference,
        amount,
        text,
    ) in _ROWS
]

FIXTURES_DIR = Path(__file__).parent
CREATED = datetime(2026, 1, 1)  # fixed, so every run writes the same bytes

README_LINES = [
    "IDS Spend Audit sample file. Every value in it is made up.",
    "It imitates an SAP G/L line item export (FAGLL03), with the GL and cost center mapping sheets added.",
    "Made by tests/fixtures/make_fixtures.py. To make it again, run: uv run python -m tests.fixtures.make_fixtures",
    "GL account 640200 is missing from the GL Mapping sheet, so it has no budget line.",
    "GL account 650200 is listed without a commitment item.",
    "Cost centers 3100 and 3200 are missing from the Cost Center Mapping sheet, so they are outside IDS.",
    "A subtotal row follows each GL account on the Transactions sheet. Subtotals are not spend.",
    "Its expected result is sample.expected.json, beside this file. See ENG-02 12.3.",
]

HEADER = [
    "CoCd",
    "G/L Acct",
    "DocumentNo",
    "Line Item",
    "Type",
    "Doc. Date",
    "Pstng Date",
    "Year",
    "Period",
    "Cost Ctr",
    "Reference",
    "Text",
    "Amount in LC",
    "LCurr",
]

# A cell is text (str), a number (int) or empty (None).
Cells = list[str | int | None]


def sap_code(code: str) -> str:
    """'610100' -> '0000610100'."""
    return code.zfill(10)


def sap_amount(amount: Decimal) -> str:
    """Decimal('-1250.000') -> '1,250.000-', SAP's trailing minus."""
    text = f"{abs(amount):,.3f}"
    return text + "-" if amount < 0 else text


def sap_date(day: date) -> str:
    """date(2026, 1, 4) -> '04.01.2026'."""
    return day.strftime("%d.%m.%Y")


def transactions_rows() -> list[tuple[str, Posting | None, Cells]]:
    """The Transactions sheet, top to bottom, as (kind, posting, cells).

    The position in the list plus 1 is the Excel row number. The xlsx and
    CSV writers write these rows, and the expected result reads row numbers
    from them, so all three share one layout.
    """
    period = f"{sap_date(PERIOD_START)} to {sap_date(PERIOD_END)}"
    rows: list[tuple[str, Posting | None, Cells]] = [
        ("title", None, ["G/L Account Line Item Display"]),
        ("title", None, [f"Company code {COMPANY_CODE}, posting date {period}"]),
        ("blank", None, []),
        ("header", None, list(HEADER)),
    ]
    ordered = sorted(
        POSTINGS, key=lambda p: (p.gl, p.posting_date, p.doc_number, p.line_item)
    )
    for gl, group in groupby(ordered, key=lambda p: p.gl):
        postings = list(group)
        for p in postings:
            cells: Cells = [
                COMPANY_CODE,
                sap_code(p.gl),
                sap_code(p.doc_number),
                f"{p.line_item:03d}",
                p.doc_type,
                sap_date(p.doc_date),
                sap_date(p.posting_date),
                p.posting_date.year,
                p.posting_date.month,
                sap_code(p.cost_center),
                p.reference,
                p.text,
                sap_amount(p.amount),
                "OMR",
            ]
            rows.append(("spend", p, cells))
        subtotal = sum((p.amount for p in postings), Decimal("0.000"))
        cells = [None, sap_code(gl)] + [None] * 10 + [sap_amount(subtotal), "OMR"]
        rows.append(("subtotal", None, cells))
    total = sum((p.amount for p in POSTINGS), Decimal("0.000"))
    rows.append(("total", None, ["Total"] + [None] * 11 + [sap_amount(total), "OMR"]))
    return rows


def write_cells(sheet: Worksheet, row: int, cells: Cells) -> None:
    """Write one row: text cells, number cells, and nothing for None."""
    for col, value in enumerate(cells):
        if isinstance(value, str):
            sheet.write_string(row, col, value)
        elif isinstance(value, int):
            sheet.write_number(row, col, value)


def write_xlsx(path: Path) -> None:
    """Write sample.xlsx: README, Transactions and the two mapping sheets."""
    # In memory, since XlsxWriter's temporary files end a line as the system
    # does, so each part inside is the same on Windows and Linux (ENG-02 12.3).
    workbook = xlsxwriter.Workbook(path, {"in_memory": True})
    workbook.set_properties({"created": CREATED})

    readme = workbook.add_worksheet("README")
    for row, line in enumerate(README_LINES):
        readme.write_string(row, 0, line)

    transactions = workbook.add_worksheet("Transactions")
    for row, (_kind, _posting, cells) in enumerate(transactions_rows()):
        write_cells(transactions, row, cells)

    gl_mapping = workbook.add_worksheet("GL Mapping")
    gl_header = [
        "G/L Account",
        "G/L Account Name",
        "Commitment Item",
        "Commitment Item Name",
    ]
    write_cells(gl_mapping, 0, gl_header)
    for row, (code, account) in enumerate(GL_ACCOUNTS.items(), start=1):
        item = account.commitment_item
        item_code = int(item) if item else None
        item_name = COMMITMENT_ITEMS[item] if item else None
        write_cells(gl_mapping, row, [int(code), account.name, item_code, item_name])

    cc_mapping = workbook.add_worksheet("Cost Center Mapping")
    cc_header = [
        "Cost Center",
        "Cost Center Name",
        "Department Code",
        "Department Name",
    ]
    write_cells(cc_mapping, 0, cc_header)
    for row, (code, center) in enumerate(COST_CENTERS.items(), start=1):
        department_name = DEPARTMENTS[center.department]
        cells = [int(code), center.name, center.department, department_name]
        write_cells(cc_mapping, row, cells)

    workbook.close()


def budget_line(p: Posting) -> str:
    """The posting's commitment item, or "none" for no budget line (ENG-02 8.2)."""
    account = GL_ACCOUNTS.get(p.gl)
    if account is None or account.commitment_item is None:
        return "none"
    return account.commitment_item


def department(p: Posting) -> str:
    """The posting's department, or "outside" for outside IDS (ENG-02 8.2)."""
    center = COST_CENTERS.get(p.cost_center)
    return center.department if center else "outside"


def money(amount: Decimal) -> str:
    """An amount as a JSON string with 3 decimals: Decimal('-1500') -> '-1500.000'."""
    return f"{amount:.3f}"


def totals_by(
    key: Callable[[Posting], str], postings: list[Posting] = POSTINGS
) -> dict[str, str]:
    """Add up the postings by key(p), sorted by key, as money strings."""
    totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for p in postings:
        totals[key(p)] += p.amount
    return {k: money(totals[k]) for k in sorted(totals)}


def nested_totals(
    outer: Callable[[Posting], str], inner: Callable[[Posting], str]
) -> dict[str, dict[str, str]]:
    """Add up every posting by outer(p), then within it by inner(p)."""
    keys = sorted({outer(p) for p in POSTINGS})
    return {
        key: totals_by(inner, [p for p in POSTINGS if outer(p) == key]) for key in keys
    }


def inside(p: Posting) -> bool:
    """True for a posting on a cost center inside IDS."""
    return department(p) != "outside"


def screen_figures() -> dict:
    """The figures the report screens show for the sample, beyond the groups
    above (ENG-03 4 and 11): by year, with outside IDS excluded, each GL
    account by cost center, each department and budget line by GL account,
    and the running total.
    """
    ids_only = [p for p in POSTINGS if inside(p)]
    running: dict[str, str] = {}
    so_far = Decimal("0.000")
    for month, amount in totals_by(lambda p: p.posting_date.strftime("%Y-%m")).items():
        so_far += Decimal(amount)
        running[month] = money(so_far)
    return {
        "lines": len(POSTINGS),
        "by_year": totals_by(lambda p: str(p.posting_date.year)),
        "excluding_outside": {
            "total": money(sum((p.amount for p in ids_only), Decimal("0.000"))),
            "lines": len(ids_only),
            "by_gl": totals_by(lambda p: p.gl, ids_only),
            "by_department": totals_by(department, ids_only),
        },
        "gl_by_cost_center": nested_totals(lambda p: p.gl, lambda p: p.cost_center),
        "department_by_gl": nested_totals(department, lambda p: p.gl),
        "budget_line_by_gl": nested_totals(budget_line, lambda p: p.gl),
        "running_total": running,
    }


# --- FAQ answers (ENG-03 7.2) ------------------------------------------------
# Written here from the true values, apart from the app's own sentences.


def omr(amount: Decimal) -> str:
    """An amount as the screens write it: Decimal('-1250') -> '-1,250.000'."""
    return f"{amount:,.3f}"


def plural(count: int, noun: str) -> str:
    """'1 GL account', '2 GL accounts'."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def largest(totals: dict[str, Decimal]) -> tuple[str, Decimal]:
    """The key with the largest amount, the smaller key on a tie."""
    return min(totals.items(), key=lambda item: (-item[1], item[0]))


def sums(key: Callable[[Posting], str], postings: list[Posting]) -> dict[str, Decimal]:
    """Add up the postings by key(p), as Decimals."""
    totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for p in postings:
        totals[key(p)] += p.amount
    return dict(totals)


def faq_answers(postings: list[Posting], outside_excluded: bool) -> dict[str, str]:
    """The answer sentence of each FAQ question for these postings, with
    the default picks: the largest GL account and the largest department.
    """
    total = sum((p.amount for p in postings), Decimal("0.000"))
    ids_total = sum((p.amount for p in postings if inside(p)), Decimal("0.000"))
    if outside_excluded:
        q1 = (
            f"Actual spend on IDS cost centers is {omr(total)} OMR across "
            f"{len(postings)} lines. Cost centers outside IDS are excluded."
        )
    else:
        q1 = (
            f"Actual spend is {omr(total)} OMR across {len(postings)} lines: "
            f"{omr(ids_total)} OMR on IDS cost centers and "
            f"{omr(total - ids_total)} OMR outside IDS."
        )

    gl, gl_amount = largest(sums(lambda p: p.gl, postings))
    percent = (gl_amount / total * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
    q2 = (
        f"The largest is {GL_ACCOUNTS[gl].name} ({gl}), with {omr(gl_amount)} OMR, "
        f"{percent}% of the total shown."
    )

    gls = {p.gl for p in postings}
    mapped = {gl for gl in gls if budget_line_of(gl) != "none"}
    items = {budget_line_of(gl) for gl in mapped}
    q3 = (
        f"{plural(len(mapped), 'GL account')} "
        f"{'maps' if len(mapped) == 1 else 'map'} to "
        f"{plural(len(items), 'budget line')}"
    )
    unmapped = len(gls) - len(mapped)
    if unmapped:
        q3 += f", and {unmapped} {'has' if unmapped == 1 else 'have'} no budget line"
    q3 += "."

    used = [p for p in postings if p.gl == gl]
    ids_centers = {p.cost_center for p in used if inside(p)}
    departments = {department(p) for p in used if inside(p)}
    outside_centers = {p.cost_center for p in used if not inside(p)}
    q4 = (
        f"{GL_ACCOUNTS[gl].name} ({gl}) was used by "
        f"{plural(len(ids_centers), 'cost center')} in "
        f"{plural(len(departments), 'department')}"
    )
    if outside_centers:
        q4 += f", and by {plural(len(outside_centers), 'cost center')} outside IDS"
    q4 += "."

    dept, dept_amount = largest(sums(department, [p for p in postings if inside(p)]))
    q5 = f"The largest is {DEPARTMENTS[dept]} ({dept}), with {omr(dept_amount)} OMR."

    dept_gl, dept_gl_amount = largest(
        sums(lambda p: p.gl, [p for p in postings if department(p) == dept])
    )
    q6 = (
        f"{DEPARTMENTS[dept]} ({dept}) spent most on "
        f"{GL_ACCOUNTS[dept_gl].name} ({dept_gl}): {omr(dept_gl_amount)} OMR."
    )

    last = max(p.posting_date for p in postings)
    q7 = f"Spending reached {omr(total)} OMR by the end of {last:%B %Y}."

    no_ci = [p for p in postings if budget_line(p) == "none"]
    no_dept = [p for p in postings if not inside(p)]
    both = [p for p in no_ci if not inside(p)]
    either = {id(p) for p in no_ci + no_dept}
    in_all = sum((p.amount for p in postings if id(p) in either), Decimal("0.000"))
    q8 = (
        f"{omr(sum((p.amount for p in no_ci), Decimal('0.000')))} OMR has no budget "
        f"line, and {omr(sum((p.amount for p in no_dept), Decimal('0.000')))} OMR "
        "is outside IDS."
    )
    if both:
        q8 += (
            f" {omr(sum((p.amount for p in both), Decimal('0.000')))} OMR is in both, "
            f"so {omr(in_all)} OMR couldn't be matched in all."
        )
    else:
        q8 += f" In all, {omr(in_all)} OMR couldn't be matched."
    return {"1": q1, "2": q2, "3": q3, "4": q4, "5": q5, "6": q6, "7": q7, "8": q8}


def budget_line_of(gl: str) -> str:
    """A GL account's commitment item, or "none" for no budget line."""
    account = GL_ACCOUNTS.get(gl)
    if account is None or account.commitment_item is None:
        return "none"
    return account.commitment_item


def amount_and_rows(items: list[tuple[int, Posting]]) -> dict:
    """The total amount and the Excel row numbers of (row number, posting) pairs."""
    amount = sum((p.amount for _, p in items), Decimal("0.000"))
    return {"amount": money(amount), "rows": [number for number, _ in items]}


def exceptions() -> dict:
    """The preview's exceptions, with Transactions row numbers (ENG-02 11.2)."""
    groups: dict[str, dict[str, list[tuple[int, Posting]]]] = {
        "missing_from_gl_mapping": {},
        "listed_without_ci": {},
        "outside_ids": {},
    }
    other_rows = []
    for number, (kind, p, _cells) in enumerate(transactions_rows(), start=1):
        if kind == "subtotal":
            other_rows.append(number)
        if kind != "spend":
            continue
        account = GL_ACCOUNTS.get(p.gl)
        if account is None:
            groups["missing_from_gl_mapping"].setdefault(p.gl, []).append((number, p))
        elif account.commitment_item is None:
            groups["listed_without_ci"].setdefault(p.gl, []).append((number, p))
        if p.cost_center not in COST_CENTERS:
            groups["outside_ids"].setdefault(p.cost_center, []).append((number, p))

    result: dict = {
        name: {code: amount_and_rows(items) for code, items in sorted(codes.items())}
        for name, codes in groups.items()
    }
    result["other_rows"] = other_rows
    return result


def expected_result(fixture: str, scenario: str) -> dict:
    """Every figure the app must produce for one upload (ENG-02 12.3)."""
    layout = transactions_rows()
    kinds = [kind for kind, _, _ in layout]
    spend = [p for kind, p, _ in layout if kind == "spend"]

    control_total = sum((p.amount for p in POSTINGS), Decimal("0.000"))
    to_commit = sum((p.amount for p in spend), Decimal("0.000"))
    skipped = Decimal("0.000")  # the sample has no bad rows
    outside = sum(
        (p.amount for p in POSTINGS if department(p) == "outside"), Decimal("0.000")
    )
    dates = [p.posting_date for p in POSTINGS]

    return {
        "fixture": fixture,
        "scenario": scenario,
        "header_row": kinds.index("header") + 1,
        "rows": {
            "to_commit": kinds.count("spend"),
            "skipped": 0,
            "other": kinds.count("subtotal"),
        },
        "period": {
            "first_posting": min(dates).isoformat(),
            "last_posting": max(dates).isoformat(),
        },
        "reconciliation": {
            "control_total": money(control_total),
            "to_commit": money(to_commit),
            "skipped": money(skipped),
            "difference": money(control_total - to_commit - skipped),
        },
        "actual_spend": {
            "total": money(control_total),
            "inside_ids": money(control_total - outside),
            "outside_ids": money(outside),
        },
        "by_gl": totals_by(lambda p: p.gl),
        "by_ci": totals_by(budget_line),
        "by_department": totals_by(department),
        "by_month": totals_by(lambda p: p.posting_date.strftime("%Y-%m")),
        "exceptions": exceptions(),
        "skipped_rows": [],
        "screens": screen_figures(),
        "faq": faq_answers(POSTINGS, outside_excluded=False),
        "faq_excluding_outside": faq_answers(
            [p for p in POSTINGS if inside(p)], outside_excluded=True
        ),
    }


def write_json(path: Path, data: dict) -> None:
    """Write JSON with LF line endings on every system, so the bytes never change."""
    text = json.dumps(data, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


# --- The semicolon CSV ------------------------------------------------------


def write_csv(path: Path) -> None:
    """Write the Transactions sheet as SAP-style semicolon text (ENG-02 12.3).

    UTF-8 with a byte-order mark, CRLF line endings, and every line padded to
    14 fields, so line n of the file is row n of the Excel sheet.
    """
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=";", lineterminator="\r\n")
        for _kind, _posting, cells in transactions_rows():
            writer.writerow(cells + [None] * (len(HEADER) - len(cells)))


def expected_csv_result() -> dict:
    """The CSV uploaded after sample.xlsx into the same report.

    It covers the same months, so it replaces every posting of sample.xlsx
    (ENG-02 7.3), and uses the Excel file's mappings (8.1). The figures are
    the same as the Excel file's.
    """
    result = expected_result(
        "sample_semicolon.csv", "uploaded after sample.xlsx into the same report"
    )
    replaced = sum((p.amount for p in POSTINGS), Decimal("0.000"))
    result["replaced"] = {"rows": len(POSTINGS), "amount": money(replaced)}
    return result


def write_all(directory: Path) -> list[Path]:
    """Write every fixture into directory, and return the files written."""
    xlsx = directory / "sample.xlsx"
    xlsx_result = directory / "sample.expected.json"
    csv_file = directory / "sample_semicolon.csv"
    csv_result = directory / "sample_semicolon.expected.json"

    write_xlsx(xlsx)
    write_json(
        xlsx_result,
        expected_result("sample.xlsx", "first upload into an empty report"),
    )
    write_csv(csv_file)
    write_json(csv_result, expected_csv_result())
    return [xlsx, xlsx_result, csv_file, csv_result]


def main() -> None:
    write_all(FIXTURES_DIR)


if __name__ == "__main__":
    main()
