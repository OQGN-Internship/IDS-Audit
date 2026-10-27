"""Make the demo files beside this script: a mapping, five clean samples, and
five files that each fill one list of the Exceptions screen (ENG-03 8).

Every value is made up. The files are laid out like the test sample
(ENG-02 12.3), an SAP G/L line item export, and the figures in README.md are
worked out here from the same values with Decimal, never by reading the files
back.

Run from the repository root:

    uv run python Demo/make_demo.py
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import xlsxwriter

HERE = Path(__file__).resolve().parent
CREATED = datetime(2026, 10, 27)  # fixed, so every run writes the same parts
COMPANY = "1000"
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

COMMITMENT_ITEMS = {
    "2101": "Software and licences",
    "2201": "Hardware",
    "2301": "Telecom and network",
    "2401": "Professional services",
    "2501": "Training and other",
}

# Each GL account's name and commitment item. 650200 is listed without one,
# and 640200, IT contractors, isn't listed at all: only file 06 posts to them.
GL_ACCOUNTS = {
    "610100": ("Software licences", "2101"),
    "610200": ("Software support", "2101"),
    "610300": ("Cloud services", "2101"),
    "620100": ("Hardware purchases", "2201"),
    "620200": ("Hardware maintenance", "2201"),
    "630100": ("Telecommunications", "2301"),
    "630200": ("Data lines", "2301"),
    "640100": ("IT consulting", "2401"),
    "650100": ("IT training", "2501"),
    "650200": ("Conferences and travel", ""),
    "660100": ("IT supplies", "2501"),
}

DEPARTMENTS = {
    "APP": "Applications",
    "INF": "Infrastructure",
    "SEC": "Cyber security",
    "GOV": "IT governance",
}

# Each cost center's name and department. 4570 is listed without one, and
# 3100 and 3200 aren't listed, so they're outside IDS: only files 07 and 08
# post to them.
COST_CENTERS = {
    "4510": ("Application development", "APP"),
    "4520": ("Enterprise applications", "APP"),
    "4530": ("Data center", "INF"),
    "4540": ("Network", "INF"),
    "4550": ("Security operations", "SEC"),
    "4560": ("IT governance office", "GOV"),
    "4570": ("Digital innovation lab", ""),
}

# File 09's mapping moves one GL account to another budget line and one cost
# center to another department.
MOVED_GL, MOVED_TO_CI = "610300", "2201"
MOVED_CC, MOVED_TO_DEPT = "4540", "SEC"

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
GL_COLUMN, DATE_COLUMN, PERIOD_COLUMN, AMOUNT_COLUMN = 1, 6, 8, 12

# The reasons the app gives for a skipped row (ENG-02 5).
INVALID_DATE = "Invalid date"
DECIMALS = "More than 3 decimal places"
UNREADABLE_CODE = "Unreadable code"
REPEAT = "Repeats row"


@dataclass(frozen=True)
class Doc:
    """One SAP document: its day of the month, type, and lines as
    (GL account, cost center, amount, text).
    """

    day: int
    kind: str  # KR invoice, KG credit memo, SA journal
    parts: tuple[tuple[str, str, str, str], ...]
    doc_days: int = 3  # how many days the document date is before posting
    bad: str = ""  # the skipped-row reason file 10 gives it


@dataclass(frozen=True)
class Line:
    """One spend line, as true values. row_cells adds SAP's formatting."""

    gl: str
    doc: str
    item: int
    kind: str
    doc_date: date
    posted: date
    cc: str
    reference: str
    amount: Decimal
    text: str
    skipped: str = ""  # why the app skips it, or "" for a line that counts
    cells: tuple[tuple[int, str | int], ...] = ()  # cells written instead

    @property
    def key(self) -> tuple[int, str, int]:
        """The line key's parts that differ: fiscal year, document, item."""
        return (self.posted.year, self.doc, self.item)

    @property
    def month(self) -> tuple[int, int]:
        return (self.posted.year, self.posted.month)


