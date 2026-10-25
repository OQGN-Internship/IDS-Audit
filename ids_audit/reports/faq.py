"""The FAQ pages: one entry per agreed question, fixed in code (ENG-03 7).

Until the client's final list arrives, these are the plan's eight default
questions. Each entry's query builds the tables behind its answer from
reports/queries.py, and its answer sentence is filled from those tables'
own figures, never typed. A part of a sentence with nothing to say is left
out (ENG-03 7.2).
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from ids_audit.reports import queries, screens
from ids_audit.reports.models import Report
from ids_audit.reports.queries import NONE, OUTSIDE, Filters, Keys
from ids_audit.reports.screens import Picker, Table
from ids_audit.reports.templatetags.figures import amount

COUNTED = (
    "Counted: the actual spend of every posting that counts in this report, "
    "from its committed uploads, under the filters above"
)


@dataclass
class Found:
    """What an entry's query found: the tables on its page, the figures its
    sentence is filled from, and how many lines they cover. picker is the
    page's choice of GL account or department, if it has one.
    """

    tables: list[Table]
    figures: dict
    lines: int
    picker: Picker | None = None


@dataclass(frozen=True)
class Entry:
    """One FAQ page (ENG-03 7.1). pick is the address key of its picker, gl or
    dept, or empty.
    """

    slug: str
    question: str
    query: Callable[[Report, Filters, str | None], Found]
    answer: Callable[[dict], str]
    counted: Callable[[dict], str]
    check: str
    pick: str = ""
    outside_applies: bool = True


def plural(number: int, noun: str) -> str:
    """'1 GL account', '2 GL accounts'."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def named(code: str, name: str) -> str:
    """A code with its name, as a sentence writes it: Software licences
    (610100), or the code alone when it has no name.
    """
    return f"{name} ({code})" if name and code else name or code


def omr(figure: Decimal) -> str:
    """An amount as a sentence writes it."""
    return f"{amount(figure)} OMR"


def rows_of(table: Table) -> list:
    """A table's rows, as a list."""
    return list(table.rows)


# 1. How much has IDS spent in total?


def total_spend(report: Report, filters: Filters, pick: str | None) -> Found:
    totals = queries.totals(report, filters)
    table = screens.spend_table(report, filters, totals)
    rows = rows_of(table)
    return Found(
        [table],
        {
            "total": table.total[1].value,
            "lines": table.total[2].value,
            "ids": rows[0][1].value,
            "outside": rows[1][1].value if len(rows) > 1 else None,
        },
        totals.lines,
    )


def total_spend_answer(f: dict) -> str:
    if f["outside"] is None:
        return (
            f"Actual spend on IDS cost centers is {omr(f['total'])} across "
            f"{plural(f['lines'], 'line')}. Cost centers outside IDS are excluded."
        )
    return (
        f"Actual spend is {omr(f['total'])} across {plural(f['lines'], 'line')}: "
        f"{omr(f['ids'])} on IDS cost centers and {omr(f['outside'])} outside IDS."
    )


# 2. Which GL accounts has IDS spent the most on?


def largest_gl_accounts(report: Report, filters: Filters, pick: str | None) -> Found:
    totals = queries.totals(report, filters)
    table = screens.gl_accounts_table(report, filters, totals)
    top = rows_of(table)[0] if totals.lines else None
    figures = {}
    if top is not None:
        figures = {
            "code": top[0].value,
            "name": top[1].value,
            "amount": top[4].value,
            "share": top[5].value,
        }
    return Found([table], figures, totals.lines)


def largest_gl_accounts_answer(f: dict) -> str:
    sentence = f"The largest is {named(f['code'], f['name'])}, with {omr(f['amount'])}"
    if f["share"] is not None:
        sentence += f", {f['share'] * 100:.1f}% of the total shown"
    return sentence + "."


# 3. Which budget line does each GL account belong to?


