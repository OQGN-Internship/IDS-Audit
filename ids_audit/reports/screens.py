"""Each report screen's tables, built once from reports/queries.py (ENG-03 4).

The page shows these tables and the export writes them (ENG-03 9), so the
two have the same rows, order and totals. Every amount and every total comes
from a query; nothing here adds amounts.
"""

import dataclasses
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from django.http import Http404
from django.urls import reverse

from ids_audit.reports import filters as addresses
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from ids_audit.reports.queries import (
    NO_KEYS,
    NONE,
    OUTSIDE,
    OUTSIDE_IDS,
    Filters,
    Group,
    Keys,
)
from ids_audit.reports.templatetags import figures


@dataclass(frozen=True)
class Cell:
    """One cell of a table, and the address it links to on screen.

    kind says how it's shown and exported: code, text, item (text shown
    with its direction set automatically), amount, exact (an amount kept as
    read, such as a skipped row's), share, count, date or month.
    """

    value: object
    kind: str
    link: str = ""


@dataclass(frozen=True)
class Column:
    """A table's column: its heading, and the kind of its cells."""

    title: str
    kind: str


@dataclass
class Table:
    """A table on a screen, which is also a sheet of its export (ENG-03 9.2):
    its heading, the sheet's fixed name, its columns, its rows and its total
    row. rows is any iterable, so an export can write every source row
    without holding them all.
    """

    title: str
    sheet: str
    columns: list[Column]
    rows: Iterable[list[Cell]]
    total: list[Cell] | None = None


@dataclass
class Screen:
    """A screen's heading, its address under the report, which names its
    export (ENG-03 9.4), and its tables.

    lines is how many lines its figures cover, so the page can say nothing
    matched the filters (3.1); None where that doesn't apply. notes are
    sentences shown above the tables, and outside_applies says whether the
    outside IDS filter applies (ENG-03 8).
    """

    title: str
    view: str
    tables: list[Table]
    lines: int | None = None
    notes: list[str] = field(default_factory=list)
    outside_applies: bool = True
    # The pages of source rows: (label, address) pairs.
    pager: list[tuple[str, str]] = field(default_factory=list)


# Cells


def code(value: str, link: str = "") -> Cell:
    return Cell(value, "code", link)


def text(value: str, link: str = "") -> Cell:
    return Cell(value or "", "text", link)


def amount(value: Decimal | None, link: str = "") -> Cell:
    return Cell(value, "amount", link)


def count(value: int | None, link: str = "") -> Cell:
    return Cell(value, "count", link)


def share(value: Decimal | None) -> Cell:
    return Cell(value, "share")


BLANK = Cell("", "text")


class Links:
    """The addresses a screen's cells link to, each carrying its filters."""

    def __init__(self, report: Report, filters: Filters) -> None:
        self.report = report
        self.filters = filters

    def to(self, name: str, *args: object, keys: Keys = NO_KEYS) -> str:
        return reverse(name, args=[self.report.pk, *args]) + addresses.query(
            self.filters, keys
        )

    def rows(self, keys: Keys = NO_KEYS) -> str:
        return self.to("rows", keys=keys)

    def gl(self, gl: str) -> str:
        return self.to("gl_account", gl)

    def department(self, key: str) -> str:
        return self.to("department", key)

    def budget_line(self, key: str) -> str:
        return self.to("budget_line", key)


def shown_code(key: str) -> str:
    """A group's code as a code column shows it: empty for a catch-all."""
    return "" if key in (NONE, OUTSIDE, "") else key


def keyed(link: str, key: str, name: str) -> tuple[Cell, Cell]:
    """A group's code and name cells. The code links to its screen, or for a
    catch-all, which has no code, the name does.
    """
    if shown_code(key):
        return code(key, link), text(name)
    return code(""), text(name, link)


def total_row(
    columns: list[Column], figure: Decimal, lines: int | None, link: str
) -> list[Cell]:
    """A total row: "Total" first, then the figure under the first amount
    column and the lines under the last count column, if the table has them.
    """
    cells = [text("Total")] + [BLANK] * (len(columns) - 1)
    kinds = [column.kind for column in columns]
    if "amount" in kinds:
        cells[kinds.index("amount")] = amount(figure, link)
    if lines is not None and "count" in kinds:
        cells[len(kinds) - 1 - kinds[::-1].index("count")] = count(lines)
    return cells


MONTH_COLUMNS = [
    Column("Month", "month"),
    Column("Actual spend", "amount"),
    Column("Running total", "amount"),
    Column("Lines", "count"),
]


