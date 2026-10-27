"""Finding each sheet's header row, its columns and its type (ENG-02 4.2 to 4.5).

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

import dataclasses
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ids_audit.pipeline.cleaning import cell_text
from ids_audit.pipeline.readers import Cell, FileUnreadable, Sheet

# The sheet types (ENG-02 4.2).
SPEND = "spend"
GL_MAP = "gl_map"
CC_MAP = "cc_map"
SHEET_TYPE_NAMES = {
    SPEND: "spend sheets",
    GL_MAP: "GL mapping sheets",
    CC_MAP: "cost center mapping sheets",
}

# How far down a sheet the header row is looked for (ENG-02 4.3).
HEADER_SEARCH_ROWS = 25

# The fields each sheet type reads. A text file's spend sheet reads no mapping
# fields (ENG-02 4.1), and an Excel spend sheet reads them too (4.2).
SPEND_FIELDS = (
    "gl_code",
    "cost_center_code",
    "amount",
    "posting_date",
    "fiscal_year",
    "period",
    "document_number",
    "line_item",
    "company_code",
    "value_type",
    "currency",
    "item_text",
)
MAPPING_FIELDS = ("ci_code", "gl_name", "ci_name", "dept_code", "cc_name", "dept_name")
FIELDS_READ = {
    SPEND: SPEND_FIELDS + MAPPING_FIELDS,
    GL_MAP: ("gl_code", "gl_name", "ci_code", "ci_name"),
    CC_MAP: ("cost_center_code", "cc_name", "dept_code", "dept_name"),
}

# A code field, and the name field that takes a second column for the same
# subject, such as Department beside Department Code (ENG-02 4.4).
NAME_FIELD = {
    "gl_code": "gl_name",
    "ci_code": "ci_name",
    "cost_center_code": "cc_name",
    "dept_code": "dept_name",
}


# A column's letters, as Excel names it.
LETTERS = re.compile(r"[A-Z]+")


@dataclass(frozen=True)
class Choice:
    """Columns picked for a sheet whose header is exactly header: each field's
    column letter, such as {"amount": "M"} (ENG-02 4.5).
    """

    header: tuple[str, ...]
    columns: Mapping[str, str]


@dataclass(frozen=True)
class Layout:
    """Where one classified sheet's header row and columns are.

    header_row is the row number Excel shows, and columns maps each field the
    sheet reads to its column's index, 0 for column A. header is the header
    row as text, and picked the columns a Choice gave, by field.
    """

    sheet: Sheet
    sheet_type: str
    header_row: int
    columns: Mapping[str, int]
    header: tuple[str, ...] = ()
    picked: Mapping[str, str] = dataclasses.field(default_factory=dict)

    def letters(self) -> dict[str, str]:
        """Return each field's column as Excel names it, such as "B"."""
        return {field: column_letter(index) for field, index in self.columns.items()}


@dataclass(frozen=True)
class ColumnProblem:
    """A required field no column matched, or a field two columns matched."""

    sheet: str
    field: str
    columns: tuple[str, ...]  # the matching columns' letters, none if missing

    def __str__(self) -> str:
        where = f"sheet {self.sheet}" if self.sheet else "the file"
        if self.columns:
            listed = " and ".join(self.columns)
            each = "both" if len(self.columns) == 2 else "all"
            return f"In {where}, columns {listed} {each} match {self.field}."
        return f"In {where}, no column matches {self.field}."