def gl_budget_lines(report: Report, filters: Filters, pick: str | None) -> Found:
    links = screens.Links(report, filters)
    totals = queries.totals(report, filters)
    columns = [
        screens.Column("GL account", "code"),
        screens.Column("Name", "text"),
        screens.Column("Budget line", "code"),
        screens.Column("Budget line name", "text"),
        screens.Column("Actual spend", "amount"),
        screens.Column("Lines", "count"),
    ]
    found = queries.gl_budget_lines(report, filters)
    rows = [
        [
            screens.code(g.gl, links.gl(g.gl)),
            screens.text(g.gl_name),
            *screens.keyed(links.budget_line(g.ci), g.ci, g.ci_name),
            screens.amount(g.amount, links.rows(Keys(gl=g.gl, ci=g.ci))),
            screens.count(g.lines),
        ]
        for g in found
    ]
    table = Table(
        "GL accounts and their budget lines",
        "Budget lines",
        columns,
        rows,
        screens.total_row(columns, totals.total, totals.lines, links.rows()),
    )
    # Counted among the table's rows (ENG-03 7.2).
    mapped = {g.gl for g in found if g.ci != NONE}
    unmapped = {g.gl for g in found if g.ci == NONE}
    lines_of = [g.gl for g in found]
    return Found(
        [table],
        {
            "mapped": len(mapped),
            "budget_lines": len({g.ci for g in found if g.ci != NONE}),
            "unmapped": len(unmapped),
            "several": len({gl for gl in lines_of if lines_of.count(gl) > 1}),
        },
        totals.lines,
    )


def gl_budget_lines_answer(f: dict) -> str:
    verb = "maps" if f["mapped"] == 1 else "map"
    sentence = (
        f"{plural(f['mapped'], 'GL account')} {verb} to "
        f"{plural(f['budget_lines'], 'budget line')}"
    )
    if f["unmapped"]:
        has = "has" if f["unmapped"] == 1 else "have"
        sentence += f", and {f['unmapped']} {has} no budget line"
    sentence += "."
    if f["several"]:
        has = "has" if f["several"] == 1 else "have"
        sentence += (
            f" {plural(f['several'], 'GL account')} {has} more than one budget line."
        )
    return sentence


# 4. Which cost centers and departments used a GL account?


def gl_picker(report: Report, filters: Filters, pick: str | None) -> Picker:
    """The GL accounts to pick from, largest first, with the one shown: the
    one in the address, or the largest.
    """
    groups = queries.highest_first(queries.by_gl_account(report, filters))
    options = [(g.key, f"{g.key} {g.name}".strip()) for g in groups]
    chosen = pick or (options[0][0] if options else "")
    if chosen and chosen not in dict(options):
        options.append((chosen, chosen))
    return Picker("gl", "GL account", options, chosen)


def gl_account_cost_centers(
    report: Report, filters: Filters, pick: str | None
) -> Found:
    picker = gl_picker(report, filters, pick)
    gl = picker.chosen
    keys = Keys(gl=gl)
    totals = queries.totals(report, filters, keys)
    table = screens.gl_cost_centers_table(report, filters, gl, totals)
    rows = rows_of(table)
    # Columns: cost center, name, department code, department name. Outside
    # IDS is a catch-all, so it has no code (ENG-03 7.2).
    outside = [r for r in rows if not r[2].value and r[3].value == queries.OUTSIDE_IDS]
    inside = [r for r in rows if r not in outside]
    return Found(
        [table],
        {
            "code": gl,
            "name": queries.name_of(report, "gl_name", keys),
            "cost_centers": len({r[0].value for r in inside}),
            "departments": len({r[2].value for r in inside if r[2].value}),
            "outside": len({r[0].value for r in outside}),
        },
        totals.lines,
        picker,
    )


def gl_account_cost_centers_answer(f: dict) -> str:
    sentence = (
        f"{named(f['code'], f['name'])} was used by "
        f"{plural(f['cost_centers'], 'cost center')} in "
        f"{plural(f['departments'], 'department')}"
    )
    if f["outside"]:
        sentence += f", and by {plural(f['outside'], 'cost center')} outside IDS"
    return sentence + "."


