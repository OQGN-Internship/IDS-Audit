"""Building an upload's GL and cost center mappings (ENG-02 5, Mapping rows).

Rows come from the mapping sheets and, on an Excel spend sheet, from its
commitment item and department columns (ENG-02 4.2). Each code ends up with
one row, and each code with one name, so posting_resolved's join can never
count a posting twice.

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from ids_audit.pipeline.cleaning import cell_text, clean_code
from ids_audit.pipeline.columns import CC_MAP, GL_MAP, Layout
from ids_audit.pipeline.readers import Cell
from ids_audit.pipeline.results import (
    LISTED_WITH_DIFFERENT_VALUES,
    NAMES_DIFFER,
    SHEET_AND_SPEND_DISAGREE,
    UNREADABLE_CODE,
    MappingIssue,
    OtherRow,
    SkippedRow,
)


@dataclass(frozen=True)
class MapFields:
    """The four fields of one mapping: a code, the value it maps to, and the
    two names. For the GL mapping, a GL account maps to a commitment item.
    """

    sheet_type: str
    code: str
    value: str
    code_name: str
    value_name: str


GL_FIELDS = MapFields(GL_MAP, "gl_code", "ci_code", "gl_name", "ci_name")
CC_FIELDS = MapFields(CC_MAP, "cost_center_code", "dept_code", "cc_name", "dept_name")


@dataclass(frozen=True)
class Entry:
    """One row's mapping of a code, before repeats are settled."""

    code: str
    value: str
    code_name: str
    value_name: str
    sheet: str
    row_number: int

    @property
    def place(self) -> tuple[str, int]:
        return (self.sheet, self.row_number)


@dataclass(frozen=True)
class MapRow:
    """One code's row in a mapping, once repeats are settled."""

    code: str
    code_name: str
    value: str
    value_name: str


def rows_below_header(layout: Layout) -> Iterator[tuple[int, tuple[Cell, ...]]]:
    """Yield each row under a sheet's header row with its row number."""
    for index in range(layout.header_row, len(layout.sheet.rows)):
        yield index + 1, layout.sheet.rows[index]


def is_blank(row: Iterable[Cell]) -> bool:
    """Return True when every cell is empty or only spaces."""
    return all(cell_text(cell) == "" for cell in row)


def cell_of(layout: Layout, row: Sequence[Cell], field: str) -> Cell:
    """Return a row's cell for a field, or "" if the sheet has no column for it
    or the row ends before it, which a picked column can (ENG-02 4.5).
    """
    index = layout.columns.get(field)
    return row[index] if index is not None and index < len(row) else ""


def read_mapping_sheet(
    layout: Layout, fields: MapFields
) -> tuple[list[Entry], list[SkippedRow], list[OtherRow]]:
    """Read a mapping sheet's rows into entries, skipped rows and other rows.

    A blank row is left out. A row with no code is an other row. A row whose
    code, or the code it maps to, can't be read is skipped with no amount, so
    it doesn't count in reconciliation.
    """
    entries, skipped, other = [], [], []
    name = layout.sheet.name
    for row_number, row in rows_below_header(layout):
        if is_blank(row):
            continue
        code_cell = cell_of(layout, row, fields.code)
        if cell_text(code_cell) == "":
            other.append(OtherRow(name, row_number, None, row))
            continue
        try:
            code = clean_code(code_cell)
            value = clean_code(cell_of(layout, row, fields.value))
        except ValueError:
            skipped.append(
                SkippedRow(name, row_number, UNREADABLE_CODE, None, False, row)
            )
            continue
        entries.append(
            Entry(
                code,
                value,
                cell_text(cell_of(layout, row, fields.code_name)),
                cell_text(cell_of(layout, row, fields.value_name)),
                name,
                row_number,
            )
        )
    return entries, skipped, other


def settle(
    fields: MapFields, sheet_entries: Sequence[Entry], spend_entries: Sequence[Entry]
) -> tuple[list[MapRow], list[MappingIssue]]:
    """Return one row per code, and the issues found on the way.

    The mapping sheet's rows come first. A spend row only adds a code the
    sheet doesn't list, and one that disagrees with the sheet is an issue. A
    code listed again keeps its first row, and an issue lists every row of a
    code listed with different values. Each code and value keeps the first
    name given, and a code mapped to no value has no value name.
    """
    on_sheet: dict[str, Entry] = {}
    for entry in sheet_entries:
        on_sheet.setdefault(entry.code, entry)
    used = list(sheet_entries)
    disagreeing: dict[str, list[Entry]] = {}
    for entry in spend_entries:
        if entry.code not in on_sheet:
            used.append(entry)
        elif entry.value != on_sheet[entry.code].value:
            disagreeing.setdefault(entry.code, [on_sheet[entry.code]]).append(entry)

    by_code: dict[str, list[Entry]] = {}
    for entry in used:
        by_code.setdefault(entry.code, []).append(entry)

    issues = [
        MappingIssue(
            LISTED_WITH_DIFFERENT_VALUES,
            fields.code,
            code,
            tuple(entry.place for entry in entries),
        )
        for code, entries in by_code.items()
        if len({entry.value for entry in entries}) > 1
    ]
    issues += [
        MappingIssue(
            SHEET_AND_SPEND_DISAGREE,
            fields.code,
            code,
            tuple(entry.place for entry in entries),
        )
        for code, entries in disagreeing.items()
    ]
    code_names, code_issues = first_names(
        fields.code_name, ((e.code, e.code_name, e.place) for e in used)
    )
    value_names, value_issues = first_names(
        fields.value_name, ((e.value, e.value_name, e.place) for e in used if e.value)
    )
    rows = [
        MapRow(
            code,
            code_names.get(code, ""),
            entries[0].value,
            value_names.get(entries[0].value, ""),
        )
        for code, entries in by_code.items()
    ]
    return rows, issues + code_issues + value_issues


def first_names(
    field: str, named: Iterable[tuple[str, str, tuple[str, int]]]
) -> tuple[dict[str, str], list[MappingIssue]]:
    """Return the first name given to each code, and an issue for each code
    given more than one name. Empty names don't count.
    """
    names: dict[str, list[tuple[str, tuple[str, int]]]] = {}
    for code, name, place in named:
        if name:
            names.setdefault(code, []).append((name, place))
    issues = [
        MappingIssue(NAMES_DIFFER, field, code, tuple(place for _, place in given))
        for code, given in names.items()
        if len({name for name, _ in given}) > 1
    ]
    return {code: given[0][0] for code, given in names.items()}, issues