def month_table(
    links: Links, months: list[Group], totals: queries.Totals, keys: Keys = NO_KEYS
) -> Table:
    """The spend by month with its running total, oldest first (ENG-03 4)."""
    rows = [
        [
            Cell(m.name, "month"),
            amount(m.amount, links.rows(dataclasses.replace(keys, month=m.key))),
            amount(m.running, links.rows(dataclasses.replace(keys, through=m.key))),
            count(m.lines),
        ]
        for m in months
    ]
    total = [text("Total"), amount(totals.total, links.rows(keys)), BLANK]
    return Table(
        "By month", "By month", MONTH_COLUMNS, rows, [*total, count(totals.lines)]
    )


# The screens (ENG-03 4)


def overview(report: Report, filters: Filters) -> Screen:
    """Total actual spend, inside and outside IDS, the 10 largest GL accounts
    and the others, and the running total by month.
    """
    links = Links(report, filters)
    totals = queries.totals(report, filters)
    ids_only = Filters(year=filters.year, exclude_outside=True)
    ids = queries.totals(report, ids_only)
    columns = [
        Column("Spend", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            text("On IDS cost centers"),
            amount(ids.total, Links(report, ids_only).rows()),
            count(ids.lines),
        ]
    ]
    if not filters.exclude_outside:
        outside = queries.totals(report, filters, Keys(dept=OUTSIDE))
        rows.append(
            [
                text(OUTSIDE_IDS),
                amount(outside.total, links.rows(Keys(dept=OUTSIDE))),
                count(outside.lines),
            ]
        )
    spend = Table(
        "Actual spend",
        "Actual spend",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows()),
    )

    top = queries.top_gl_accounts(report, filters)
    columns = [
        Column("GL account", "code"),
        Column("Name", "text"),
        Column("Actual spend", "amount"),
        Column("Share", "share"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            code(g.key, links.gl(g.key)),
            text(g.name),
            amount(g.amount, links.rows(Keys(gl=g.key))),
            share(g.share),
            count(g.lines),
        ]
        for g in top.largest
    ]
    if top.others is not None:
        # They cover many accounts, so they open the GL accounts screen (6.2).
        everything = links.to("gl_accounts")
        rows.append(
            [
                code(""),
                text(top.others.name, everything),
                amount(top.others.amount, everything),
                share(top.others.share),
                count(top.others.lines),
            ]
        )
    largest = Table(
        "Largest GL accounts",
        "Largest GL accounts",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows()),
    )
    months = month_table(links, queries.by_month(report, filters), totals)
    return Screen("Overview", "overview", [spend, largest, months], totals.lines)


def gl_accounts(report: Report, filters: Filters) -> Screen:
    """Each GL account with its budget line, actual spend, share, lines and
    number of cost centers.
    """
    links = Links(report, filters)
    totals = queries.totals(report, filters)
    columns = [
        Column("GL account", "code"),
        Column("Name", "text"),
        Column("Budget line", "code"),
        Column("Budget line name", "text"),
        Column("Actual spend", "amount"),
        Column("Share", "share"),
        Column("Lines", "count"),
        Column("Cost centers", "count"),
    ]
    rows = []
    for a in queries.gl_accounts(report, filters):
        if a.budget_line:
            ci_code, ci_name = keyed(
                links.budget_line(a.budget_line), a.budget_line, a.budget_line_name
            )
        else:
            # Several budget lines: the GL account's screen splits them (2.3).
            ci_code, ci_name = code(""), text(a.budget_line_name, links.gl(a.key))
        rows.append(
            [
                code(a.key, links.gl(a.key)),
                text(a.name),
                ci_code,
                ci_name,
                amount(a.amount, links.rows(Keys(gl=a.key))),
                share(a.share),
                count(a.lines),
                count(a.cost_centers),
            ]
        )
    total = total_row(columns, totals.total, None, links.rows())
    total[6] = count(totals.lines)
    table = Table("GL accounts", "GL accounts", columns, rows, total)
    return Screen("GL accounts", "gl", [table], totals.lines)


