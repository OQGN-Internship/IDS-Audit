"""The ruleset settings every report's version 1 copies, and the check every
later version passes before it's saved (ENG-02 3).

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

import re

from ids_audit.pipeline.columns import normalize_header

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

# The values each setting with a fixed list may take (ENG-02 3).
CHOICES: dict[str, tuple[str, ...]] = {
    "date_order": ("day_first", "month_first"),
    "decimal_separator": (".", ","),
    "outside_ids_rule": ("missing_from_cc_map",),
    "mapping_rule": ("latest", "own_upload"),
}
CURRENCY = re.compile(r"[A-Z]{3}")

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
    its form. Empty when nothing is.
    """
    found = [
        f"The setting {key} is missing."
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
    for key, choices in CHOICES.items():
        if key in settings and settings[key] not in choices:
            found.append(f"{key} must be one of {', '.join(choices)}.")
    currency = settings.get("currency", "OMR")
    if not (isinstance(currency, str) and CURRENCY.fullmatch(currency)):
        found.append("currency must be three capital letters, such as OMR.")
    return found


def header_name_problems(header_names) -> list[str]:
    """Return what's wrong with header_names: every field's key with a list of
    names, each with a letter or digit, and no name under two fields, since a
    column would then match both (ENG-02 4.4).
    """
    if not isinstance(header_names, dict):
        return ["header_names must be an object of each field's header names."]
    found = [
        f"header_names has no list for {key}."
        for key in HEADER_NAMES
        if key not in header_names
    ]
    found += [
        f"header_names has no field called {key}."
        for key in header_names
        if key not in HEADER_NAMES
    ]
    # Each normalized name, with every (field, name as given) it's listed as.
    listed: dict[str, list[tuple[str, str]]] = {}
    for key, names in header_names.items():
        if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
            found.append(f"header_names' {key} must be a list of header names.")
            continue
        for name in names:
            normalized = normalize_header(name)
            if not normalized:
                found.append(f"The header name {name!r} has no letter or digit.")
            else:
                listed.setdefault(normalized, []).append((key, name))
    for entries in listed.values():
        fields = list(dict.fromkeys(key for key, _ in entries))
        if len(fields) > 1:
            found.append(
                f"The header name {entries[-1][1]!r} is listed under "
                f"{' and '.join(fields)}."
            )
    return found


def actual_spend_rule_problems(rule) -> list[str]:
    """Return what's wrong with actual_spend_rule: null, or two lists of value
    types, actual and not_actual, with no value in both once trimmed and
    lower-cased, as the cleaning compares them (ENG-02 5).
    """
    if rule is None:
        return []
    if not (isinstance(rule, dict) and set(rule) == {"actual", "not_actual"}):
        return [
            "actual_spend_rule must be null, or hold the lists actual and not_actual."
        ]
    found = []
    for key, values in rule.items():
        if not (
            isinstance(values, list)
            and all(isinstance(v, str) and v.strip() for v in values)
        ):
            found.append(f"actual_spend_rule's {key} must be a list of value types.")
    if not found:
        both = {v.strip().lower() for v in rule["actual"]} & {
            v.strip().lower() for v in rule["not_actual"]
        }
        found += [f"The value type {v!r} is in both lists." for v in sorted(both)]
    return found