class NeedsColumns(Exception):
    """A classified sheet is missing a required field, or has a field matched
    by two columns, so the upload stops at needs_columns (ENG-02 4.5).

    headers holds the header, as text, of each sheet with a problem, so a
    column picked for it can be saved against that header.
    """

    def __init__(
        self,
        problems: Sequence[ColumnProblem],
        headers: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.problems = tuple(problems)
        self.headers = dict(headers or {})
        super().__init__(" ".join(str(problem) for problem in self.problems))


def find_layouts(
    sheets: Sequence[Sheet],
    settings: Mapping,
    *,
    is_text: bool,
    choices: Sequence[Choice] = (),
    mapping_only: bool = False,
) -> dict[str, Layout]:
    """Return the layout of each classified sheet, by sheet type.

    A sheet whose header is exactly a choice's takes the columns picked in the
    first such choice, in place of what the header names matched (ENG-02 4.5).
    Raise FileUnreadable when the file has no spend sheet or two sheets of one
    type (4.2), and NeedsColumns when a classified sheet's columns don't pass
    the column check (4.5). mapping_only is True for a mapping upload, whose
    file has mapping sheets and no spend sheet (8.5).
    """
    names = header_lookup(settings["header_names"])
    found: dict[str, list[tuple[Sheet, int, dict[str, list[int]]]]] = {
        SPEND: [],
        GL_MAP: [],
        CC_MAP: [],
    }
    headers: dict[str, tuple[str, ...]] = {}
    picked: dict[str, dict[str, str]] = {}
    for sheet in sheets:
        header = find_header_row(sheet, names)
        if header is None:
            continue  # no recognised header, such as a README sheet
        row_number, matches = header
        sheet_type = classify(set(matches.values()))
        if sheet_type is None:
            continue
        read = SPEND_FIELDS if is_text else FIELDS_READ[sheet_type]
        columns = field_columns(sheet.rows[row_number - 1], matches)
        headers[sheet.name] = header_text(sheet.rows[row_number - 1])
        choice = next((c for c in choices if c.header == headers[sheet.name]), None)
        if choice is not None:
            picked[sheet.name] = {f: c for f, c in choice.columns.items() if f in read}
            for f, letters in picked[sheet.name].items():
                columns[f] = [column_index(letters)]
        found[sheet_type].append(
            (sheet, row_number, {f: c for f, c in columns.items() if f in read})
        )

    if mapping_only and found[SPEND]:
        listed = " and ".join(sheet.name for sheet, _, _ in found[SPEND])
        raise FileUnreadable(
            f"The file has a spend sheet, {listed}, so it isn't a mapping on its "
            "own. Upload it with Upload a file, which reads its mapping sheets "
            "too."
        )
    if mapping_only and not (found[GL_MAP] or found[CC_MAP]):
        raise FileUnreadable(
            "The file has no mapping sheet: no sheet has a GL account or a cost "
            f"center column, and no amount, in its first {HEADER_SEARCH_ROWS} "
            "rows."
        )
    if not mapping_only and not found[SPEND]:
        raise FileUnreadable(
            "The file has no spend sheet: no sheet has a GL account and a cost "
            f"center column in its first {HEADER_SEARCH_ROWS} rows."
        )
    for sheet_type, candidates in found.items():
        if len(candidates) > 1:
            listed = " and ".join(sheet.name for sheet, _, _ in candidates)
            raise FileUnreadable(
                f"The file has {len(candidates)} {SHEET_TYPE_NAMES[sheet_type]}: "
                f"{listed}. Upload a file with one."
            )

    problems = [
        problem
        for sheet_type, candidates in found.items()
        for sheet, _, columns in candidates
        for problem in check_columns(sheet.name, sheet_type, columns, settings)
    ]
    if problems:
        raise NeedsColumns(problems, {p.sheet: headers[p.sheet] for p in problems})
    return {
        sheet_type: Layout(
            sheet,
            sheet_type,
            row_number,
            {field: found_columns[0] for field, found_columns in columns.items()},
            headers[sheet.name],
            picked.get(sheet.name, {}),
        )
        for sheet_type, candidates in found.items()
        for sheet, row_number, columns in candidates
    }


def normalize_header(cell: Cell) -> str:
    """Return a header in lower case with everything but letters and digits
    removed, so "G/L Account" and "gl account" are both "glaccount" (ENG-02 4.4).
    """
    return "".join(char for char in cell_text(cell).lower() if char.isalnum())


def header_lookup(header_names: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """Return each recognised header, normalized, with the field it names."""
    return {
        normalize_header(name): field
        for field, field_names in header_names.items()
        for name in field_names
    }


def find_header_row(
    sheet: Sheet, names: Mapping[str, str]
) -> tuple[int, dict[int, str]] | None:
    """Return the header row's number and the field each matching column
    names, or None if none of the first 25 rows has a recognised header.

    The row matching the most headers wins, and of two that match as many, the
    higher one (ENG-02 4.3). A header matches only as a whole cell.
    """
    best: tuple[int, dict[int, str]] | None = None
    for index, row in enumerate(sheet.rows[:HEADER_SEARCH_ROWS]):
        matches = {
            column: names[header]
            for column, cell in enumerate(row)
            if (header := normalize_header(cell)) in names
        }
        if matches and (best is None or len(matches) > len(best[1])):
            best = (index + 1, matches)
    return best


def classify(fields: set[str]) -> str | None:
    """Return a sheet's type from the fields its header has (ENG-02 4.2), or
    None for a sheet that's ignored. A text file that isn't a spend sheet has
    no spend sheet, whatever this returns.
    """
    has_gl, has_cc = "gl_code" in fields, "cost_center_code" in fields
    if has_gl and has_cc:
        return SPEND
    if "amount" in fields:
        return None
    if has_gl:
        return GL_MAP
    if has_cc:
        return CC_MAP
    return None


def field_columns(
    header: Sequence[Cell], matches: Mapping[int, str]
) -> dict[str, list[int]]:
    """Return each field's matching columns, left to right.

    When a code field has two columns and its name field has none, the one
    whose header ends in "code" stays the code and the other is the name
    (ENG-02 4.4). Any other repeat is left for the column check.
    """
    columns: dict[str, list[int]] = {}
    for column, field in sorted(matches.items()):
        columns.setdefault(field, []).append(column)
    for code_field, name_field in NAME_FIELD.items():
        pair = columns.get(code_field, [])
        if len(pair) != 2 or name_field in columns:
            continue
        ends_in_code = [normalize_header(header[c]).endswith("code") for c in pair]
        if ends_in_code.count(True) == 1:
            columns[code_field] = [pair[ends_in_code.index(True)]]
            columns[name_field] = [pair[ends_in_code.index(False)]]
    return columns


def check_columns(
    sheet_name: str,
    sheet_type: str,
    columns: Mapping[str, Sequence[int]],
    settings: Mapping,
) -> list[ColumnProblem]:
    """Return the column check's problems for one sheet (ENG-02 4.5): each
    required field no column matched, and each field two columns matched.
    """
    missing = [
        ColumnProblem(sheet_name, field, ())
        for field in required_fields(sheet_type, columns, settings)
        if field not in columns
    ]
    repeated = [
        ColumnProblem(sheet_name, field, tuple(map(column_letter, found)))
        for field, found in columns.items()
        if len(found) > 1
    ]
    return missing + repeated


def required_fields(
    sheet_type: str, columns: Mapping[str, Sequence[int]], settings: Mapping
) -> list[str]:
    """Return the fields a sheet type needs (ENG-02 4.4)."""
    if sheet_type == GL_MAP:
        return ["gl_code", "ci_code"]
    if sheet_type == CC_MAP:
        return ["cost_center_code", "dept_code"]
    required = ["gl_code", "cost_center_code", "amount"]
    if not ("fiscal_year" in columns and "period" in columns):
        required.append("posting_date")
    if settings["actual_spend_rule"] is not None:
        required.append("value_type")
    return required


def header_text(row: Sequence[Cell]) -> tuple[str, ...]:
    """Return a header row's cells as text, to the last one that isn't empty."""
    texts = [cell_text(cell) for cell in row]
    while texts and texts[-1] == "":
        texts.pop()
    return tuple(texts)


def column_index(letters: str) -> int:
    """Return a column's index from its letters: A is 0, AA is 26. Anything but
    capital letters is a ValueError.
    """
    if not LETTERS.fullmatch(letters):
        raise ValueError(f"{letters!r} isn't a column, such as A or AB.")
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def column_letter(index: int) -> str:
    """Return a column's letters as Excel shows them: 0 is A, 26 is AA."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters
