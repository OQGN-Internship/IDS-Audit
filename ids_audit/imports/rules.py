"""The Rules screen's content (ENG-03 4): a report's ruleset version as one
card per setting, in words, the header names by field with the sheets each is
needed on, and the history of its versions.

The words for each setting, field and value are pipeline/'s (ENG-02 3), and
each example is read with the cleaning's own functions, so it says what a
file would get.
"""

import datetime
import json
from dataclasses import dataclass, field

from django.db.models import Count

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.services import locked_reason
from ids_audit.pipeline import columns, ruleset
from ids_audit.pipeline.cleaning import classify_value_type, clean_amount, clean_date
from ids_audit.pipeline.ruleset import (
    DEFAULT_SETTINGS,
    FIELD_NAMES,
    SETTING_NAMES,
    value_in_words,
)
from ids_audit.reports.models import Report, Ruleset

# The settings a form changes on screen, a setting at a time. Header names
# change a field at a time, and outside_ids_rule has one value (ENG-02 3).
CHANGED_BY_FORM = (
    "date_order",
    "decimal_separator",
    "currency",
    "actual_spend_rule",
    "mapping_rule",
)

# When a change applies: to files received after it, for a setting that acts
# while a file is read, and at once for one that acts whenever the report is
# shown (ENG-02 3).
READ_APPLIES = (
    "To files received after the change. Files already received keep the "
    "version they were read with, and the figures they gave."
)
SHOWN_APPLIES = (
    "At once, to every figure of the report, earlier files' included, since "
    "it acts whenever the report is shown. So it can't change once the report "
    "has an open or a committed upload: choose it before the first upload."
)

DATE_EXAMPLE = "03.04.2026"
# The same amount written with each decimal separator: twelve thousand four
# hundred and a half.
AMOUNT_EXAMPLES = {".": "12,400.500", ",": "12.400,500"}


@dataclass(frozen=True)
class Card:
    """One setting as the Rules screen shows it: its name, what it decides,
    its value in words, an example built from that value, its default, when a
    change applies, and its key and value as a command writes them.

    lists holds the actual-spend rule's two lists of value types. fixed says
    why it can't change now, and changes whether a form changes it at all.
    """

    key: str
    name: str
    decides: str
    value: str
    example: str
    default: str
    applies: str
    code: str
    lists: tuple[tuple[str, tuple[str, ...]], ...] = ()
    fixed: str = ""
    changes: bool = True


def date_example(date_order: str) -> str:
    """Return how DATE_EXAMPLE is read with the date order, such as 3 April
    2026.
    """
    day = clean_date(DATE_EXAMPLE, date_order)
    return f"{day.day} {day:%B %Y}"


def amount_example(decimal_separator: str) -> str:
    """Return what the decimal separator makes of the example amount written
    each way: one is read, and the other can't be.
    """
    sentences = []
    for text in AMOUNT_EXAMPLES.values():
        try:
            amount = clean_amount(text, decimal_separator)
        except ValueError:
            sentences.append(
                f"{text} can't be read, and its row is skipped as an unreadable amount."
            )
        else:
            sentences.append(
                f"{text} is read as {amount:,.3f}, twelve thousand four hundred "
                "and a half."
            )
    return " ".join(sentences)


def actual_spend_example(rule) -> str:
    """Return what the actual-spend rule makes of a row's value type."""
    if rule is None:
        return (
            "A row counts as actual spend whatever its value type, and a file "
            "needs no Value type column."
        )
    sentences = [f"A row whose value type is {rule['actual'][0]} counts."]
    if rule["not_actual"]:
        sentences.append(
            f"One whose value type is {rule['not_actual'][0]} is kept, but never "
            "counted."
        )
    other = next(
        value
        for value in ("Plan", "Budget", "Forecast", "Other value")
        if classify_value_type(value, rule) is None
    )
    sentences.append(
        f"Any other value, such as {other}, is unclassified, and blocks approval "
        "until it's added to a list. The spend sheet must have a Value type column."
    )
    return " ".join(sentences)


