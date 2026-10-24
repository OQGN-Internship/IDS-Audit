"""The ruleset settings every report's version 1 copies (ENG-02 3).

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

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
    "currency": ["Currency", "Crcy"],
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
    "actual_spend_rule": None,
    "date_order": "day_first",
    "currency": "OMR",
    "outside_ids_rule": "missing_from_cc_map",
    "mapping_rule": "latest",
}
