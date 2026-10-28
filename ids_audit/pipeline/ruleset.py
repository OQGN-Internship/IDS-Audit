"""The ruleset settings every report's version 1 copies, and the check every
later version passes before it's saved (ENG-02 3).

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

import re
from dataclasses import dataclass

from ids_audit.pipeline.columns import FIELD_NAMES, normalize_header

# The built-in header names for each field, under its key (ENG-02 4.4). They're
# compared after the clean-up in 4.4, so they're written as people read them.
HEADER_NAMES: dict[str, list[str]] = {
    "gl_code": ["G/L Account", "GL Account", "GL Acct", "Cost Element", "Account"],
    "cost_center_code": ["Cost Center", "Cost Centre", "Cost Ctr", "CCtr"],
    "amount": ["Amount in LC", "Amount", "Value in Local Currency", "Val/COArea Crcy"],
    "posting_date": ["Posting Date", "Pstng Date", "Date"],
    "fiscal_year": ["Fiscal Year", "Year"],
    "period": ["Period", "Posting Period"],
    "document_number": ["Document Number", "DocumentNo", "Doc. No."],
    "line_item": ["Line Item", "Item"],
    "company_code": ["Company Code", "CoCd"],
    "value_type": ["Value Type"],
    # The local currency, which the amount is in. A document-currency column,
    # such as Currency or Crcy, isn't read (ENG-02 4.4).
    "currency": ["Local Currency", "LCurr", "Company Code Currency"],
    "item_text": ["Item Text", "Description", "Text"],
    "ci_code": ["Commitment Item", "Cmmt Item"],
    "gl_name": ["G/L Account Name", "GL Name"],
    "ci_name": ["Commitment Item Name", "Cmmt Item Name"],
    "dept_code": ["Department Code", "Dept Code", "Department"],
    "cc_name": ["Cost Center Name", "Cost Centre Name"],
    "dept_name": ["Department Name", "Dept Name"],
}

# Every setting in full, so a version never depends on these defaults once it
# exists (ENG-02 3).
DEFAULT_SETTINGS: dict = {
    "header_names": HEADER_NAMES,
    # None (JSON null): every row counts as actual spend, the plan's default.
    # Otherwise {"actual": [...], "not_actual": [...]} (ENG-02 3).
    "actual_spend_rule": None,
    "date_order": "day_first",
    "decimal_separator": ".",
    "currency": "OMR",
    "outside_ids_rule": "missing_from_cc_map",
    "mapping_rule": "latest",
}

# Each setting's name on screen, which the Rules screen, every refusal and the
# guide use, in the order a file meets them (ENG-02 3).
SETTING_NAMES: dict[str, str] = {
    "header_names": "Header names",
    "date_order": "Date order",
    "decimal_separator": "Decimal separator",
    "currency": "Currency",
    "actual_spend_rule": "Actual spend",
    "mapping_rule": "Mapping rule",
    "outside_ids_rule": "Outside IDS",
}

# The values each setting with a fixed list may take, each with its words
# (ENG-02 3).
VALUE_NAMES: dict[str, dict[str, str]] = {
    "date_order": {"day_first": "Day first", "month_first": "Month first"},
    "decimal_separator": {".": "A point", ",": "A comma"},
    "mapping_rule": {
        "latest": "The latest mapping",
        "own_upload": "Each file's own mapping",
    },
    "outside_ids_rule": {
        "missing_from_cc_map": "Missing from the cost center mapping",
    },
}
CHOICES: dict[str, tuple[str, ...]] = {
    key: tuple(names) for key, names in VALUE_NAMES.items()
}
CURRENCY = re.compile(r"[A-Z]{3}")

# The two lists of actual_spend_rule, in words.
LIST_NAMES = {"actual": "actual spend", "not_actual": "not actual spend"}

# The settings that act whenever the report is shown, in posting_resolved, so
# they can't change once a preview or a commit depends on them (ENG-02 3).
SHOWN_SETTINGS = ("outside_ids_rule", "mapping_rule")


def merged(settings: dict, changes: dict) -> dict:
    """Return the settings with the changes made. Each setting given replaces
    its value whole, except header_names, where each field given replaces that
    field's list and the others keep theirs (ENG-02 3).
    """
    new = {**settings, **changes}
    names = changes.get("header_names")
    if isinstance(names, dict):
        new["header_names"] = {**settings["header_names"], **names}
    return new


def problems(settings: dict) -> list[str]:
    """Return what's wrong with a version's settings, checked against ENG-02
    3's table: every setting is there and nothing else, and each value is in
    its form. Empty when nothing is. Each problem starts with the setting's
    name on screen, as the Rules screen and the commands show it.
    """
    found = [
        f"{SETTING_NAMES[key]}: the setting is missing."
        for key in DEFAULT_SETTINGS
        if key not in settings
    ]
    found += [
        f"There's no setting called {key}."
        for key in settings
        if key not in DEFAULT_SETTINGS
    ]
    if "header_names" in settings:
        found += header_name_problems(settings["header_names"])
    if "actual_spend_rule" in settings:
        found += actual_spend_rule_problems(settings["actual_spend_rule"])
    for key, names in VALUE_NAMES.items():
        if key in settings and settings[key] not in names:
            words = " or ".join(lower_first(name) for name in names.values())
            found.append(f"{SETTING_NAMES[key]}: must be {words}.")
    currency = settings.get("currency", "OMR")
    if not (isinstance(currency, str) and CURRENCY.fullmatch(currency)):
        found.append("Currency: must be three capital letters, such as OMR.")
    return found


def lower_first(words: str) -> str:
    """Return the words with their first letter in lower case, to sit inside
    a sentence, such as "day first".
    """
    return words[:1].lower() + words[1:]


def header_name_problems(header_names) -> list[str]:
    """Return what's wrong with header_names: every field's key with a list of
    at least one name, each with a letter or digit, and no name under two
    fields, since a column would then match both (ENG-02 4.4).
    """
    where = SETTING_NAMES["header_names"]
    if not isinstance(header_names, dict):
        return [f"{where}: must be an object of each field's header names."]
    found = [
        f"{where}: {FIELD_NAMES[key]} has no list."
        for key in HEADER_NAMES
        if key not in header_names
    ]
    found += [
        f"{where}: there's no field called {key}."
        for key in header_names
        if key not in HEADER_NAMES
    ]
    # Each normalized name, with every (field, name as given) it's listed as.
    listed: dict[str, list[tuple[str, str]]] = {}
    for key, names in header_names.items():
        field = FIELD_NAMES.get(key, key)
        if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
            found.append(f"{where}: {field} must be a list of header names.")
            continue
        if not names and key in HEADER_NAMES:
            # A field with no name is never found, so a sheet that needs it
            # always stops, and the GL account's and cost center's never read.
            found.append(f"{where}: {field} needs at least one header name.")
        for name in names:
            normalized = normalize_header(name)
            if not normalized:
                found.append(f"{where}: {name!r} has no letter or digit.")
            elif key in HEADER_NAMES:
                listed.setdefault(normalized, []).append((key, name))
    order = list(HEADER_NAMES)
    for entries in listed.values():
        # Each field once, in ENG-02 4.4's order, since a version's JSON keeps
        # no order of its own, with the name it lists.
        under = {}
        for key, name in sorted(entries, key=lambda entry: order.index(entry[0])):
            under.setdefault(key, name)
        if len(under) > 1:
            each = " and ".join(
                f"{name!r} under {FIELD_NAMES[key]}" for key, name in under.items()
            )
            found.append(
                f"{where}: {each} match the same header. A header name can be "
                "under one field only, or a column would match both."
            )
    return found


def actual_spend_rule_problems(rule) -> list[str]:
    """Return what's wrong with actual_spend_rule: null, or two lists of value
    types, actual and not_actual, actual with at least one, and no value in
    both once trimmed and lower-cased, as the cleaning compares them (ENG-02
    5).
    """
    where = SETTING_NAMES["actual_spend_rule"]
    if rule is None:
        return []
    if not (isinstance(rule, dict) and set(rule) == {"actual", "not_actual"}):
        return [f"{where}: must be null, or hold the lists actual and not_actual."]
    found = []
    for key, values in rule.items():
        if not (
            isinstance(values, list)
            and all(isinstance(v, str) and v.strip() for v in values)
        ):
            found.append(
                f"{where}: the {LIST_NAMES[key]} list must be a list of value types."
            )
    if not found and not rule["actual"]:
        found.append(
            f"{where}: at least one value type must be actual spend, or nothing "
            "would count."
        )
    if not found:
        both = {v.strip().lower() for v in rule["actual"]} & {
            v.strip().lower() for v in rule["not_actual"]
        }
        found += [f"{where}: {v!r} is in both lists." for v in sorted(both)]
    return found


def value_in_words(key: str, value) -> str:
    """Return a setting's value in words, as the Rules screen shows it: a
    choice by its words, and the actual-spend rule by what it counts. Header
    names are shown field by field instead.
    """
    if key in VALUE_NAMES:
        return VALUE_NAMES[key].get(value, str(value))
    if key == "actual_spend_rule":
        if value is None:
            return "Every row counts"
        return (
            f"By value type. Actual spend: {listed(value['actual'])}. "
            f"Not actual spend: {listed(value['not_actual'])}."
        )
    return str(value)


def listed(values: list[str]) -> str:
    """Return values joined with commas, or none."""
    return ", ".join(values) if values else "none"


@dataclass(frozen=True)
class Change:
    """One change from a version's settings to the next's, in words: what
    changed, such as Date order or Header names for Amount, its value before
    and after, and the change as one line of the Rules screen's history.
    """

    what: str
    before: str
    after: str
    summary: str


def changes_in_words(before: dict, after: dict) -> list[Change]:
    """Return each change from one version's settings to another's, in the
    order the Rules screen shows the settings, and a field of header names at
    a time (ENG-03 4).
    """
    found = []
    for key, name in SETTING_NAMES.items():
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if key != "header_names":
            old_words, new_words = value_in_words(key, old), value_in_words(key, new)
            found.append(
                Change(
                    name, old_words, new_words, f"{name}: {old_words} to {new_words}"
                )
            )
            continue
        for field, field_name in FIELD_NAMES.items():
            old_names, new_names = old.get(field, []), new.get(field, [])
            if old_names == new_names:
                continue
            added = [n for n in new_names if n not in old_names]
            removed = [n for n in old_names if n not in new_names]
            parts = []
            if added:
                parts.append(f"added {listed(added)}")
            if removed:
                parts.append(f"removed {listed(removed)}")
            what = f"{name} for {field_name}"
            found.append(
                Change(
                    what,
                    listed(old_names),
                    listed(new_names),
                    f"{what}: {', '.join(parts) or 'put in another order'}",
                )
            )
    return found