# Each month's own documents, beside the ones every month or quarter has.
OWN: dict[tuple[int, int], list[Doc]] = {
    (2025, 7): [
        Doc(2, "KR", (("610100", "4520", "185000.000", "ERP licence renewal"),), 5),
        Doc(21, "KR", (("620100", "4530", "87500.000", "Server purchase"),)),
    ],
    (2025, 8): [
        Doc(
            11,
            "KR",
            (("650100", "4550", "5600.000", "دورة تدريبية في الأمن السيبراني"),),
        ),
        Doc(22, "KR", (("640100", "4560", "18750.000", "IT strategy review"),)),
    ],
    (2025, 9): [
        Doc(
            9,
            "KR",
            (
                ("620100", "4510", "24360.000", "Developer laptops"),
                ("620100", "4520", "12180.000", "Analyst laptops"),
            ),
        ),
        Doc(17, "KR", (("610100", "4510", "8750.500", "Design software licences"),)),
    ],
    (2025, 10): [
        Doc(20, "KR", (("640100", "4550", "26400.000", "Penetration test"),)),
        Doc(23, "KG", (("610200", "4520", "-3800.000", "Service credit, Q3 outage"),)),
    ],
    (2025, 11): [
        Doc(5, "KR", (("620200", "4540", "2000.000", "صيانة أجهزة الشبكة"),)),
        Doc(24, "KR", (("650100", "4560", "3150.750", "Project management course"),)),
    ],
    (2025, 12): [
        Doc(10, "KR", (("610100", "4550", "23456.789", "تجديد ترخيص البرمجيات"),)),
        Doc(
            28,
            "SA",
            (
                ("630100", "4540", "1200.000", "Reclass to network"),
                ("630100", "4530", "-1200.000", "Reclass to network"),
            ),
            0,
        ),
    ],
    (2026, 1): [
        # Dated 28.12.2025 and posted 05.01.2026: the posting date counts.
        Doc(5, "KR", (("610100", "4510", "12400.000", "Licence renewal"),), 8),
        Doc(22, "KG", (("610100", "4510", "-1250.000", "Licence overcharge refund"),)),
    ],
    (2026, 2): [
        Doc(3, "KR", (("620100", "4540", "31275.400", "Network switches"),)),
        Doc(19, "KR", (("650100", "4510", "4200.000", "Cloud architecture course"),)),
    ],
    (2026, 3): [
        Doc(10, "KR", (("640100", "4520", "21900.000", "ERP upgrade advisory"),)),
        Doc(24, "KR", (("610100", "4560", "6480.000", "Audit tool subscription"),)),
    ],
    (2026, 4): [
        Doc(8, "KR", (("620100", "4550", "42180.000", "Firewall appliances"),)),
        Doc(
            20,
            "KG",
            (("620200", "4530", "-3500.000", "Maintenance credit, unused hours"),),
        ),
    ],
    (2026, 5): [
        Doc(13, "KR", (("650100", "4550", "7350.000", "Incident response training"),)),
        Doc(27, "KR", (("610100", "4520", "15600.000", "CRM licences"),)),
    ],
    # 06: GL accounts with no budget line.
    (2026, 6): [
        Doc(
            9,
            "KR",
            (
                ("640200", "4510", "15200.000", "Contract developer, May"),
                ("640200", "4520", "8400.000", "Contract tester, May"),
            ),
        ),
        Doc(16, "KR", (("650200", "4560", "2780.000", "Technology conference"),)),
        Doc(23, "KR", (("650200", "4510", "1945.300", "Developer conference travel"),)),
    ],
    # 07: cost centers outside IDS.
    (2026, 7): [
        Doc(8, "KR", (("610100", "3100", "8750.500", "Finance reporting licences"),)),
        Doc(14, "KR", (("620100", "3100", "6300.000", "Laptops for finance"),)),
        Doc(20, "KR", (("660100", "3200", "640.500", "Printer toner, HR"),)),
        Doc(27, "KR", (("650100", "3200", "2340.250", "HR systems training"),)),
    ],
    # 08: a cost center with no department.
    (2026, 8): [
        Doc(7, "KR", (("620100", "4570", "9450.000", "Test devices for the lab"),)),
        Doc(19, "KR", (("640100", "4570", "6200.000", "Innovation workshop"),)),
        Doc(25, "KR", (("660100", "4570", "412.750", "Prototyping supplies"),)),
    ],
    # 09: its mapping changes 610300's budget line and 4540's department.
    (2026, 9): [
        Doc(15, "KR", (("620100", "4540", "11640.000", "Wireless access points"),)),
    ],
    # 10: rows the app skips, each with a readable amount, so it reconciles.
    (2026, 10): [
        Doc(
            12,
            "KR",
            (("660100", "4560", "640.500", "Printer toner"),),
            bad=INVALID_DATE,
        ),
        Doc(14, "KR", (("650100", "4560", "1980.000", "Audit standards course"),)),
        Doc(
            17,
            "KR",
            (("650100", "4510", "1250.4567", "Online security course"),),
            bad=DECIMALS,
        ),
        Doc(
            21,
            "KR",
            (("620200", "4530", "870.000", "Rack cabling"),),
            bad=UNREADABLE_CODE,
        ),
    ],
}