def gl_account(report: Report, filters: Filters, gl: str) -> Screen:
    """One GL account: its budget line, its spend by cost center and
    department, and its spend by month with the running total.
    """
    links = Links(report, filters)
    keys = Keys(gl=gl)
    name = queries.name_of(report, "gl_name", keys)
    totals = queries.totals(report, filters, keys)

    columns = [
        Column("Budget line", "code"),
        Column("Name", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            *keyed(links.budget_line(g.key), g.key, g.name),
            amount(g.amount, links.rows(Keys(gl=gl, ci=g.key))),
            count(g.lines),
        ]
        for g in queries.highest_first(queries.by_budget_line(report, filters, keys))
    ]
    budget = Table(
        "Budget lines",
        "Budget lines",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows(keys)),
    )

    columns = [
        Column("Cost center", "code"),
        Column("Name", "text"),
        Column("Department", "code"),
        Column("Department name", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            code(c.key),
            text(c.name),
            *keyed(links.department(c.dept), c.dept, c.dept_name),
            amount(c.amount, links.rows(Keys(gl=gl, cc=c.key, dept=c.dept))),
            count(c.lines),
        ]
        for c in queries.by_cost_center(report, filters, keys)
    ]
    centers = Table(
        "Cost centers",
        "Cost centers",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows(keys)),
    )
    months = month_table(links, queries.by_month(report, filters, keys), totals, keys)
    title = f"GL account {gl}: {name}" if name else f"GL account {gl}"
    return Screen(title, f"gl/{gl}", [budget, centers, months], totals.lines)


GROUP_COLUMNS = [
    Column("Name", "text"),
    Column("Actual spend", "amount"),
    Column("Share", "share"),
    Column("Lines", "count"),
]


def departments(report: Report, filters: Filters) -> Screen:
    """Each department's actual spend, share and lines, with No department
    and Outside IDS as rows of their own.
    """
    links = Links(report, filters)
    totals = queries.totals(report, filters)
    columns = [Column("Department", "code"), *GROUP_COLUMNS]
    rows = [
        [
            *keyed(links.department(g.key), g.key, g.name),
            amount(g.amount, links.rows(Keys(dept=g.key))),
            share(g.share),
            count(g.lines),
        ]
        for g in queries.highest_first(queries.by_department(report, filters))
    ]
    table = Table(
        "Departments",
        "Departments",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows()),
    )
    return Screen("Departments", "departments", [table], totals.lines)


def department_title(key: str, name: str) -> str:
    """A department screen's heading."""
    if key == OUTSIDE:
        return OUTSIDE_IDS
    if key == NONE:
        return queries.NO_DEPARTMENT
    return f"Department {key}: {name}" if name else f"Department {key}"


def department(report: Report, filters: Filters, key: str) -> Screen:
    """One department: its spend by GL account and by cost center, and by
    month with the running total.
    """
    links = Links(report, filters)
    keys = Keys(dept=key)
    totals = queries.totals(report, filters, keys)
    columns = [
        Column("GL account", "code"),
        Column("Name", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            code(g.key, links.gl(g.key)),
            text(g.name),
            amount(g.amount, links.rows(Keys(dept=key, gl=g.key))),
            count(g.lines),
        ]
        for g in queries.highest_first(queries.by_gl_account(report, filters, keys))
    ]
    gls = Table(
        "GL accounts",
        "GL accounts",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows(keys)),
    )
    columns = [
        Column("Cost center", "code"),
        Column("Name", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            code(c.key),
            text(c.name),
            amount(c.amount, links.rows(Keys(dept=key, cc=c.key))),
            count(c.lines),
        ]
        for c in queries.by_cost_center(report, filters, keys)
    ]
    centers = Table(
        "Cost centers",
        "Cost centers",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows(keys)),
    )
    months = month_table(links, queries.by_month(report, filters, keys), totals, keys)
    name = "" if key in (NONE, OUTSIDE) else queries.name_of(report, "dept_name", keys)
    return Screen(
        department_title(key, name),
        f"departments/{key}",
        [gls, centers, months],
        totals.lines,
    )


def budget_lines(report: Report, filters: Filters) -> Screen:
    """Each commitment item with its GL accounts and actual spend, with No
    budget line as its own row.
    """
    links = Links(report, filters)
    totals = queries.totals(report, filters)
    columns = [
        Column("Budget line", "code"),
        Column("Name", "text"),
        Column("GL accounts", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            *keyed(links.budget_line(b.key), b.key, b.name),
            text(", ".join(b.gl_accounts)),
            amount(b.amount, links.rows(Keys(ci=b.key))),
            count(b.lines),
        ]
        for b in queries.budget_lines(report, filters)
    ]
    table = Table(
        "Budget lines",
        "Budget lines",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows()),
    )
    return Screen("Budget lines", "budget-lines", [table], totals.lines)