# 5. How much has each department spent?


def department_spend(report: Report, filters: Filters, pick: str | None) -> Found:
    totals = queries.totals(report, filters)
    table = screens.departments_table(report, filters, totals)
    figures = {}
    if totals.lines:
        top = rows_of(table)[0]
        figures = {"code": top[0].value, "name": top[1].value, "amount": top[2].value}
    return Found([table], figures, totals.lines)


def department_spend_answer(f: dict) -> str:
    return f"The largest is {named(f['code'], f['name'])}, with {omr(f['amount'])}."


# 6. What has a department spent its money on?


def department_picker(report: Report, filters: Filters, pick: str | None) -> Picker:
    """The departments to pick from, as the departments screen lists them,
    No department and Outside IDS included, with the one shown.
    """
    groups = queries.highest_first(queries.by_department(report, filters))
    options = [
        (g.key, g.name if g.key in (NONE, OUTSIDE) else f"{g.key} {g.name}".strip())
        for g in groups
    ]
    chosen = pick or (options[0][0] if options else "")
    if chosen and chosen not in dict(options):
        options.append((chosen, chosen))
    return Picker("dept", "Department", options, chosen)


def department_gl_accounts(report: Report, filters: Filters, pick: str | None) -> Found:
    picker = department_picker(report, filters, pick)
    key = picker.chosen
    keys = Keys(dept=key)
    totals = queries.totals(report, filters, keys)
    table = screens.department_gl_table(report, filters, key, totals)
    figures = {}
    if totals.lines:
        top = rows_of(table)[0]
        if key in (NONE, OUTSIDE):
            department = screens.department_title(key, "")
        else:
            department = named(key, queries.name_of(report, "dept_name", keys))
        figures = {
            "department": department,
            "code": top[0].value,
            "name": top[1].value,
            "amount": top[2].value,
        }
    return Found([table], figures, totals.lines, picker)


def department_gl_accounts_answer(f: dict) -> str:
    return (
        f"{f['department']} spent most on {named(f['code'], f['name'])}: "
        f"{omr(f['amount'])}."
    )


# 7. How has spending built up over time?


def spend_over_time(report: Report, filters: Filters, pick: str | None) -> Found:
    links = screens.Links(report, filters)
    totals = queries.totals(report, filters)
    months = queries.by_month(report, filters)
    table = screens.month_table(links, months, totals)
    figures = {}
    if months:
        last = rows_of(table)[-1]
        figures = {"running": last[2].value, "month": months[-1].key}
    return Found([table], figures, totals.lines)


def spend_over_time_answer(f: dict) -> str:
    return f"Spending reached {omr(f['running'])} by the end of {f['month']:%B %Y}."


# 8. What couldn't be matched to a budget line or an IDS department?


def unmatched_spend(report: Report, filters: Filters, pick: str | None) -> Found:
    # The outside IDS filter doesn't apply, since excluding outside IDS would
    # empty its own list (ENG-03 8).
    chosen = Filters(year=filters.year)
    tables, unmatched = screens.unmatched_tables(report, chosen)
    no_ci, outside, no_dept = tables
    return Found(
        tables,
        {
            "no_budget_line": no_ci.total[3].value,
            "outside_ids": outside.total[2].value,
            "no_department": no_dept.total[2].value,
            "with_no_department": bool(rows_of(no_dept)),
            "in_both": unmatched.in_both,
            "in_all": unmatched.in_all,
        },
        queries.totals(report, chosen).lines,
    )


def unmatched_spend_answer(f: dict) -> str:
    sentence = (
        f"{omr(f['no_budget_line'])} has no budget line, and "
        f"{omr(f['outside_ids'])} is outside IDS."
    )
    if f["with_no_department"]:
        sentence += (
            f" {omr(f['no_department'])} is on IDS cost centers with no department."
        )
    if f["in_both"]:
        sentence += (
            f" {omr(f['in_both'])} is in both, so {omr(f['in_all'])} couldn't be "
            "matched in all."
        )
    else:
        sentence += f" In all, {omr(f['in_all'])} couldn't be matched."
    return sentence