def recurring(year: int, month: int) -> list[Doc]:
    """The documents every month has, and those every quarter has."""
    i = (year - 2025) * 12 + month - 7  # 0 for July 2025
    name = MONTH_NAMES[month - 1]
    cloud = Decimal("5980.250") + Decimal("61.375") * i
    phones = Decimal("4120.350") + Decimal("23.125") * (i % 5)
    docs = [
        Doc(
            4,
            "KR",
            (
                ("630200", "4530", "9800.000", f"Data line, data center, {name}"),
                ("630200", "4540", "5400.000", f"Data line, branch offices, {name}"),
            ),
        ),
        Doc(6, "KR", (("610300", "4510", str(cloud), f"Cloud hosting, {name}"),)),
        Doc(
            26,
            "KR",
            (("630100", "4540", str(phones), f"Mobile and fixed lines, {name}"),),
        ),
    ]
    if i % 2 == 0:
        center = ("4560", "4530", "4510", "4520", "4550")[i // 2 % 5]
        amount = Decimal("640.500") + Decimal("95.250") * (i // 2 % 4)
        text = ("Printer toner", "Cables and adapters")[i // 2 % 2]
        docs.append(Doc(12, "KR", (("660100", center, str(amount), text),)))
    if month in (1, 4, 7, 10):
        quarter = (month + 2) // 3
        docs.append(
            Doc(
                15, "KR", (("610200", "4520", "45600.000", f"ERP support, Q{quarter}"),)
            )
        )
        docs.append(
            Doc(
                18,
                "KR",
                (("620200", "4530", "12750.000", f"Server maintenance, Q{quarter}"),),
            )
        )
    return docs


def month_lines(year: int, month: int) -> list[Line]:
    """Every line posted in one month, its documents numbered by day."""
    docs = sorted(
        recurring(year, month) + OWN.get((year, month), []), key=lambda d: d.day
    )
    lines = []
    for number, doc in enumerate(docs, start=1):
        posted = date(year, month, doc.day)
        prefix = "01" if doc.kind == "SA" else "51"
        doc_number = f"{prefix}{year % 100:02d}{month:02d}{number:04d}"
        letters = {"KR": "INV", "KG": "CN", "SA": "JV"}[doc.kind]
        reference = f"{letters}-{year % 100:02d}{month:02d}-{number:03d}"
        first_item = 1 if doc.kind == "SA" else 2  # an invoice's line 1 is the vendor's
        for offset, (gl, cc, amount, text) in enumerate(doc.parts):
            line = Line(
                gl=gl,
                doc=doc_number,
                item=first_item + offset,
                kind=doc.kind,
                doc_date=posted - timedelta(days=doc.doc_days),
                posted=posted,
                cc=cc,
                reference=reference,
                amount=Decimal(amount),
                text=text,
            )
            lines.append(spoiled(line, doc.bad))
    return lines


def spoiled(line: Line, bad: str) -> Line:
    """The line with the cells that make the app skip it for the reason bad."""
    if bad == INVALID_DATE:  # 31 September isn't a day
        cells = ((DATE_COLUMN, "31.09.2026"), (PERIOD_COLUMN, 9))
    elif bad == DECIMALS:
        cells = ((AMOUNT_COLUMN, f"{line.amount:,}"),)
    elif bad == UNREADABLE_CODE:
        cells = ((GL_COLUMN, "#N/A"),)
    else:
        return line
    return replace(line, skipped=bad, cells=cells)


def lines_in(months: list[tuple[int, int]]) -> list[Line]:
    """Every line in the months, the way SAP lists them: by GL account, then
    posting date, document and item.
    """
    lines = [line for year, month in months for line in month_lines(year, month)]
    return sorted(lines, key=lambda line: (line.gl, line.posted, line.doc, line.item))


def months_from(first: tuple[int, int], count: int) -> list[tuple[int, int]]:
    """count months from first: months_from((2025, 11), 3) runs to January."""
    year, month = first
    found = []
    for _ in range(count):
        found.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return found


@dataclass(frozen=True)
class DemoFile:
    """One file to upload, with what it shows."""

    name: str
    shows: str
    lines: list[Line]
    mapping: dict[str, dict] | None = None  # {"gl": ..., "cc": ...}


def mapping(gl: dict[str, tuple[str, str]], cc: dict[str, tuple[str, str]]) -> dict:
    return {"gl": gl, "cc": cc}


def with_repeat(lines: list[Line], gl: str) -> list[Line]:
    """The lines with the first line on gl written a second time, right after
    it, as an export that lists one line twice would.
    """
    first = next(i for i, line in enumerate(lines) if line.gl == gl)
    repeat = replace(lines[first], skipped=REPEAT)
    return lines[: first + 1] + [repeat] + lines[first + 1 :]


def demo_files() -> list[DemoFile]:
    """Every file, in the order to upload them."""
    changed_gl = dict(GL_ACCOUNTS)
    changed_gl[MOVED_GL] = (GL_ACCOUNTS[MOVED_GL][0], MOVED_TO_CI)
    changed_cc = dict(COST_CENTERS)
    changed_cc[MOVED_CC] = (COST_CENTERS[MOVED_CC][0], MOVED_TO_DEPT)
    windows = [(2025, 7), (2025, 9), (2025, 11), (2026, 1), (2026, 3)]
    samples = [
        DemoFile(
            f"{n:02d} Sample - {label(months_from(start, 3))}.xlsx",
            "Clean: every line mapped and inside IDS",
            lines_in(months_from(start, 3)),
        )
        for n, start in enumerate(windows, start=1)
    ]
    return [
        DemoFile(
            "00 Mapping.xlsx",
            "The GL and cost center mappings on their own",
            [],
            mapping(GL_ACCOUNTS, COST_CENTERS),
        ),
        *samples,
        DemoFile(
            "06 Exceptions - No budget line - Jun 2026.xlsx",
            "GL accounts with no budget line",
            lines_in([(2026, 6)]),
        ),
        DemoFile(
            "07 Exceptions - Outside IDS - Jul 2026.xlsx",
            "Cost centers outside IDS",
            lines_in([(2026, 7)]),
        ),
        DemoFile(
            "08 Exceptions - No department - Aug 2026.xlsx",
            "A cost center with no department",
            lines_in([(2026, 8)]),
        ),
        DemoFile(
            "09 Exceptions - Mapping changes - Sep 2026.xlsx",
            "A budget-line change and a department change between files",
            lines_in([(2026, 9)]),
            mapping(changed_gl, changed_cc),
        ),
        DemoFile(
            "10 Exceptions - Skipped rows - Oct 2026.xlsx",
            "Rows skipped while reading",
            with_repeat(lines_in([(2026, 10)]), "610300"),
        ),
    ]


def label(months: list[tuple[int, int]]) -> str:
    """'Jul to Sep 2025', or 'Nov 2025 to Jan 2026' across a year."""
    (y1, m1), (y2, m2) = months[0], months[-1]
    first, last = MONTH_NAMES[m1 - 1][:3], MONTH_NAMES[m2 - 1][:3]
    if y1 == y2:
        return f"{first} to {last} {y2}"
    return f"{first} {y1} to {last} {y2}"


# --- Writing the workbooks ---------------------------------------------------


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


def row_cells(line: Line) -> list[str | int]:
    cells: list[str | int] = [
        COMPANY,
        sap_code(line.gl),
        sap_code(line.doc),
        f"{line.item:03d}",
        line.kind,
        sap_date(line.doc_date),
        sap_date(line.posted),
        line.posted.year,
        line.posted.month,
        sap_code(line.cc),
        line.reference,
        line.text,
        sap_amount(line.amount),
        "OMR",
    ]
    for column, value in line.cells:
        cells[column] = value
    return cells


def control_total(lines: list[Line]) -> Decimal:
    """The file's total row: every line, skipped ones too, as SAP adds them,
    rounded to the baisa as SAP shows it.
    """
    return sum((line.amount for line in lines), Decimal("0")).quantize(Decimal("0.001"))


def write_row(sheet, row: int, cells: list) -> None:
    """Write text cells as text, numbers as numbers, and nothing for None."""
    for column, value in enumerate(cells):
        if isinstance(value, str):
            sheet.write_string(row, column, value)
        elif isinstance(value, int):
            sheet.write_number(row, column, value)


def readme_lines(file: DemoFile) -> list[str]:
    lines = [
        "IDS Spend Audit demo file. Every value in it is made up.",
        f"It shows: {file.shows}.",
        "Made by Demo/make_demo.py.",
        "Demo/README.md gives the order to upload the files in.",
    ]
    if not file.lines:
        lines.append("It has no spend lines: upload it with Upload a mapping.")
    elif file.mapping is None:
        lines.append(
            "It has no mapping sheets, so its lines take the latest approved "
            "mapping, such as 00 Mapping's."
        )
    return lines


def write_xlsx(file: DemoFile, path: Path) -> None:
    """Write one file: README, Transactions if it has lines, and the mapping
    sheets if it has them.
    """
    workbook = xlsxwriter.Workbook(path, {"in_memory": True})
    workbook.set_properties({"created": CREATED})

    readme = workbook.add_worksheet("README")
    for row, text in enumerate(readme_lines(file)):
        readme.write_string(row, 0, text)
    if file.lines:
        write_transactions(workbook, file)
    if file.mapping is not None:
        write_mappings(workbook, file.mapping)
    workbook.close()


def write_transactions(workbook, file: DemoFile) -> None:
    """The spend sheet: two title lines, the header on row 4, then the lines
    and the total row.
    """
    sheet = workbook.add_worksheet("Transactions")
    counted = [line for line in file.lines if not line.skipped]
    first = min(line.posted for line in counted)
    last = max(line.posted for line in counted)
    write_row(sheet, 0, ["G/L Account Line Item Display"])
    write_row(
        sheet,
        1,
        [f"Company code {COMPANY}, posting date {sap_date(first)} to {sap_date(last)}"],
    )
    write_row(sheet, 3, HEADER)
    for row, line in enumerate(file.lines, start=4):
        write_row(sheet, row, row_cells(line))
    total = control_total(file.lines)
    write_row(
        sheet, 4 + len(file.lines), ["Total"] + [None] * 11 + [sap_amount(total), "OMR"]
    )


def write_mappings(workbook, mapping: dict) -> None:
    """The GL Mapping and Cost Center Mapping sheets."""
    gl_sheet = workbook.add_worksheet("GL Mapping")
    write_row(
        gl_sheet,
        0,
        ["G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name"],
    )
    for row, (code, (name, item)) in enumerate(mapping["gl"].items(), start=1):
        item_cells = [int(item), COMMITMENT_ITEMS[item]] if item else [None, None]
        write_row(gl_sheet, row, [int(code), name, *item_cells])
    cc_sheet = workbook.add_worksheet("Cost Center Mapping")
    write_row(
        cc_sheet,
        0,
        ["Cost Center", "Cost Center Name", "Department Code", "Department Name"],
    )
    for row, (code, (name, dept)) in enumerate(mapping["cc"].items(), start=1):
        dept_cells = [dept, DEPARTMENTS[dept]] if dept else [None, None]
        write_row(cc_sheet, row, [int(code), name, *dept_cells])


# --- The figures for README.md -----------------------------------------------


@dataclass
class Step:
    """What uploading one file does to a report holding every file before it."""

    file: DemoFile
    replaced: tuple[int, Decimal]  # replace by period: postings, amount
    duplicates: tuple[int, Decimal]  # line key: lines, amount
    spend_after: Decimal


def months_covered(lines: list[Line]) -> set[tuple[int, int]]:
    """Each month from the first line's to the last line's (ENG-02 7.3)."""
    first = min(line.month for line in lines)
    last = max(line.month for line in lines)
    found, month = set(), first
    while month <= last:
        found.add(month)
        month = months_from(month, 2)[1]
    return found


def total(lines) -> Decimal:
    return sum((line.amount for line in lines), Decimal("0.000"))


def steps(files: list[DemoFile]) -> list[Step]:
    """Upload every file in order, in both duplicate modes, and check the two
    reports end each step with the same postings.
    """
    by_period: list[Line] = []
    by_key: dict[tuple, Line] = {}
    found = []
    for file in files:
        counted = [line for line in file.lines if not line.skipped]
        months = months_covered(counted) if counted else set()
        replaced = [line for line in by_period if line.month in months]
        by_period = [line for line in by_period if line.month not in months] + counted
        duplicates = [line for line in counted if line.key in by_key]
        for line in duplicates:
            assert by_key[line.key].amount == line.amount, line
        by_key.update({line.key: line for line in counted if line.key not in by_key})
        assert sorted(map(repr, by_period)) == sorted(map(repr, by_key.values()))
        found.append(
            Step(
                file,
                (len(replaced), total(replaced)),
                (len(duplicates), total(duplicates)),
                total(by_period),
            )
        )
    return found


def omr(amount: Decimal) -> str:
    return f"{amount:,.3f}"


def spend_on(lines: list[Line], test) -> str:
    """'15,200.000 OMR on 2 lines' for the lines that pass test."""
    chosen = [line for line in lines if test(line)]
    noun = "line" if len(chosen) == 1 else "lines"
    return f"{omr(total(chosen))} OMR on {len(chosen)} {noun}"


def readme(files: list[DemoFile]) -> str:
    found = steps(files)
    by_name = {step.file.name[:2]: step for step in found}
    rows = []
    for step in found:
        counted = [line for line in step.file.lines if not line.skipped]
        if counted:
            first = min(line.posted for line in counted)
            last = max(line.posted for line in counted)
            dates = f"{sap_date(first)} to {sap_date(last)}"
            total_row = omr(control_total(step.file.lines))
        else:
            dates, total_row = "None: a mapping", "None"
        rows.append(
            f"| `{step.file.name}` | {dates} "
            f"| {len(step.file.lines)} | {total_row} "
            f"| {step.replaced[0]}, {omr(step.replaced[1])} "
            f"| {step.duplicates[0]}, {omr(step.duplicates[1])} "
            f"| {omr(step.spend_after)} |"
        )

    six = by_name["06"].file.lines
    seven = by_name["07"].file.lines
    eight = by_name["08"].file.lines
    ten = by_name["10"].file.lines
    every = [
        line
        for step in found[: found.index(by_name["09"]) + 1]
        for line in step.file.lines
        if not line.skipped
    ]
    # Every line counts once in both modes, so the lines that count after 09
    # are the distinct lines of 00 to 09.
    after_nine = list({line.key: line for line in every}.values())
    nine_own = {line.key for line in by_name["09"].file.lines}
    moved_gl = [line for line in after_nine if line.gl == MOVED_GL]
    moved_cc = [line for line in after_nine if line.cc == MOVED_CC]
    gl_before = GL_ACCOUNTS[MOVED_GL][1]
    cc_before = COST_CENTERS[MOVED_CC][1]

    skipped = []
    for row, line in enumerate(ten, start=5):
        if line.skipped == REPEAT:
            reason = f"Repeats row {row - 1}"
        else:
            reason = line.skipped
        if line.skipped:
            amount = (
                f"{line.amount:,}" if line.skipped == DECIMALS else omr(line.amount)
            )
            skipped.append(f"  - Row {row}, {reason}: {amount} OMR")

    return "\n".join(
        [
            "# Demo files",
            "",
            "Files to upload into IDS Spend Audit for a demo. Every value in them "
            "is made up. The spend files are laid out like the test sample "
            "(ENG-02 12.3): a Transactions sheet below two title lines, ending in "
            "a total row that matches its lines. To make them again, run "
            "`uv run python Demo/make_demo.py` from the repository root.",
            "",
            "## The order",
            "",
            "1. In a new report, upload `00 Mapping.xlsx` with **Upload a "
            "mapping**, and approve it. It's the GL and cost center mappings on "
            "their own (ENG-02 8.5), so its preview has no rows to reconcile.",
            "2. Upload `01` to `05` with **Upload a file**, approving each before "
            "the next. Approving `01` chooses the report's duplicate mode: to try "
            "both, make two reports, choose **Replace by period** in one and "
            "**Line key** in the other, and upload the same files to both. The "
            "samples have no mapping sheets, so their lines take `00`'s mapping. "
            "Every line is mapped to a budget line and a department, so they add "
            "no exception, and the Exceptions screen stays empty.",
            "3. Upload `06` to `10` the same way. Each fills one list of the "
            "Exceptions screen, and its preview shows it before approval.",
            "",
            "**Both modes.** Each sample covers three months, and starts with the "
            "month the one before it ended with, listed again in full and "
            "unchanged. By period, a sample replaces the postings in its months. "
            "By line key, it adds only the lines not already in the report and "
            "counts the rest as duplicates, with the same amounts. Both reports "
            "therefore end each step with the same figures.",
            "",
            "## The files",
            "",
            "Each figure assumes the report holds every file above it. Amounts "
            "are in OMR.",
            "",
            "| File | Posting dates | Lines | Control total | Replaced (by period) "
            "| Duplicates (line key) | Actual spend after approval |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            "## What each exception file shows",
            "",
            "- `06`, GL accounts with no budget line: `640200` IT contractors is "
            "missing from the GL mapping, with "
            f"{spend_on(six, lambda x: x.gl == '640200')}, "
            "and `650200` Conferences and travel is listed without a commitment "
            f"item, with {spend_on(six, lambda x: x.gl == '650200')}.",
            "- `07`, cost centers outside IDS: `3100` with "
            f"{spend_on(seven, lambda x: x.cc == '3100')}, and `3200` with "
            f"{spend_on(seven, lambda x: x.cc == '3200')}. Neither is on the cost "
            "center mapping.",
            "- `08`, a cost center with no department: `4570` Digital innovation "
            "lab is on the cost center mapping with no department code, with "
            f"{spend_on(eight, lambda x: x.cc == '4570')}.",
            "- `09`, changes between files: a spend file with its mapping sheets "
            "inside, which are `00`'s with two "
            f"changes. `{MOVED_GL}` Cloud services moves from `{gl_before}` to "
            f"`{MOVED_TO_CI}`, moving {omr(total(moved_gl))} OMR, "
            f"{omr(total(x for x in moved_gl if x.key not in nine_own))} OMR of "
            f"it already approved. `{MOVED_CC}` Network moves from `{cc_before}` "
            f"to `{MOVED_TO_DEPT}`, moving {omr(total(moved_cc))} OMR, "
            f"{omr(total(x for x in moved_cc if x.key not in nine_own))} OMR of it "
            "already approved. Its mapping then applies to every line in the "
            "report. The Exceptions screen lists each change under `00` and "
            "`09`, the two files whose mapping lists the code, each with its own "
            "lines on it that still count: none for `00`, which has no lines, "
            "and September's for `09`.",
            "- `10`, skipped rows, each with a readable amount, so the file still "
            "reconciles and can be approved:",
            *skipped,
            "",
            "The mapping lists `650200` and `4570` from the start, so `06` and "
            "`08` need no mapping of their own. No sample posts to them, so they "
            "show only once `06` and `08` do.",
            "",
        ]
    )


def main() -> None:
    files = demo_files()
    for file in files:
        write_xlsx(file, HERE / file.name)
    (HERE / "README.md").write_text(readme(files), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