def budget_line(report: Report, filters: Filters, key: str) -> Screen:
    """One budget line: its GL accounts and their spend."""
    links = Links(report, filters)
    keys = Keys(ci=key)
    totals = queries.totals(report, filters, keys)
    columns = [
        Column("GL account", "code"),
        Column("Name", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            code(g.key, links.gl(g.key)),
            text(g.name),
            amount(g.amount, links.rows(Keys(ci=key, gl=g.key))),
            count(g.lines),
        ]
        for g in queries.highest_first(queries.by_gl_account(report, filters, keys))
    ]
    table = Table(
        "GL accounts",
        "GL accounts",
        columns,
        rows,
        total_row(columns, totals.total, totals.lines, links.rows(keys)),
    )
    if key == NONE:
        title = queries.NO_BUDGET_LINE
    else:
        name = queries.name_of(report, "ci_name", keys)
        title = f"Budget line {key}: {name}" if name else f"Budget line {key}"
    return Screen(title, f"budget-lines/{key}", [table], totals.lines)


ROW_COLUMNS = [
    Column("Posting date", "date"),
    Column("GL account", "code"),
    Column("Cost center", "code"),
    Column("Value type", "text"),
    Column("Amount", "amount"),
    Column("Item text", "item"),
    Column("Document number", "code"),
    Column("Budget line", "text"),
    Column("Department", "text"),
    Column("Upload", "count"),
    Column("File", "text"),
    Column("Sheet", "text"),
    Column("Row", "count"),
]


def row_cells(links: Links, row) -> list[Cell]:
    """One source row's cells (ENG-03 6.2)."""
    if row.is_outside_ids:
        dept = OUTSIDE_IDS
    else:
        dept = row.dept_code or queries.NO_DEPARTMENT
    return [
        Cell(row.posting_date, "date"),
        code(row.gl_code, links.gl(row.gl_code)),
        code(row.cost_center_code),
        text(row.value_type),
        amount(row.amount),
        Cell(row.item_text, "item"),
        code(row.document_number),
        text(row.ci_code or queries.NO_BUDGET_LINE),
        text(dept),
        count(
            row.upload.number,
            reverse("upload", args=[links.report.pk, row.upload.number]),
        ),
        text(row.upload.original_name),
        text(row.sheet),
        count(row.row_number),
    ]


def source_rows(
    report: Report, filters: Filters, keys: Keys, page: int | None
) -> Screen:
    """The postings behind a figure, 100 to a page, or with page None every
    one of them, for the export (ENG-03 6).
    """
    links = Links(report, filters)
    totals = queries.totals(report, filters, keys)
    rows = queries.source_rows(report, filters, keys)
    pages = max(1, math.ceil(totals.lines / queries.ROWS_PER_PAGE))
    if page is None:
        cells = (row_cells(links, row) for row in rows.iterator(chunk_size=2000))
    else:
        if page > pages:
            raise Http404("There's no such page.")
        start = (page - 1) * queries.ROWS_PER_PAGE
        cells = [
            row_cells(links, row) for row in rows[start : start + queries.ROWS_PER_PAGE]
        ]
    total = total_row(ROW_COLUMNS, totals.total, None, "")
    total[0] = text(f"Total of {totals.lines} rows")
    table = Table("Source rows", "Source rows", ROW_COLUMNS, cells, total)
    pager = []
    if page is not None and pages > 1:
        url = reverse("rows", args=[report.pk])
        for label, number in (("Previous", page - 1), ("Next", page + 1)):
            if 1 <= number <= pages:
                pager.append((label, url + addresses.query(filters, keys, page=number)))
    return Screen(
        addresses.keys_in_words(keys),
        "rows",
        [table],
        totals.lines,
        notes=[
            f"{totals.lines} rows, adding up to {figures.amount(totals.total)} OMR."
            + (f" Page {page} of {pages}." if page is not None and pages > 1 else "")
        ],
        pager=pager,
    )


CI_REASONS = {
    queries.CiStatus.MISSING_FROM_GL_MAP: "Missing from the GL mapping",
    queries.CiStatus.LISTED_WITHOUT_CI: "Listed without a commitment item",
}