def counted(suffix: Callable[[dict], str] | None = None) -> Callable[[dict], str]:
    """The line saying exactly what an entry counted, with its own part."""
    return lambda f: f"{COUNTED}{suffix(f) if suffix else ''}."


ENTRIES = [
    Entry(
        "total-spend",
        "How much has IDS spent in total?",
        total_spend,
        total_spend_answer,
        counted(),
        "Keep only actual-spend rows, then add up the amount column.",
    ),
    Entry(
        "largest-gl-accounts",
        "Which GL accounts has IDS spent the most on?",
        largest_gl_accounts,
        largest_gl_accounts_answer,
        counted(lambda f: ", by GL account"),
        "A pivot table of amount by GL account, sorted largest first.",
    ),
    Entry(
        "gl-budget-lines",
        "Which budget line does each GL account belong to?",
        gl_budget_lines,
        gl_budget_lines_answer,
        counted(lambda f: ", by GL account and the budget line it's counted under"),
        "Look up each GL account in the GL mapping sheet.",
    ),
    Entry(
        "gl-account-cost-centers",
        "Which cost centers and departments used a GL account?",
        gl_account_cost_centers,
        gl_account_cost_centers_answer,
        counted(lambda f: f", for GL account {f['code']}, by cost center"),
        "Filter on the GL account, then pivot amount by cost center.",
        pick="gl",
    ),
    Entry(
        "department-spend",
        "How much has each department spent?",
        department_spend,
        department_spend_answer,
        counted(lambda f: ", by department"),
        "Add each row's department from the cost center mapping sheet, then "
        "pivot by department.",
    ),
    Entry(
        "department-gl-accounts",
        "What has a department spent its money on?",
        department_gl_accounts,
        department_gl_accounts_answer,
        counted(lambda f: f", for {f['department']}, by GL account"),
        "Filter on the department's cost centers, then pivot by GL account.",
        pick="dept",
    ),
    Entry(
        "spend-over-time",
        "How has spending built up over time?",
        spend_over_time,
        spend_over_time_answer,
        counted(lambda f: ", by month of posting date"),
        "Pivot amount by posting month, and add a running total.",
    ),
    Entry(
        "unmatched-spend",
        "What couldn't be matched to a budget line or an IDS department?",
        unmatched_spend,
        unmatched_spend_answer,
        counted(lambda f: ", with outside IDS always included"),
        "Add up the GL accounts missing from the GL mapping or listed there "
        "without a commitment item. Then, separately, add up the cost centers "
        "missing from the cost center mapping, and those listed there without a "
        "department code. Add up the rows in both once, and take them from the "
        "sum of the two.",
        outside_applies=False,
    ),
]
BY_SLUG = {entry.slug: entry for entry in ENTRIES}

# The hand checks are written for a report built from one file (ENG-03 7.2).
CHECK_NOTE = (
    "Every amount check counts only actual-spend rows, so the total row and "
    "subtotals stay out, and leaves out the skipped rows, which each upload's "
    "preview lists. With several files, check against the latest file for each "
    "month, and take budget lines and departments from the mapping in force."
)


def page(report: Report, filters: Filters, entry: Entry, pick: str | None):
    """Build an FAQ page as a screen: its question, answer, tables, what was
    counted and how to check it (ENG-03 7).
    """
    found = entry.query(report, filters, pick)
    answer = entry.answer(found.figures) if found.lines else ""
    view = f"faq/{entry.slug}"
    return screens.Screen(
        entry.question,
        view,
        found.tables,
        found.lines,
        notes=[screens.OUTSIDE_NOTE] if not entry.outside_applies else [],
        outside_applies=entry.outside_applies,
        answer=answer,
        counted=entry.counted(found.figures) if found.lines else "",
        check=f"{entry.check} {CHECK_NOTE}",
        picker=found.picker,
    )