def card(key: str, settings: dict, locked: bool) -> Card:
    """Return the card for one setting of a version, other than header_names.
    locked says whether the report has an open or a committed upload.
    """
    value = settings[key]
    base = {
        "key": key,
        "name": SETTING_NAMES[key],
        "value": value_in_words(key, value),
        "default": value_in_words(key, DEFAULT_SETTINGS[key]),
        "applies": READ_APPLIES,
        "code": f'"{key}": {json.dumps(value, ensure_ascii=False)}',
    }
    if key == "date_order":
        other = "month_first" if value == "day_first" else "day_first"
        return Card(
            **base,
            decides=(
                "How a date written as text, as in a CSV file, is read when its "
                "day and month could be either way round. A date in an Excel date "
                "cell, and one written year first, such as 2026-04-03, are always "
                "read as they are."
            ),
            example=(
                f"{DATE_EXAMPLE} is read as {date_example(value)}. "
                f"{ruleset.VALUE_NAMES[key][other]} would read it as "
                f"{date_example(other)}."
            ),
        )
    if key == "decimal_separator":
        return Card(
            **base,
            decides=(
                "Which mark separates the decimals in an amount written as text, "
                "as in a CSV file. The other mark may only separate thousands. An "
                "amount in an Excel number cell is read as its number, whatever "
                "this says."
            ),
            example=amount_example(value),
        )
    if key == "currency":
        other = "USD" if value != "USD" else "OMR"
        return Card(
            **base,
            decides=(
                "The currency every amount must be in. A row whose local currency "
                "column holds another currency is skipped, and blocks approval, "
                "since a file mixing currencies can't add up. A file with no local "
                "currency column is taken to be in this currency."
            ),
            example=(
                f"A row in {value} is read. A row in {other} is skipped as another "
                "currency."
            ),
        )
    if key == "actual_spend_rule":
        lists = ()
        if value is not None:
            lists = (
                ("Actual spend", tuple(value["actual"])),
                ("Not actual spend", tuple(value["not_actual"])),
            )
        return Card(
            **{
                **base,
                "value": "Every row counts" if value is None else "By value type",
            },
            decides=(
                "Which rows count as actual spend: every row, or only rows whose "
                "Value type column holds a value listed as actual spend. Rows "
                "listed as not actual spend are kept, but never counted."
            ),
            example=actual_spend_example(value),
            lists=lists,
        )
    if key == "mapping_rule":
        if value == "latest":
            example = (
                "If March's file maps GL account 610100 to budget line 2101, and "
                "April's to 2401, every 610100 line shows 2401 once April's is "
                "approved, March's lines too."
            )
        else:
            example = (
                "If March's file maps GL account 610100 to budget line 2101, and "
                "April's to 2401, March's lines show 2101 and April's 2401. A file "
                "with no mapping of its own uses the latest."
            )
        return Card(
            **{**base, "applies": SHOWN_APPLIES},
            decides=(
                "Which mapping gives each line its budget line and department, "
                "when a file maps a GL account or a cost center differently from "
                "an earlier file."
            ),
            example=example,
            fixed=locked_reason(key) if locked else "",
        )
    if key == "outside_ids_rule":
        return Card(
            **{**base, "applies": SHOWN_APPLIES},
            decides=(
                "Which cost centers are outside IDS. Their spend stays in the "
                "report, marked Outside IDS, and every screen can leave it out."
            ),
            example=(
                "If the cost center mapping in force lists 4510 and 4520, a line on "
                "cost center 3100 is outside IDS."
            ),
            fixed=(
                "This is the only rule there is for now. If OQGN marks its IDS cost "
                "centers another way, another rule is added to the application."
            ),
            changes=False,
        )
    raise ValueError(f"No card for {key}.")


def cards(version: Ruleset, locked: bool) -> list[Card]:
    """Return a card for each setting of the version but header_names, in the
    order a file meets them (ENG-03 4). locked says whether the report has an
    open or a committed upload, which fixes the settings shown with it.
    """
    return [
        card(key, version.settings, locked)
        for key in SETTING_NAMES
        if key != "header_names"
    ]


@dataclass(frozen=True)
class FieldRow:
    """One field of the header names: its key, its name in words, where it's
    needed, and the header names recognised for it.
    """

    key: str
    name: str
    needed: str
    names: tuple[str, ...] = field(default=())