def exceptions(report: Report, filters: Filters) -> Screen:
    """What's wrong or unmatched in the report as it stands now (ENG-03 8).

    The outside IDS filter doesn't apply here, and every link uses
    exclude_outside=0, so the rows add up to the amount clicked.
    """
    chosen = Filters(year=filters.year)
    links = Links(report, chosen)
    unmatched = queries.unmatched_totals(report, chosen)

    columns = [
        Column("GL account", "code"),
        Column("Name", "text"),
        Column("Why", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            code(u.key, links.gl(u.key)),
            text(u.name),
            text(CI_REASONS[u.reason]),
            amount(u.amount, links.rows(Keys(gl=u.key, ci=NONE))),
            count(u.lines),
        ]
        for u in queries.no_budget_line(report, chosen)
    ]
    no_ci = Table(
        "GL accounts with no budget line",
        "No budget line",
        columns,
        rows,
        total_row(columns, unmatched.no_budget_line, None, links.rows(Keys(ci=NONE))),
    )

    columns = [
        Column("Cost center", "code"),
        Column("GL accounts used", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            code(u.key),
            text(", ".join(u.gl_accounts)),
            amount(u.amount, links.rows(Keys(cc=u.key, dept=OUTSIDE))),
            count(u.lines),
        ]
        for u in queries.outside_ids(report, chosen)
    ]
    outside = Table(
        "Cost centers outside IDS",
        "Outside IDS",
        columns,
        rows,
        total_row(columns, unmatched.outside_ids, None, links.rows(Keys(dept=OUTSIDE))),
    )

    columns = [
        Column("Cost center", "code"),
        Column("Name", "text"),
        Column("Actual spend", "amount"),
        Column("Lines", "count"),
    ]
    rows = [
        [
            code(u.key),
            text(u.name),
            amount(u.amount, links.rows(Keys(cc=u.key, dept=NONE))),
            count(u.lines),
        ]
        for u in queries.no_department(report, chosen)
    ]
    no_dept = Table(
        "Cost centers with no department",
        "No department",
        columns,
        rows,
        total_row(columns, unmatched.no_department, None, links.rows(Keys(dept=NONE))),
    )

    tables = [no_ci, outside, no_dept]
    for spec, title, sheet, what, empty in (
        (
            queries.GL_MAP,
            "Budget-line changes between files",
            "Budget-line changes",
            ("GL account", "Commitment item"),
            "No commitment item",
        ),
        (
            queries.CC_MAP,
            "Department changes between files",
            "Department changes",
            ("Cost center", "Department"),
            queries.NO_DEPARTMENT,
        ),
    ):
        tables.append(
            change_table(report, links, chosen, spec, title, sheet, what, empty)
        )
    tables.append(skipped_table(report))
    return Screen(
        "Exceptions",
        "exceptions",
        tables,
        notes=[
            "The year filter applies to the first three lists, and to the spend "
            "in the changes between files. The changes and the skipped rows have "
            "no posting date, so they're listed for every committed upload, "
            "whatever the year.",
            "The outside IDS filter doesn't apply on this screen, since excluding "
            "outside IDS would empty its own list.",
        ],
        outside_applies=False,
    )


def change_table(
    report: Report,
    links: Links,
    chosen: Filters,
    spec: queries.MapSpec,
    title: str,
    sheet: str,
    what: tuple[str, str],
    empty: str,
) -> Table:
    """A list of the changes between files of one mapping (ENG-03 8)."""
    code_column, value_column = what
    columns = [
        Column(code_column, "code"),
        Column("Upload", "count"),
        Column("File", "text"),
        Column(value_column, "code"),
        Column("Name", "text"),
        Column("Actual spend still counting", "amount"),
        Column("Lines", "count"),
    ]
    key = "gl" if spec is queries.GL_MAP else "cc"
    rows = []
    for change in queries.mapping_changes(report, spec, chosen):
        for mapped in change.uploads:
            number = mapped.upload.number
            rows.append(
                [
                    code(change.code, links.gl(change.code) if key == "gl" else ""),
                    count(number, reverse("upload", args=[report.pk, number])),
                    text(mapped.upload.original_name),
                    code(mapped.value),
                    text(mapped.value_name if mapped.value else empty),
                    amount(
                        mapped.amount,
                        links.rows(Keys(upload=number, **{key: change.code})),
                    ),
                    count(mapped.lines),
                ]
            )
    figure, lines = queries.mapping_changes_total(report, spec, chosen)
    return Table(title, sheet, columns, rows, total_row(columns, figure, lines, ""))


def skipped_table(report: Report) -> Table:
    """Each committed upload's skipped rows by reason, linking to its preview
    (ENG-03 8).
    """
    columns = [
        Column("Upload", "count"),
        Column("File", "text"),
        Column("Reason", "text"),
        Column("Rows", "count"),
        Column("Amount", "exact"),
    ]
    rows = [
        [
            count(s.upload, reverse("upload", args=[report.pk, s.upload])),
            text(s.file),
            text(s.reason),
            count(s.rows),
            Cell(s.amount, "exact"),
        ]
        for s in queries.skipped(report)
    ]
    found = queries.skipped_total(report)
    total = [
        text("Total"),
        BLANK,
        BLANK,
        count(found.rows),
        Cell(found.amount, "exact"),
    ]
    return Table("Skipped rows", "Skipped rows", columns, rows, total)