def needed_on(key: str, settings: dict) -> str:
    """Say on which sheets a field is needed, from the column check itself
    (ENG-02 4.5), and when that depends on the file or the settings.
    """
    kinds = (columns.SPEND, columns.GL_MAP, columns.CC_MAP)
    sheets = [
        columns.SHEET_TYPE_NAME[kind]
        for kind in kinds
        if key in columns.required_fields(kind, {}, settings)
    ]
    if sheets:
        words = " and the ".join(sheets)
        # The posting date is needed only without both of these (ENG-02 4.4).
        found = {"fiscal_year": [0], "period": [1]}
        if columns.SHEET_TYPE_NAME[columns.SPEND] in sheets and key not in (
            columns.required_fields(columns.SPEND, found, settings)
        ):
            return f"On the {words}, unless the file has Fiscal year and Period"
        return f"On the {words}"
    by_value_type = {
        **settings,
        "actual_spend_rule": {"actual": ["x"], "not_actual": []},
    }
    if key in columns.required_fields(columns.SPEND, {}, by_value_type):
        return "On the spend sheet, while Actual spend counts by value type"
    return "No, it's read when it's found"


def field_rows(settings: dict) -> list[FieldRow]:
    """Return each field of the version's header names, in ENG-02 4.4's
    order.
    """
    return [
        FieldRow(
            key, name, needed_on(key, settings), tuple(settings["header_names"][key])
        )
        for key, name in FIELD_NAMES.items()
    ]


@dataclass(frozen=True)
class Version:
    """One version in the history: the version, what it changed from the one
    before in words, where it was made, and how many uploads were read with
    it.
    """

    ruleset: Ruleset
    changes: tuple[str, ...]
    made: str
    uploads: int
    in_force: bool


def made_where(version: Ruleset, event: AuditEvent | None) -> str:
    """Say where a version was made, from its ruleset_changed event: on the
    Rules screen, from the command line, or with the report (ENG-01 6.3).
    """
    if version.version == 1:
        return "Created with the report"
    details = event.details if event is not None else {}
    if "screen" in details:
        return "On this page"
    if "command" in details:
        return "Command line"
    return "Not recorded"


def history(report: Report) -> list[Version]:
    """Return every version of the report's rules, newest first (ENG-03 4)."""
    versions = list(report.rulesets.select_related("created_by").order_by("version"))
    read = dict(
        report.uploads.order_by()
        .values_list("ruleset__version")
        .annotate(count=Count("pk"))
    )
    events = {
        event.details.get("version"): event
        for event in AuditEvent.objects.filter(
            report=report, action=AuditEvent.Action.RULESET_CHANGED
        )
    }
    found = []
    for before, version in zip([None, *versions], versions, strict=False):
        if before is None:
            changes = ("The defaults every new report starts with",)
        else:
            changes = tuple(
                change.summary
                for change in ruleset.changes_in_words(
                    before.settings, version.settings
                )
            )
        found.append(
            Version(
                version,
                changes,
                made_where(version, events.get(version.version)),
                read.get(version.version, 0),
                version is versions[-1],
            )
        )
    return found[::-1]


def in_force_until(report: Report, version: Ruleset) -> datetime.datetime | None:
    """Return when the next version replaced this one, or None while it's in
    force.
    """
    later = report.rulesets.filter(version__gt=version.version).order_by("version")
    return later.values_list("created_at", flat=True).first()


def latest_version(report: Report) -> Ruleset:
    """Return the report's rules in force: its latest ruleset version."""
    return report.rulesets.select_related("created_by").order_by("-version").first()


def uploads_read_with(report: Report, version: Ruleset) -> int:
    """Return how many of the report's uploads were read with the version,
    counted in PostgreSQL.
    """
    return report.uploads.filter(ruleset=version).count()


def choice_labels(key: str) -> dict[str, str]:
    """Return each value of a setting with a fixed list, as its form offers
    it: its words, then what it does.
    """
    names = ruleset.VALUE_NAMES[key]
    if key == "date_order":
        return {
            value: f"{name}: {DATE_EXAMPLE} is {date_example(value)}"
            for value, name in names.items()
        }
    if key == "decimal_separator":
        return {
            value: f"{name}: {AMOUNT_EXAMPLES[value]} is twelve thousand four "
            "hundred and a half"
            for value, name in names.items()
        }
    if key == "mapping_rule":
        return {
            "latest": f"{names['latest']}: every line takes the mapping of the most "
            "recently approved file that has one, earlier months' lines too",
            "own_upload": f"{names['own_upload']}: each line keeps the mapping its "
            "own file brought, or the latest when its file brought none",
        }
    raise ValueError(f"{key} has no fixed list of values.")
