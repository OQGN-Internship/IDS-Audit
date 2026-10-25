# ENG-02 Data Model and Import

Implements: Phases 1 and 2 of [IDS Spend Audit Delivery Plan](../IDS%20Spend%20Audit%20Delivery%20Plan.md). Builds on [ENG-01](ENG-01%20Architecture,%20Security%20and%20Operations.md).

## 1. Purpose and scope

This document defines where every number comes from: the tables that hold the data, how an uploaded file becomes postings, and the rules that decide what counts. It's written for the finance validator as much as for developers. Every rule here can be checked by hand against a file.

It covers the data model, file reading, cleaning, upload states, duplicates, mapping versions, reconciliation, commit and rollback. Sign-in and access are in ENG-01, and the report screens and exports are in ENG-03. Which parts are built in Phase 1 and which in Phase 2 is set by the build order in ENG-01, 4.

The rules are written against the agreed file structure: spend lines plus the GL and cost center mapping sheets, all in one file. Until the real export arrives, they're built and tested against the sample file (12.2). Anything marked **Confirm with the real export** may change when the real file arrives. Section 13 lists those items.

## 2. Data model

Everything belongs to a report, and every posting, mapping row and skipped row belongs to the upload it came from. Users, roles, report access and the audit log are defined in ENG-01.

Each table has the name given here, set with `Meta.db_table` on its model, so hand-written SQL such as `sql/grants.sql` and `posting_resolved` (8.4) can name it. `audit_event` (ENG-01, 6.3) does the same. The `accounts` models keep Django's own table names, since no hand-written SQL names them.

![Everything hangs off a report, and each upload brings its own rows](../images/eng02-data-model.png)

Every arrow points from one row to many: a report has many uploads, and an upload has many postings.

**Written once, never changed.** Postings, mapping rows, skipped rows and ruleset versions are only ever inserted. The application's database role, `ids_app`, has no `UPDATE` or `DELETE` on them, the same protection as the audit log (ENG-01, 6.3). Staging rows are the exception: they are deleted once an upload is approved or rejected.

### `posting`: one row per spend line

| Field | Type | Notes |
| --- | --- | --- |
| `id` | bigint |  |
| `report_id` | reference to `report` |  |
| `upload_id` | reference to `upload` | The upload it came from. An upload with postings can't be deleted |
| `sheet`, `row_number` | text, integer | Its place in the original file: the sheet's name, and the 1-based row number Excel shows, counted from the top of the sheet with blank rows included. For a delimited text file, `sheet` is empty and the row number is the record's number, which is the row Excel shows when it opens the file |
| `company_code`, `fiscal_year`, `document_number`, `line_item` | text, integer, text, text | All optional. **Confirm with the real export** |
| `line_key` | text | The four fields above, cleaned as in Section 5 and joined with `-`, when all four exist, such as `1000-2026-5100000123-1`. Used for duplicates (Section 7) |
| `posting_date` | date | Required, or derived from fiscal year and period |
| `gl_code` | text | Cleaned as in Section 5 |
| `cost_center_code` | text | Cleaned as in Section 5 |
| `value_type` | text | As written in the file, if the file has one |
| `is_actual` | boolean | Set by the ruleset's actual-spend rule (Section 3) |
| `amount` | `numeric(18,3)` | Never empty. Negative for credits |
| `currency` | text | `OMR` |
| `item_text` | text | The line's description, kept as written, Arabic included |

### `upload`: one row per file

| Field | Type | Notes |
| --- | --- | --- |
| `id`, `report_id` |  |  |
| `number` | integer | 1, 2, 3 within the report, in the order its uploads are created. Given once, when the upload is created, and never changed, so failed and rejected uploads keep theirs and no number is reused or skipped. Unique per report. "Upload *n*" means this number (7.1, and ENG-03, 3.2) |
| `file_sha256` | text | The stored file's name in the uploads volume |
| `original_name`, `size_bytes` | text, integer |  |
| `uploaded_by`, `uploaded_at` | user, time |  |
| `ruleset_id` | reference to `ruleset` | The settings it was read with |
| `status` | text | One of the states in Section 6 |
| `column_layout` | JSON | Which column held each field, per sheet, with the sheet's header and any columns picked for it (4.5) |
| `period_start`, `period_end` | date | The first and last posting date in the file |
| `control_total`, `control_total_source` | `numeric(18,3)`, text | The total to reconcile against, and whether it came from the file or was entered |
| `rows_read`, `rows_skipped`, `rows_new`, `rows_duplicate`, `rows_replaced` | integer | The preview figures (Section 11) |
| `preview` | JSON | The preview's amounts and exceptions, saved when the preview is built (Section 11) |
| `reconciliation_difference` | `numeric(18,3)` | 0.000 means the file reconciles |
| `decided_by`, `decided_at` | user, time | Approval or rejection |
| `rolled_back_by`, `rolled_back_at` | user, time |  |
| `error` | text | Why it failed, if it did |

An upload changes only through the state changes in Section 6, and each change writes an audit event.

### Mapping tables: one row per code, per upload

| Table | Fields | Rule |
| --- | --- | --- |
| `gl_ci_map` | `upload_id`, `report_id`, `gl_code`, `gl_name`, `ci_code` (may be empty), `ci_name` | One row per GL account in each upload, enforced by a unique constraint on (`upload_id`, `gl_code`) |
| `cc_dept_map` | `upload_id`, `report_id`, `cc_code`, `cc_name`, `dept_code` (may be empty), `dept_name` | One row per cost center in each upload, enforced by a unique constraint on (`upload_id`, `cc_code`) |

### The other tables

| Table | Holds |
| --- | --- |
| `report` | `id`, `name` (unique ignoring case, Section 3), `duplicate_mode` (`replace_by_period` or `line_key`, fixed by the first committed upload and empty until then, Section 7), `created_by`, `created_at` |
| `ruleset` | `report_id`, `version`, `settings` (JSON), `created_by`, `created_at`. One row per report and version |
| `staging_row` | The same fields as `posting`, plus `is_duplicate`. Rows exist only while an upload awaits approval |
| `skipped_row` | `upload_id`, `sheet` and `row_number` (as in `posting`), `reason`, `amount` (if it could be read, stored exactly as read in plain `numeric`, ENG-01, 4 rule 5), and `raw_values`: the row's cells as JSON |

**`raw_values`** is a JSON list of text, one entry per cell from column A to the row's last cell that isn't empty, so a row gives the same list from either Excel reader. A CSV copy gives the same list when each of its fields holds the cell's text as written here, as the sample's copy does (12.3). One saved with Excel's display formats, such as `12,400.000` for a number cell, keeps that text instead. Text is kept exactly as read, spaces included. A number is written in its shortest form, without `.0` when it's whole, such as `2026` or `12.3456`. A date is written as `yyyy-mm-dd`, a time as `hh:mm:ss`, and a date with a time as both, joined by `T`. A duration is written as `h:mm:ss`, after its days if it has any. A logical cell is `TRUE` or `FALSE`, and an empty cell is `""`. Every value is text, so a number never passes through a JSON number, which most readers turn into a float.

## 3. Reports and rulesets

A report is a separate set of uploads with its own totals and its own user access. Fixed administrators create reports, following the plan's default for its open question on report creation.

**Creating a report.** One transaction creates the `report` row, its ruleset version 1 and a `report_created` audit event (ENG-01, 6.3), so none of them exists without the others. The name is trimmed and can't be empty, and no two reports' names may differ only in case: once `IDS 2026` exists, `ids 2026 ` is refused. A unique constraint on the lower-cased name enforces this in the database.

Each report has a **ruleset**: the settings that decide how its files are read and what counts. Every upload records the ruleset version it was read with, so any upload can be explained later exactly as it was processed.

| Setting | What it decides | Form | Version 1 |
| --- | --- | --- | --- |
| `header_names` | The header names recognised for each field (Section 4) | An object from each field's key to its list of header names (4.4). Each name holds a letter or a digit, and no name, once cleaned up as in 4.4, is under two fields, since a column would then match both | The built-in list in 4.4 |
| `actual_spend_rule` | Which value-type values count as actual spend, and which don't | `null`, or an object with two lists of value-type values, such as `{"actual": ["Actual"], "not_actual": ["Commitment"]}`. With an object, a row is actual spend when its value type matches a value in `actual`, isn't when it matches one in `not_actual`, and is unclassified when it matches neither (Section 5). The value-type column is then required (4.4). No value may be in both lists | `null`: every row counts, whether or not the file has a value-type column. This is the plan's default |
| `date_order` | How a date such as `03.04.2026` is read | `"day_first"` or `"month_first"`. A `yyyy-mm-dd` date is always read as written (Section 5) | `"day_first"`: 3 April 2026 |
| `decimal_separator` | Which character separates the decimals in an amount written as text (Section 5) | `"."` or `","` | `"."`: `12,400.000` is twelve thousand four hundred |
| `currency` | The currency every amount must be in | A three-letter currency code | `"OMR"` |
| `outside_ids_rule` | Which cost centers are outside IDS | `"missing_from_cc_map"`: any cost center missing from the cost center mapping in force (8.1). It's the only value until the client gives another rule (13, item 3) | `"missing_from_cc_map"`. This is the plan's default |
| `mapping_rule` | Which mapping applies when a code's budget line or department changes between files | `"latest"` or `"own_upload"` (8.1) | `"latest"`. This is the plan's default |

**Every version holds every setting in full.** A report's version 1 is a copy of the defaults above, kept as `DEFAULT_SETTINGS` in `ids_audit/pipeline/ruleset.py`. A later change to the code's defaults never changes a version that already exists, so each upload's version still says exactly how it was read.

The first five settings act while a file is read, so their effect is stored in its rows. `outside_ids_rule` and `mapping_rule` act whenever the report is shown, in `posting_resolved` (8.4). The view reads `mapping_rule` from the report's latest ruleset version. `outside_ids_rule` has only one value, so the view applies that rule directly, and a new value needs a new view migration (8.4).

**Changing a ruleset**

- Only fixed administrators can change a ruleset.
- A change creates a new version. No version is ever edited: `ids_app` can't `UPDATE` or `DELETE` a ruleset row (ENG-01, 6.3).
- A new version's settings are checked against the table above before it's saved: every setting is present, and every value is in its form. Anything else is refused.
- A change to a setting that acts while a file is read applies only to uploads made after it. Committed uploads keep the rows, amounts and `is_actual` flags they were read with.
- `outside_ids_rule` and `mapping_rule` can't change once the report has a committed upload, like `report.duplicate_mode` (7.3), or while it has an open upload (Section 6). Otherwise a change would move figures that a preview has already shown.
- To apply new rules to old data, create a new report and upload the files again.
- Every change writes a `ruleset_changed` audit event (ENG-01, 6.3).
- A change that leaves every setting as it was is refused, so no version repeats the one before it.
- The report's row is locked while a change is checked and written, as it is while a file is received (ENG-01, 5.1), so no upload can open during a change to `outside_ids_rule` or `mapping_rule`.

**From the command line.** A ruleset is changed with `manage.py change_ruleset <report> --by <username>`, run as the fixed administrators' commands are (ENG-01, 6.1). `<report>` is the report's name, matched ignoring case, and `--by` names the active fixed administrator making the change, whom the new version and its event record. Anyone else is refused. The command reads a JSON object from standard input, holding only the settings that change, such as `{"date_order": "month_first"}`. Each setting given replaces the latest version's whole, except `header_names`, where each field given replaces that field's list and the other fields keep theirs, so adding a header name needs only that field. The command prints the new version's number. `manage.py show_ruleset <report>` prints the latest version's number and settings as JSON, so a change can be written from it. A screen was the alternative, and isn't built: a change is rare, made with the validator when the real export needs one (Section 13), and a JSON object is the plainest way to write header names and the actual-spend rule.

**Tested by**

- Creating a report writes the report, its ruleset version 1 and exactly one `report_created` event. A failure part-way through leaves none of them.
- A new report's ruleset version 1 holds exactly `DEFAULT_SETTINGS`.
- Once `IDS 2026` exists, a report named `ids 2026 ` is refused, and so is an empty name.
- A new version with a setting missing, or with a value outside its form, such as `mapping_rule` set to `fiscal_year` or a value type in both lists of `actual_spend_rule`, is refused.
- Changing `mapping_rule` or `outside_ids_rule` is refused while the report has an open or a committed upload. On a report with neither, it creates a new ruleset version and leaves the old one unchanged.
- After `date_order` changes, a committed upload's postings keep their dates, and the next upload is read with the new order.
- A user who isn't a fixed administrator is refused when changing a ruleset, and so is a disabled one.
- A change naming one field of `header_names` changes only that field's list, and writes one `ruleset_changed` event with the new version and each setting changed, before and after. A change that changes nothing, or names a setting that doesn't exist, is refused and writes nothing.
- `show_ruleset` prints the latest version, and `change_ruleset` reads its change from standard input.
- A change to `mapping_rule` made while a file is being received waits for it, and is then refused.
- A header name under two fields, or with no letter or digit, is refused.

## 4. Reading the file

### 4.1 Accepted formats

The format is identified from the file's first bytes, never from its name.

| Format | First bytes | Reader |
| --- | --- | --- |
| Excel `.xlsx` | `50 4B 03 04`: `PK`, which marks a ZIP file, and the start of its first entry | python-calamine, with openpyxl if calamine fails |
| Excel `.xls` | `D0 CF 11 E0 A1 B1 1A E1` | python-calamine |
| Delimited text | Readable text in UTF-8, with or without a byte-order mark, or UTF-16 with its byte-order mark | Python's `csv` module |

Both Excel readers read each sheet from cell A1, so blank rows at the top keep their row numbers (Section 2). calamine is called with `skip_empty_area=False`, since by default it drops them.

Only a formula's cached result, the value saved with it, is read. calamine reads it as it is, and openpyxl is opened with `data_only=True`. A formula with no saved result, and an error such as `#N/A`, read as an empty cell in both readers, because calamine can't tell either from one. The rules for an empty cell then apply (Section 5).

Readable text means the first 4,096 bytes decode, and hold no control characters other than tab, line feed and carriage return. A text file named `.xls` is read as delimited text. Anything else, such as HTML, PDF or an empty file, is refused as an unknown file type. A file whose first characters, after any byte-order mark and spaces, are `<`, `MIME-Version` or `%PDF` is HTML, XML, MHTML or PDF, never text. **Confirm with the real export:** some SAP exports are HTML saved as `.xls`. If the client's is, a reader for it is added on Day 2.

**No other text encoding is read.** A file in a Windows code page, such as Excel's plain "CSV" save, which uses cp1256 on an Arabic Windows, is never decoded with a guessed encoding. If its first 4,096 bytes aren't valid UTF-8, it's refused as an unknown file type. If they are, and an invalid byte comes later, such as in an Arabic item text, the upload ends at `failed` (Section 6). Both messages say how to fix it: "Save it as CSV UTF-8 in Excel, or choose UTF-8 when downloading from SAP, and upload it again." The real export's encoding is checked when it arrives (13, item 14).

**The separator** in delimited text is a semicolon, a comma or a tab. Each is tried on the first 4,096 characters, with quoted cells kept whole, and the one that splits the most lines into the same number of cells, more than one, is used. If two split as many lines, the one giving more cells wins. A file that no separator splits is one column, so it has no spend sheet.

A delimited text file holds one table. It's the spend sheet when its header has a GL account and a cost center, and otherwise the file has no spend sheet. It never has a mapping of its own: commitment item or department columns in it are ignored, unlike on an Excel spend sheet (4.2). Its postings use the mapping in force (8.1).

### 4.2 Finding the sheets

Each sheet is classified by the key fields found in its header row:

| Sheet type | Recognised when its header has | How many |
| --- | --- | --- |
| Spend lines | GL account and cost center | Exactly one |
| GL mapping | GL account, and no cost center or amount | At most one |
| Cost center mapping | Cost center, and no GL account or amount | At most one |
| Anything else | None of the above, such as a README sheet | Ignored |

A sheet's other required fields, such as the amount or the commitment item, are checked after it's classified (4.5). A missing one stops the upload at `needs_columns` instead of hiding the sheet.

A file with no spend sheet, or with two sheets of the same type, ends at `failed` (Section 6), with a message naming the sheets. Choosing between two sheets would be a guess.

If the spend sheet itself has commitment item or department columns, their values build that upload's mappings too. Where they disagree with the mapping sheets, the mapping sheets win, and each disagreement is shown as an exception. The rules for mapping rows are in Section 5.

### 4.3 Finding the header row

The header row is searched for in a sheet's first 25 rows. The row that matches the most recognised header names wins, and any title lines above it are ignored. If two rows match as many, the higher one wins. In the sample file, for example, the Transactions header is on row 4, below two title lines. A sheet with no recognised header in its first 25 rows is ignored.

### 4.4 Matching columns to fields

Before comparing, headers are put in lower case with everything but letters and digits removed, so `G/L Account` and `gl account` match the same name. A header matches a name only as a whole cell. The ruleset's `header_names` holds the list, under each field's key (Section 3). These are the built-in names, which version 1 copies:

| Field | Key | Built-in headers | Required |
| --- | --- | --- | --- |
| GL account | `gl_code` | G/L Account, GL Account, GL Acct, Cost Element, Account | Yes, on the spend sheet and the GL mapping sheet |
| Cost center | `cost_center_code` | Cost Center, Cost Centre, Cost Ctr, CCtr | Yes, on the spend sheet and the cost center mapping sheet |
| Amount | `amount` | Amount in LC, Amount, Value in Local Currency, Val/COArea Crcy | Yes, on the spend sheet |
| Posting date | `posting_date` | Posting Date, Pstng Date, Date | Yes, on the spend sheet, unless fiscal year and period are both present |
| Fiscal year, period | `fiscal_year`, `period` | Fiscal Year, Year; Period, Posting Period | No |
| Document number | `document_number` | Document Number, DocumentNo, Doc. No. | No |
| Line item | `line_item` | Line Item, Item | No |
| Company code | `company_code` | Company Code, CoCd | No |
| Value type | `value_type` | Value Type | Yes, on the spend sheet, when `actual_spend_rule` isn't `null` (Section 3) |
| Currency | `currency` | Local Currency, LCurr, Company Code Currency | No |
| Item text | `item_text` | Item Text, Description, Text | No |
| Commitment item | `ci_code` | Commitment Item, Cmmt Item | Yes, on the GL mapping sheet |
| GL account name, commitment item name | `gl_name`, `ci_name` | G/L Account Name, GL Name; Commitment Item Name, Cmmt Item Name | No |
| Department code | `dept_code` | Department Code, Dept Code, Department | Yes, on the cost center mapping sheet |
| Cost center name, department name | `cc_name`, `dept_name` | Cost Center Name, Cost Centre Name; Department Name, Dept Name | No |

When a sheet has two columns for the same subject, such as `Department Code` and `Department`, the one ending in "code" is the code and the other is the name.

**The currency is the amount's.** An SAP line carries two currencies: the invoice's (the document currency) and the company code's (the local currency), which the amount column is in. Only the local currency is read. A document-currency column, such as `Currency`, `Crcy` or `Curr.`, isn't, since an invoice in USD still has its local amount in OMR.

### 4.5 The column check

If a required field isn't found on a classified sheet, the mapping sheets included, or two columns match the same field, the upload stops at `needs_columns` (Section 6). The uploader picks the column for each field from a list showing the file's first rows.

**Picking columns** moves the upload back to `reading` and reads the file again with the columns picked, in one transaction, so an upload is never left at `reading` by a pick (Section 6, An interrupted read). A column picked for a field replaces whatever the header names matched for it on that sheet. If a problem remains, the upload stops at `needs_columns` again, with its problems. Picks add up: the next pick keeps every column picked before it, so only the problems left need a column.

**The choice is saved for the report** in `upload.column_layout`, which holds, for each classified sheet, its header row's number, its header as text, cell by cell to the last one that isn't empty, the column letter of each field, and under `picked` the columns picked for it. The choice applies to the next file whose sheet has exactly the same header, cell for cell, so that file is matched automatically. Only the choices of committed uploads are reused, the most recently committed first (10.2), so a choice made for a file that was rejected, perhaps because the choice was wrong, is never reused. The ruleset's `header_names` stay as they are, since only fixed administrators change a ruleset (Section 3). Each upload records the layout it used, a reused choice included.

**Tested by**

- In the sample file, the README sheet is ignored and the Transactions header is found on row 4.
- A file with no spend sheet, a file with two, and a file with two GL mapping sheets each end at `failed`.
- A cost center mapping sheet with `Department Code` and `Department` columns reads the first as the code and the second as the name.
- In a file whose first two rows are blank, the header is found on row 3 and the first spend row is row 4, through calamine and through openpyxl.
- The sample file gives the same rows, mapping rows and control total through calamine and through openpyxl.
- A formula with no saved result, and a `#N/A` error cell, each read as an empty cell through both readers.
- An HTML file renamed to `.xlsx`, a PDF renamed to `.xls` and an MHTML file saved as `.xls` are each refused.
- A CSV in cp1256 is refused when an Arabic text falls in its first 4,096 bytes, and ends at `failed` when one falls later, each with the message to save it as CSV UTF-8. The same file in UTF-16 with its byte-order mark is read.
- A semicolon-separated CSV file is read correctly.
- A comma-separated CSV with Windows line endings, whose only quoted cell is the amount at the end of each line, and a semicolon-separated CSV below title lines that have no separators, are each read with the right separator.
- A CSV with a Commitment Item column, uploaded after the Excel sample, leaves every GL account's budget line as the Excel file mapped it.
- A file with no amount column stops at `needs_columns`.
- A file with no value-type column stops at `needs_columns` when `actual_spend_rule` isn't `null`, and is read when the rule is `null`.
- A GL mapping sheet whose commitment item header isn't recognised stops at `needs_columns`.
- A file with both Posting Date and Document Date columns uses Posting Date.

## 5. Cleaning rules

Every value is cleaned the same way every time, and a value that can't be cleaned is never guessed. Its row is skipped with a reason and shown in the preview.

| Field | Rule | Examples |
| --- | --- | --- |
| Codes (GL account, cost center, commitment item, department, company code, document number, line item) | Read as text and trim spaces. A number Excel stored as `4510.0` becomes `4510`. Leading zeros are removed from all-digit codes, leaving at least one digit, and letters are upper-cased. A code is unreadable when, once cleaned, it holds anything but English letters, digits, `-`, `_` and `.`, or when it's a number that isn't whole | `0000610100` → `610100`; `4510` → `4510`; `ab12` → `AB12`; `#N/A` → unreadable |
| Fiscal year, period | Read as whole numbers. A number Excel stored as `2026.0` becomes `2026`. Anything else is unreadable. The period is read only when the date comes from it | `2026.0` → `2026`; `003` → `3`; `FY26` → unreadable |
| Amount, from an Excel number | Converted through its shortest text form, never straight from the binary value | `1234.5` → `1234.500` |
| Amount, from text | Read with the ruleset's `decimal_separator`. Currency letters at either end are removed, with any spaces. A trailing minus, a leading minus or brackets each mean negative, and only one of them may be used. The other character, `,` when the separator is `.` and `.` when it's `,`, and spaces are allowed only as thousands separators, between groups of three digits. The decimal separator needs a digit on each side. Anything else is unreadable | `1,234.500-` → `-1234.500`; `(300.000)` → `-300.000`; `OMR 1,200` → `1200.000`; `12,34.5` → unreadable. With `","`: `1.234,500-` → `-1234.500` |
| Amount, precision | More than 3 decimal places, not counting zeros at the end, is never rounded. The row is skipped. An amount of a thousand million million or more doesn't fit `numeric(18,3)`, so it's unreadable | `12.3456` → skipped; `12.3450` → `12.345` |
| Date, from an Excel date cell | Used as it is, without its time of day |  |
| Date, from text | `dd.mm.yyyy`, `dd/mm/yyyy` and `dd-mm-yyyy` are read day first (the ruleset's `date_order`). `yyyy-mm-dd` is also accepted, and a two-digit year means 20yy. An empty date, or one that isn't a real day, is invalid | `03.04.2026` → 3 April 2026; `31.02.2026` → invalid |
| Date, from an Excel serial number | Whole numbers between 20000 and 80000 in a date column, as numbers or as digits in text, are read as Excel day numbers. A number cell's time of day is dropped | `46082` → 1 March 2026 |
| Date, from fiscal year and period | Used only when there's no date column. Periods 1 to 12 become the first day of that month, and periods 13 to 16 become 31 December. A missing or unreadable year or period, or any other period, gives an invalid date. **Confirm with the real export** that the fiscal year is the calendar year | Year 2026, period 3 → 1 March 2026 |
| Value type | Trimmed, lower-cased and compared with the values in the ruleset's `actual_spend_rule` (Section 3), trimmed and lower-cased the same way. A row matching `actual` is actual spend. A row matching `not_actual` is kept with `is_actual` false, and never counted as spend. A row matching neither is unclassified: it's kept with `is_actual` false, and approval is blocked until a fixed administrator classifies the value in a new ruleset version and the file is uploaded again (9.3). With the rule `null`, every row is actual spend | `ACTUAL ` matches `Actual` |
| Currency | Trimmed, upper-cased and compared with the ruleset's `currency`. The currency column is the local currency's (4.4). With no currency column, or an empty cell, the row is in the ruleset's currency. A row in another currency is skipped | `omr ` → `OMR`; `USD` → skipped |
| Item text | Trimmed, and otherwise kept exactly as written, Arabic included. Stored as UTF-8. A number cell is written as its shortest text form |  |

**When a row is skipped**

A **spend row** is any row whose GL account and cost center cells both hold something. Rows without both, such as totals, subtotals and labels, are never spend.

| Row | Example | Reason | Counts in reconciliation (Section 9) |
| --- | --- | --- | --- |
| Completely blank | All cells empty |  | Not recorded at all |
| The total row | No GL account and no cost center, an amount, and the word Total in a cell. Total is matched as a whole word in any case, so `Grand Total` qualifies and `Subtotal` doesn't. Which one is used is in 9.1 |  | It **is** the control total |
| Other rows without a GL account or cost center | A subtotal or a label |  | No. Listed in the preview, with its amount if it has one |
| Spend row in another currency | `USD` | `Another currency` | No, and approval is blocked: a file mixing currencies can't reconcile in OMR. **Confirm with the real export** which amount column holds local currency |
| Spend row with an unreadable code | `#N/A`, written as text, as the GL account | `Unreadable code` | Yes |
| Spend row with an unreadable amount | `12,34.5`, or an empty cell | `Unreadable amount` | Yes |
| Spend row with more than 3 decimal places | `12.3456` | `More than 3 decimal places` | Yes |
| Spend row with an invalid date | `31.02.2026` | `Invalid date` | Yes |
| Spend row with an unreadable fiscal year | `FY26`, in a file with a date column | `Unreadable fiscal year` | Yes |
| Spend row without a full line key, in a report in line-key mode | No document number | `No line key` | Yes (7.3) |
| Spend row repeating a line key already in the same file | Same document and line item twice | `Repeats row 12`, naming the first row | Yes (Section 7) |

A row with more than one problem gets the reason that comes first in the table. A skipped row keeps its amount whenever it can be read, whatever the reason.

**Mapping rows**

The GL mapping sheet holds one row per GL account, and the cost center mapping sheet one per cost center (Section 2). Their codes and names are cleaned by the rules above, and then:

| Row | What happens | Exception (11.2) |
| --- | --- | --- |
| Completely blank | Not recorded at all | None |
| No code of its own, such as a note | Listed in the preview as an other row | Other row without GL account or cost center |
| A code that can't be read: its own, or its commitment item or department | Skipped with the reason `Unreadable code`. It has no amount, so it doesn't count in reconciliation | Skipped row |
| A code listed again with the same commitment item or department | The first row is kept, and the repeat changes nothing | None |
| A code listed again with a different commitment item or department | The first row is kept, and approval is blocked: which budget line or department its postings get would be a guess | Mapping code listed with different values |
| A code given different names on different rows | The first name given is used for that code everywhere, so a group has one name on every screen (ENG-03). This covers GL account, commitment item, cost center and department names | Names differ for one code |

**From the spend sheet (4.2).** When the spend sheet has a commitment item column, each clean spend row whose commitment item isn't empty adds its GL account to the upload's GL mapping, with the names from the spend sheet's name columns, if it has any. A GL account on the GL mapping sheet keeps the sheet's values, and each spend row that disagrees with them is shown as the exception "Mapping sheet and spend sheet disagree". A GL account found only on spend rows follows the table above, so two different commitment items for it block approval. A department column does the same for cost centers.

**Tested by**

- A property test writes thousands of random amounts in SAP's formats and checks that each one reads back as the same `Decimal`.
- Cleaning a code that's already clean leaves it unchanged.
- A skipped row's `raw_values` are the same whether it's read through calamine, through openpyxl or from a CSV copy holding the same text, and a text cell keeps its spaces.
- Every row below the sample file's header ends up clean, skipped with a reason, or listed in the preview as the total row, a subtotal or a label. Only blank rows are left out, and none is lost.
- `USD`, `#N/A` written as text, `12,34.5`, `12.3456` and `31.02.2026` rows are each skipped with the right reason, and a row with two problems gets the first one's.
- The skipped `12.3456` row keeps its amount as `12.3456`, not `12.346`.
- With `decimal_separator` set to `","`, `1.234,500-` reads as `-1234.500` and `12,400.000` is unreadable.
- A row whose local currency column holds `USD` is skipped. A `Currency` column holding `USD` beside an `LCurr` of `OMR` changes nothing.
- With `actual_spend_rule` set to `{"actual": ["Actual"], "not_actual": ["Commitment"]}`, a row whose value type is `ACTUAL ` is actual spend, a `Commitment` row is kept with `is_actual` false, and a `Plan` row is unclassified. With the rule `null`, all three are actual spend.
- `Grand Total` makes a total row, and `Subtotal` doesn't.
- The same line read from Excel number cells and from CSV text gets the same `line_key`.
- A GL mapping sheet listing `610100` twice with `2101` keeps one row. Listing it with `2101` and `2201` keeps `2101`, blocks approval and names both rows.
- `#N/A` as a cost center on the cost center mapping sheet is skipped as `Unreadable code`.
- A commitment item given two names gets the first on every row, with an exception.
- A spend sheet with a Commitment Item column adds the GL accounts the mapping sheet lacks, and a spend row that disagrees with the mapping sheet leaves the sheet's value and raises an exception.
- In the sample file, mapping codes stored as numbers, such as `4510`, match spend codes written as `0000004510`.

## 6. Upload states

An upload moves only through the changes below, and each change writes an audit event (ENG-01, 6.3). Reading runs inside the upload request (ENG-01, 5.1).

![A file counts only once approved, and a rollback keeps it on record](../images/eng02-upload-states.png)

Any change not drawn is refused. Committed is the only state whose postings count.

| State (`upload.status`) | What it means | Moves to | Who moves it |
| --- | --- | --- | --- |
| `reading` | The file is stored and hashed, and is being read, cleaned and staged | `needs_columns`, `awaiting_approval` or `failed` | The app |
| `needs_columns` | A required field wasn't found (4.5) | `reading` again, once the columns are picked | The uploader |
|  |  | `rejected` | The uploader withdrawing it, or a user with **approve** |
| `awaiting_approval` | Rows are staged and the preview is ready (Section 11) | `committed` | A user with **approve** |
|  |  | `rejected` | A user with **approve**, or the uploader withdrawing it |
| `committed` | The postings are written and count in the report | `rolled_back` | A user with **roll\_back** (Section 10) |
| `rejected` | No postings were written, so the report didn't change | Final |  |
| `failed` | The file couldn't be read: it was damaged, it couldn't be opened in the format its first bytes showed, or it had no spend sheet | Final |  |
| `rolled_back` | The postings are kept but no longer count (Section 10) | Final |  |

**Rules**

- **Refused before an upload exists.** A file is refused at the request, and no upload is created, when the user lacks **upload**, the file is over `MAX_UPLOAD_MB` (ENG-01, 6.4), its first bytes aren't an accepted format (4.1), it matches a committed upload (7.1), or the report has an open upload. They're checked in that order, and the first that applies gives the message. The last two are checked with the report's row locked (ENG-01, 5.1), so two files sent at once can't both pass. Every refusal but the first writes an `upload_refused` event with the user, the report and the reason (ENG-01, 6.3), so a refused file is on record although no upload exists. A user without **upload** gets 403 or 404 from the access check (ENG-01, 6.2), which isn't logged, like any other address a user can't open.
- **One open upload per report.** While an upload is at `reading`, `needs_columns` or `awaiting_approval`, a new file for the same report is refused with a message naming the open one. A preview's duplicate and replacement figures are worked out against the committed postings, and a second commit would make them wrong.
- **No rollback during an open upload.** A rollback is refused while the report has an open upload, for the same reason.
- **Rejecting and withdrawing need a reason.** It's kept in the audit event, trimmed, and can't be empty. Either sets `decided_by` and `decided_at` to whoever did it. An upload at `needs_columns` can be rejected as well as withdrawn, so a user with **approve** can clear an upload its uploader left, as the plan's Approve permission says, without a fixed administrator (ENG-01, 6.2, rule 6).
- **Approving your own upload.** A fixed administrator, or a user whose role holds both **upload** and **approve**, can approve their own upload. Who holds both is decided by the permissions an administrator gives (ENG-01, 6.2), and the audit log records both actions.
- **An interrupted read.** If the app stops while a file is being read, nothing is staged, because reading runs in one transaction. The upload stays at `reading`, and any upload at `reading` more than 10 minutes after it was received is marked `failed`, with the error "Reading was interrupted". This runs whenever the report's uploads page opens, and before a new file or a rollback checks for an open upload, so an interrupted read never blocks the report. A read that's still running keeps its upload's row locked, and is left alone. The application makes this change on its own, so its `upload_failed` event has no user (ENG-01, 6.3).
- **What's kept.** Skipped rows and mapping rows are saved when reading ends at `awaiting_approval`, in the same transaction as the staging rows and the figures in 11.1. An upload that stops at `needs_columns` saves none of them, so reading it again can't save them twice. One that ends at `failed` saves only its `error`. They're kept whatever happens to the upload next, so a rejected file can still be explained. Staging rows are deleted when an upload leaves `awaiting_approval` or fails.
- **Trying again.** A failed or rejected file can be uploaded again as a new upload.

**Tested by**

- Every change in the table works, and every other change is refused, such as `rejected` to `committed`.
- A second file for a report with an open upload is refused, naming the open one, and so is a file of unknown type. Each refusal creates no upload, stores nothing and writes one `upload_refused` event with its reason.
- Of two files sent at once to a report with no open upload, one is received and the other is refused.
- A user without **approve** can't commit, and the uploader can withdraw.
- An upload stopped during reading leaves no staging rows and is later marked `failed`, with an event that has no user.
- A new file for a report whose only open upload was received more than 10 minutes ago and never read marks that upload `failed`, and is received. One received less than 10 minutes ago, or whose read is still running, stays at `reading`, and the new file is refused.
- A file missing a required column stops at `needs_columns` with no rows and no figures saved, and a file that can't be read ends at `failed` with its `error`. Each writes its audit event.
- Reading an upload that isn't at `reading` is refused, and changes nothing.
- Reading an upload again after its columns are picked saves each skipped row and mapping row once.
- Picking a column for a missing amount reads the file to `awaiting_approval`, and its `column_layout` keeps the pick. A later file with the same header is matched with it once that upload is committed, and not while it's only rejected or awaiting approval. A file whose header differs by one cell isn't.
- Picking a column that doesn't solve the problem stops the upload at `needs_columns` again, with both events.
- Rejecting or withdrawing without a reason is refused. Rejecting an upload at `needs_columns` or `awaiting_approval` sets `decided_by` and `decided_at`, deletes its staging rows, keeps its preview, and writes one event with the reason.
- A report's uploads are numbered 1, 2, 3 in the order they're created, failed and rejected ones included.

## 7. Duplicates

The same spend must never count twice. Duplicates are caught at three levels.

### 7.1 The same file twice

A file whose `file_sha256` matches a committed upload in the same report is refused before an upload is created, with the message "This file was already committed on *date* as upload *n*.", where the date is the upload's `decided_at` in Muscat time. The refusal is kept in the audit log (Section 6). The same file can go into a different report, and a file whose upload was rolled back, rejected or failed can be uploaded again.

### 7.2 The same line twice in one file

When two spend rows in one file have the same `line_key`, the second is skipped with the reason "repeats row *n*". It still counts in reconciliation (Section 9), because the file's own total includes it.

### 7.3 Lines already in the report

Each report uses one of two modes, set in `report.duplicate_mode`.

|  | Replace by period (the default) | Line key |
| --- | --- | --- |
| Used when | Always, unless line-key mode is chosen | The approver chooses it when approving the report's first upload. It's offered only when the file has rows to commit and every one has a full `line_key`. A spend row skipped for another reason doesn't count against it |
| What committing does | Every posting already in the report dated in the file's months is replaced. The months run from the month of `period_start` to the month of `period_end` | Rows whose `line_key` matches a posting that counts are marked `is_duplicate` and not committed. Only new lines are added |
| Suits | Year-to-date exports, where each file restates every month it covers | Exports that overlap irregularly |
| Preview shows | The months replaced, and the number and amount of postings replaced | The number and amount of duplicates |
| A posting that disappears from a later export | Disappears from the report too | Stays in the report |
| Rows without a full line key | Accepted | Skipped with the reason "No line key", and counted in reconciliation (Section 5) |

The mode can't change once a report has a committed upload, and a rollback doesn't clear it: a report whose only commit was rolled back keeps the mode that commit chose. A report's first upload means any upload made before the report's first commit (10.1), such as a new try after a rejected file. Nothing is in the report yet, so both modes give the same preview figures.

Replacement never deletes or edits a posting. A replaced posting simply stops counting (Section 10.2), so rolling back the upload that replaced it makes it count again.

**Marking duplicates.** In line-key mode, a staging row is marked `is_duplicate` when its `line_key` is exactly the `line_key` of a posting in the report that counts (10.2), as `posting_resolved` shows it. This is worked out when the file is read, so the preview's figures already leave duplicates out (11.1), and the commit copies only the rows not marked (10.1). A line key repeated within the file is skipped before this (7.2), so a staging row matches at most one posting.

A duplicate whose amount differs from the posting already in the report is shown as an exception, with both amounts. The posting already in the report stays as it is.

**Tested by**

- The same file twice in one report is refused, and accepted in another report, or in the same report once its upload is rolled back.
- In replace-by-period mode, a September year-to-date file followed by an October one leaves exactly the October file's postings for January to October.
- In line-key mode, a second file overlapping the first adds only its new lines, and the preview counts the others as duplicates, with their amount.
- In line-key mode, a duplicate whose amount differs is an exception, and approving leaves the posting already in the report unchanged.
- In line-key mode, a row without a full line key is skipped as `No line key`, and the file still reconciles. In replace-by-period mode the same row is staged.
- Line-key mode is offered for a first upload whose rows to commit all have a full line key, and refused at approval for one where a row doesn't, or for a report that already has a committed upload.
- A posting whose upload was rolled back doesn't make a row a duplicate.
- After a report's only commit is rolled back, the report keeps its mode, and the next file isn't offered line-key mode.
- A line repeated in one file is skipped once, and the file still reconciles.

## 8. Mapping versions and resolution

A posting stores only its GL account and cost center. Its budget line and department are looked up whenever a report is shown, from the mapping in force. A mapping change therefore never edits a posting, and every past mapping stays on record with the upload that brought it.

### 8.1 The mapping in force

The GL mapping and the cost center mapping are resolved separately, by the ruleset's `mapping_rule`.

| `mapping_rule` | Which mapping applies |
| --- | --- |
| `latest` (the default) | The mapping of the most recently committed upload (10.2) that has that mapping, whole. A code missing from it is unmapped, even if an older file mapped it |
| `own_upload` | Each posting uses the mapping from its own upload. If its upload had none, the `latest` rule applies |

Rolled-back uploads are never used. Mapping values taken from the spend sheet itself (4.2) count as part of that upload's mapping.

### 8.2 What a posting gets

| Case | Budget line shown | Department shown | In the sample file |
| --- | --- | --- | --- |
| GL account and cost center both mapped | Its commitment item | Its department | Most rows |
| GL account missing from the GL mapping | No budget line | As mapped | `640200` |
| GL account listed with an empty commitment item | No budget line, marked "listed without a commitment item" | As mapped | `650200` |
| Cost center missing from the cost center mapping | As mapped | Outside IDS, by the default `outside_ids_rule` | `3100`, `3200` |
| Cost center listed with an empty department | As mapped | No department. It's inside IDS, since the mapping lists it (ENG-03, 2.3) | None |

Outside-IDS postings stay in the data. Reports flag them and can exclude them (ENG-03).

### 8.3 Catching changes

The preview compares the upload's mappings with the mappings in force, and lists each difference as an exception, with the spend it affects:

- a GL account whose commitment item changes
- a cost center whose department changes
- a code added, or a code that the new mapping no longer lists

With the `latest` rule, approving moves postings that are already committed too, and the preview says so: for example, "Approving moves 12,400.000 OMR from 2101 to 2401, including 9,100.000 OMR already committed." Rolling the upload back moves them back.

On a report with no committed upload, no mapping is in force to compare with, so the preview lists no mapping changes. It lists none either for a GL or cost center mapping the upload doesn't have (8.1). Instead, the preview names the upload whose mapping applies. If no committed upload has that mapping yet, the preview says so, and its postings show No budget line or Outside IDS (8.2).

### 8.4 The resolved view

Reports never join the tables themselves. A database view, `posting_resolved`, joins each posting that counts (Section 10.2) to its mapping in force. Every report, chart and export reads from it (ENG-03), so all of them follow the same rules.

**What it returns:** one row per posting that counts. Postings that aren't actual spend are included, with `is_actual` false. ENG-03, 2.1 keeps only actual spend.

| Columns | From |
| --- | --- |
| Every column of `posting` (Section 2), unchanged | The posting |
| `gl_name`, `ci_code`, `ci_name`, `ci_status` | The GL mapping in force (8.1) |
| `cc_name`, `dept_code`, `dept_name`, `is_outside_ids` | The cost center mapping in force (8.1), and the report's `outside_ids_rule` (Section 3) |

- **`ci_status`** is `mapped`, `missing_from_gl_map` or `listed_without_ci`, the cases in 8.2. `ci_code` and `ci_name` are empty unless it's `mapped`.
- **`is_outside_ids`** is true when the report's `outside_ids_rule` puts the posting's cost center outside IDS. `dept_code` and `dept_name` are then empty. They're empty too, with `is_outside_ids` false, for a cost center listed with an empty department.
- **Empty** means empty text, `''`, never `NULL`, in every text column of the view, as in the tables. So `gl_name` is empty for a GL account missing from the GL mapping in force, and `cc_name` for a cost center missing from the cost center mapping in force.
- **The upload's file name** isn't in the view. Screens that show it read `upload` by `upload_id`.

**How it's created:** a migration in `ledger/` creates the view with `RunSQL`, so the main database and the test database both get it (ENG-01, 7.5).

- The view's SQL is written in the migration itself. Each migration keeps its own version of the view, so replaying the migrations on a new database always gives the same result.
- A change to the view is a new migration that holds the whole new definition, with the previous definition as its reverse. An existing migration is never edited.
- A migration that changes or drops a column the view uses drops the view first and creates it again after, because PostgreSQL refuses to change a column a view depends on.
- `sql/grants.sql` runs after every migration, so a view created again gets its revokes back (ENG-01, 6.3).

**How the application reads it:** through `PostingResolved` in `ledger/models.py`, a model with `managed = False` and `db_table = "posting_resolved"`. Django never creates, changes or drops the view, and queries on it are built by the ORM, with every value passed as a parameter (ENG-01, 6.4). The model takes every column of `posting` from the abstract model that `Posting` and `StagingRow` share, and adds the mapping columns above. A migration that changes the view's columns changes the model in the same commit.

**Tested by**

- The fields of `PostingResolved` are exactly the view's columns.
- In the sample file, `640200` shows no budget line, `650200` shows listed without a commitment item, and `3100` and `3200` show outside IDS, each with the amount and rows in its expected result (12.3).
- In the sample file, the `640200` posting at `3200` shows both no budget line and outside IDS.
- The view has exactly one row per posting that counts, and its total equals theirs.
- A second file moving `610100` to another commitment item shows the move and its amount in the preview. After approval, every `610100` posting reports under the new item, and after rollback, under the old one.
- A second file whose mapping leaves out a cost center makes that cost center outside IDS under `latest`.

## 9. Reconciliation

Reconciliation proves the file was read completely: every amount in it is either staged or skipped with a reason, and together they add up to the file's own total.

### 9.1 The control total

- **From the file.** The total row (Section 5) on the spend sheet. Only a row below the last spend row qualifies, and if more than one does, the last is used. Any other row that looks like a total is an other row. `control_total_source` is `file`.
- **Entered.** If the file has no total row, the uploader types the total shown in SAP for the same selection. It's typed on the upload form, with the file, so it's given before the file is read and the person typing it hasn't seen what the rows add up to. It's typed as SAP shows it, and read by the rule for an amount written as text in the file (Section 5), with the report's decimal separator, so `1,234.500-` is minus 1234.5. A total that can't be read that way is refused with the file, before anything is stored. It's saved on the upload as it's received, with at most 3 decimal places, and `control_total_source` is `entered`. The preview shows it as typed by hand. It can't be changed afterwards: a wrong total is fixed by withdrawing the upload and uploading the file again.

If the file has a total row, the file's total is used, and a typed total is kept only in the `upload_received` event (ENG-01, 6.3). A file with no total row, uploaded with no typed total, has no control total, so its approval is blocked (9.3).

Excel adds tiny binary errors when it sums, such as `1234.5000000001`. So the difference (9.2) is worked out from the control total as read, before any rounding. The difference is then rounded half up to 3 decimal places, which removes only fractions of a baisa. A file with a skipped amount such as `12.3456` still reconciles. `control_total` is saved rounded the same way. Spend rows are never rounded (Section 5).

### 9.2 The formula

The staged rows include every spend row, actual or not, because the file's total includes them all.

| Line | From |
| --- | --- |
| Control total | 9.1 |
| minus staged rows to commit | Staging rows not marked `is_duplicate` |
| minus duplicates already in the report | Staging rows marked `is_duplicate` (line-key mode only) |
| minus skipped spend rows | Skipped rows that count in reconciliation (Section 5), with a readable amount |
| **= Difference** | Saved as `upload.reconciliation_difference` |

With no control total there's nothing to subtract from, so `reconciliation_difference` stays empty until one is entered (9.1).

### 9.3 When approval is blocked

| Condition | Why |
| --- | --- |
| The difference isn't exactly `0.000` | Some amount in the file is unexplained. There's no tolerance |
| There's no control total | Nothing to reconcile against |
| A row is in another currency (Section 5) | The file can't reconcile in OMR |
| A row's value type is unclassified (Section 5) | Counting it or leaving it out would be a guess. The upload is rejected, a fixed administrator classifies the value in a new ruleset version, and the file is uploaded again |
| A mapping code is listed with different values (Section 5) | The budget line or department its postings get would be a guess |

A skipped spend row whose amount can't be read leaves a difference, so it blocks approval through the first condition.

The preview lists each condition that applies as an exception that blocks approval (11.2), and the commit refuses an upload whose preview lists one (10.1).

Skipped rows count in reconciliation only to prove the file was read completely. They're never in the report. A file's actual spend in the report is therefore its control total minus three amounts. They're its skipped spend rows, its duplicates already in the report (line-key mode), and its rows to commit that aren't actual spend. No row is in more than one, and the preview shows each (11.1).

**Tested by**

- The sample file reconciles to `0.000`.
- A total row read as `1234.5000000001` is saved as `1234.500`, and a difference of `0.0005` is saved as `0.001`.
- A file with no total row is saved with no control total and no difference.
- Changing one amount by `0.001` blocks approval.
- A row with an unclassified value type blocks approval, and so does a mapping code listed with different values.
- Of two rows reading Total below the last spend row, the last gives the control total and the other is an other row. A Total row above the last spend row is an other row.
- A total typed on the upload form is saved with `control_total_source` as `entered`, and ignored for a file with a total row.
- A total typed as `1,234.500-` is saved as `-1234.500`, and one that isn't an amount is refused with nothing stored.
- A file of actual-spend rows with one skipped row reconciles, and its report total is lower by exactly that row's amount.

## 10. Commit and rollback

### 10.1 Commit

Approval runs as one database transaction. Either every step happens or none does.

1. Lock the report's row, so two approvals for the same report can't run at once.
2. Check that the user still has **approve**, that the upload is still at `awaiting_approval`, and that its preview lists no exception that blocks approval (9.3 and 11.2).
3. Check that the report's committed uploads, in the order of commits (10.2), are still the ones the preview was built against, which it keeps as `committed_uploads` (11.1). An upload committed or rolled back since would make the preview's figures wrong. The rules in Section 6 mean this can only happen through a fault, so the approval is refused rather than the preview rebuilt: the upload is rejected and the file uploaded again, which builds a new preview.
4. Copy the staging rows not marked `is_duplicate` into `posting`.
5. Check that the count and total of the new postings equal the preview's. If they don't, stop.
6. Set the upload to `committed` with `decided_by` and `decided_at` (10.2). On the report's first commit, also set `report.duplicate_mode` to the mode the approver chose (7.3): replace by period unless they chose line-key mode and the preview offers it. Choosing a mode on any later commit is refused. Write the audit event, with the duplicate mode in its details on a first commit.
7. Delete the upload's staging rows.

If the transaction stops at any step, nothing is written, the upload stays at `awaiting_approval`, and the error is shown. Because the audit event is in the same transaction, a commit can't happen without its audit event.

**A refused approval is on record.** An approval refused at step 2 for an exception that blocks it, at step 3, for a duplicate mode it can't choose (7.3), or at step 5 writes an `approval_refused` event with its reasons once the transaction is undone (ENG-01, 6.3). A user without **approve** gets the access check's 403 instead, and an upload no longer at `awaiting_approval` is refused with no event, since there was nothing to approve.

### 10.2 Which postings count

A posting counts when both are true:

- its upload is `committed`, not `rolled_back`, and
- in replace-by-period mode, no upload committed after it covers the posting's month (the months in 7.3).

**The order of commits.** A report's committed uploads are ordered by `decided_at`, and by `number` when two times are equal, so no two are ever left unordered. "Committed after" here, and "the most recently committed" in 8.1 and 10.3, both mean this order.

- The commit takes `decided_at` from the application's clock after it locks the report's row (10.1, step 1). PostgreSQL's `now()` isn't used: it's the time the transaction started, so a commit that waited for the lock could get an earlier time than the commit it waited for.
- `number` alone isn't used. It follows the order uploads were created, which matches the order of commits only while the report has one open upload at a time (Section 6).

**A committed upload** is one at `committed`. A rolled-back upload is never one. An upload whose postings a later upload has all replaced is still a committed upload: its mapping may still be in force (8.1), and its mapping rows and skipped rows are still part of the report. ENG-03 lists the committed uploads on its exceptions screen and on an export's Source sheet, with what each still counts.

**Actual spend** is the postings that count with `is_actual` true. Whether a posting counts is never stored. It's worked out by `posting_resolved` (8.4) from the uploads' states and order, so a rollback changes it without touching a posting.

### 10.3 Rollback

- **Only the latest.** Only the most recently committed upload of a report (10.2) can be rolled back. To undo older ones, roll back newer ones first, newest first. Replacement and mappings depend on the order of uploads, and undoing in reverse order always returns the report to a state it was really in.
- **Who and why.** It needs **roll\_back** and a reason, which is kept in the audit event, trimmed, and can't be empty. It's refused while the report has an open upload (Section 6), after any interrupted read is cleared. A refused rollback changes nothing and writes no event, unlike a refused approval (10.1): it isn't an attempt to change the figures that a check stopped, only a request made in the wrong order.
- **What changes.** The upload is set to `rolled_back` with `rolled_back_by` and `rolled_back_at`. Nothing is deleted: its postings, mapping rows and skipped rows stay, and its history can still be viewed.
- **What it restores.** The postings it replaced count again, and the mappings in force go back to those before it (Section 8). Every report figure returns to exactly what it was before the commit. The confirmation screen shows the change first, for example "Actual spend: 1,250,400.000 OMR → 1,198,200.000 OMR." The figure after is worked out as the preview's is (11.1): the upload is set to `rolled_back` in a savepoint, the actual spend is read through `reports/queries.py`, and the savepoint is rolled back.
- **After a rollback.** The file can be uploaded again (7.1).

**Tested by**

- Stopping the process during a commit leaves the upload at `awaiting_approval` with nothing written.
- After a commit, the report total equals the previous total, minus the replaced postings, plus the new ones.
- In replace-by-period mode, of two uploads committed in the opposite order to their numbers, the one committed last replaces the other's months. Two uploads with the same `decided_at` are ordered by `number`.
- After a rollback, every figure in `posting_resolved` and every `report_totals` figure equals its value before the commit, and the actual spend equals the figure the confirmation screen showed.
- Rolling back an older upload while a newer one is committed is refused, and so is a rollback while the report has an open upload, each changing nothing and writing no event.
- A rollback sets `rolled_back_by` and `rolled_back_at`, writes one event with its reason, and keeps every posting, mapping row and skipped row.
- An `UPDATE` or `DELETE` on `posting` as `ids_app` is refused by the database.

## 11. Exceptions and the preview

The preview is the approver's whole view of a file before it counts. It's built when reading ends, and its figures and exceptions are saved with the upload, so the preview of any past upload, rejected ones included, can be shown again exactly as it was.

### 11.1 The preview figures

| Figure | Shows | Saved in |
| --- | --- | --- |
| File | Name, size, uploader, time, ruleset version | `upload` |
| Period | First and last posting date, and the months replaced (replace-by-period mode) | `period_start`, `period_end` |
| Duplicate mode | On a report with no committed upload, whether line-key mode can be chosen (7.3) | `preview` |
| Rows | Read, to commit, duplicates, replaced and skipped | `rows_*` |
| Amounts | The amount for each row figure above | `preview` |
| Reconciliation | The formula in 9.2, line by line, with the difference | `control_total`, `reconciliation_difference` |
| Actual spend in this file | Total, inside IDS and outside IDS, of the rows to commit | `preview` |
| Not actual spend | The amount of the rows to commit with `is_actual` false (9.3) | `preview` |
| Value types | Each value type found, when the file has a value-type column (11.2) | `preview` |
| Mappings | Which upload's GL mapping and cost center mapping the file's postings will use (8.3) | `preview` |
| Report actual spend | Before and after approval | `preview` |
| Exceptions | Each type below, with its rows, row count and amount | `preview` |

Each exception lists the rows it covers, by sheet and row number in the original file.

**The row figures.** Every spend row read (Section 5) is in exactly one of `rows_new`, `rows_duplicate` and `rows_skipped`, so those three add up to `rows_read`.

| Field | Counts | Its amount in `preview` |
| --- | --- | --- |
| `rows_read` | The spend sheet's spend rows |  |
| `rows_new` | Staging rows not marked `is_duplicate`: the rows to commit | `to_commit` |
| `rows_duplicate` | Staging rows marked `is_duplicate` (7.3) | `duplicates` |
| `rows_skipped` | Skipped spend rows. A skipped mapping row isn't a spend row, so it's listed with the exceptions only (11.2) | `skipped`: the skipped rows that count in reconciliation, with a readable amount, as read (9.2) |
| `rows_replaced` | Postings that count (10.2), dated in the file's months, which committing replaces in replace-by-period mode (7.3). None in a report with no committed upload, or in line-key mode | `replaced` |

`preview` is a JSON object. Every amount in it is a string holding the exact amount, such as `"12400.000"`, so reading it never passes through a float. Its keys:

| Key | Holds |
| --- | --- |
| `amounts` | `to_commit`, `duplicates`, `skipped` and `replaced`: the amounts of the row figures above |
| `line_key_offered` | `true` when the report's duplicate mode isn't set yet, so this is its first upload, and line-key mode can be chosen for this file (7.3) |
| `committed_uploads` | The numbers of the report's committed uploads in the order of commits (10.2) when the preview was built, which the commit checks (10.1, step 3) |
| `months_replaced` | Each month from `period_start` to `period_end`, such as `"2026-03"`, unless the report is in line-key mode (7.3) |
| `actual_spend` | `total`, `inside_ids` and `outside_ids`: the rows to commit with `is_actual` true |
| `not_actual` | The amount of the rows to commit with `is_actual` false |
| `value_types` | Each value type among the rows to commit: its `value_type` as written, its `rows`, its `amount`, and its `class`, which is `actual`, `not_actual` or `unclassified`. Empty when the file has no value-type column |
| `mappings` | `gl` and `cc`: the number of the upload whose mapping the file's postings will use, which is this upload's own when it has that mapping, or `null` when no upload has one yet (8.1 and 8.3) |
| `report_actual_spend` | `before` and `after` approval |
| `exceptions` | The exceptions in 11.2 |

**What approving would change.** The figures that depend on the mapping in force or on which postings count are worked out by approving the upload for a moment, inside the read's own transaction: the commit's steps 4 and 6 (10.1) run in a savepoint, without the audit event, the figures are read through `posting_resolved` and `reports/queries.py` as any screen reads them, and the savepoint is rolled back. These are the actual spend inside and outside IDS, the report's actual spend after approval, the exceptions for GL accounts with no budget line, listed without a commitment item, cost centers outside IDS and cost centers listed without a department, and the spend each mapping change moves. So the preview follows exactly the rules the report will, and never a second copy of them. The report's actual spend after approval is therefore its actual spend once the upload is approved, whether rows are actual or not, replaced or duplicates.

### 11.2 Exception types

| Exception | Type in `preview` | Example | Each item | Blocks approval |
| --- | --- | --- | --- | --- |
| Difference not zero | `difference_not_zero` | `-0.500` | None. Its amount is the difference | Yes |
| No control total | `no_control_total` | No total row, and none typed | None | Yes |
| Row in another currency | `another_currency` | `USD` | A row | Yes |
| Unclassified value type | `unclassified_value_type` | `Plan`, where the rule lists only `Actual` and `Commitment` | A value type, with its `value_type` | Yes |
| Mapping code listed with different values | `listed_with_different_values` | `610100` with `2101` on one row and `2201` on another | A code, with its `mapping` and `code` | Yes |
| Skipped row | `skipped_row` | Invalid date, more than 3 decimals, unreadable code, repeated line, no line key, unreadable mapping code | A row, with its `reason` | No |
| Other row without GL account or cost center | `other_row` | A subtotal or a label | A row | No |
| GL account with no budget line | `missing_from_gl_map` | `640200` | A GL account, with its `code` | No |
| GL account listed without a commitment item | `listed_without_ci` | `650200` | A GL account, with its `code` | No |
| Cost center outside IDS | `outside_ids` | `3100` | A cost center, with its `code` | No |
| Cost center listed without a department | `listed_without_department` | `4570`, on the cost center mapping with an empty department code | A cost center, with its `code` | No |
| Mapping change | `mapping_change` | `610100` moves from `2101` to another commitment item | A code, with its `mapping`, `code`, `before`, `after` and `committed` | No |
| Mapping sheet and spend sheet disagree | `sheet_and_spend_disagree` | 4.2 | A code, with its `mapping` and `code` | No |
| Names differ for one code | `names_differ` | `2101` named `Software and licences` on one row and `Software` on another | A code, with its `field` and `code` | No |
| Duplicate with a different amount | `duplicate_different_amount` | Line-key mode, 7.3 | A row, with the `posting_amount` already in the report | No |
| A month with no rows inside the replaced months | `month_without_rows` | February empty between January and March | A month, with its `month` | No |
| Replaced postings outside the file's dates | `replaces_outside_dates` | A file dated 15 to 28 February replaces postings dated 1 to 14 February | An earlier upload, with its `upload` number and the number of its `postings` replaced | No |

`exceptions` lists only the types that occur, in the order of this table. Each is an object with its `type`, `blocks_approval`, `amount` and `items`. Each item has the `rows` it covers in the original file, as pairs of sheet and row number, which may be empty, and its `amount`, or `null` when it has none, beside the keys the table gives. A type's amount is the sum of its items' amounts, or `null` when none has one. Its row count is the number of rows its items list.

- **Rows and amounts.** A row's amount is the amount it was read with, for a skipped row only if it could be read. A mapping row has none.
- **Skipped rows** are the skipped rows of every sheet except those in another currency, which have their own type.
- **Budget lines, outside IDS and no department** cover the rows to commit that are actual spend, as the report will show them once the upload is approved (11.1), so their amounts match the exceptions screen's (ENG-03, 8). A cost center listed without a department is the preview's side of the report's No department (8.2). It doesn't block approval, as a GL account listed without a commitment item doesn't: the spend is still counted, in a group of its own, and the mapping sheet can be corrected in a later file.
- **A mapping change** is a code whose budget line or department differs between the upload's mapping and the mapping in force (8.3). `before` and `after` are the commitment item or department, empty for a GL account listed without a commitment item, and `null` when the mapping doesn't list the code. Its amount is the actual spend that approving moves, worked out as in 11.1: this file's rows to commit, compared with the mapping in force now, and the committed postings that count, of which `committed` is the part. Its rows are this file's rows that move.
- **Codes listed with different values, disagreements and differing names** come from the mapping rows (Section 5). `mapping` is `gl` or `cc`, and `field` is the name that differs, such as `ci_name`.
- **A month with no rows** is a month in `months_replaced` with no rows to commit.
- **Replaced postings outside the file's dates** are the postings approving replaces (7.3) dated before `period_start` or after `period_end`. They're in the months the file covers but not in its dates, so a file covering part of a month hides the rest of it. The exception says so plainly, for each earlier upload.

An exception that doesn't block approval still needs a look. The approver approves the file with its exceptions, and the audit event records how many items each type listed.

When the file has a value-type column, the preview lists each value type found, with its row count, its amount, and whether it's actual, not actual or unclassified (Section 5).

**Tested by**

- The sample file's preview shows exactly the exceptions its README describes: `640200`, `650200`, `3100` and `3200`, and its subtotal rows as other rows without a GL account or cost center (12.3).
- Each exception type shows once, with the right rows, in a file built to cause it. The types that need an earlier upload (mapping change, duplicate with a different amount, a month with no rows) use a pair of files in a report of the right mode.
- The preview of a rejected upload shows the same figures as before it was rejected.
- After approval, the report's actual spend equals the preview's `after`, including for a file with rows that aren't actual spend, rows that replace others and duplicates.
- A file covering 15 to 28 February, after one covering 1 to 28 February, lists the earlier postings dated 1 to 14 February as replaced outside its dates.
- A cost center on the cost center mapping sheet with an empty department code is listed as a cost center listed without a department, with its rows and its actual spend, and not as outside IDS. It doesn't block approval. A later file whose mapping is in force and lists it so raises it too, though the file has no mapping sheet of its own.
- A file with no total row, uploaded with a typed total, reconciles against it, and one uploaded without is blocked as having no control total.
- An approval refused for each blocking type writes one `approval_refused` event with its reason, and commits nothing.
- An approval after another upload was committed or rolled back since the preview was built is refused.
- In any file, `rows_new`, `rows_duplicate` and `rows_skipped` add up to `rows_read`, and a skipped mapping row counts in none of them.
- A second file covering months already committed shows the number and amount of the postings it replaces. In line-key mode it shows none.

## 12. Testing

Each section above ends with the tests that prove it. This section is how they're organised.

### 12.1 Kinds of test

| Kind | Covers | Tool |
| --- | --- | --- |
| Unit | Cleaning rules, header matching, sheet classification, line keys | pytest |
| Property | Amounts and codes read back unchanged. Every row of a random file in OMR with readable amounts ends up staged or skipped, and the file reconciles | Hypothesis |
| Golden file | A whole file from upload to report figures, compared with an expected result | pytest |
| Database | Commit, rollback, replacement, `posting_resolved` and the `ids_app` grants, on a real PostgreSQL set up as in ENG-01, 7.5 | pytest-django |
| Browser | Upload, preview, approve, report and rollback, as a user would | Playwright |

### 12.2 Golden files

Each golden file has an expected result beside it: totals by GL account, by commitment item and by department, the outside-IDS total, the skipped rows with reasons, and the reconciliation. The expected results are worked out independently of the app. For the sample file, they come from the script that generated it (12.3). The sample file imitates an SAP G/L line item export (transaction FAGLL03), with the two mapping sheets added, so that moving to the real export changes as little as possible.

| File | Proves |
| --- | --- |
| The sample file (12.3) | Title lines, the README sheet, subtotal rows, zero-padded codes, `640200`, `650200`, `3100` and `3200`, and a total that reconciles |
| The sample saved as semicolon-separated text (12.3) | Uploaded after the Excel sample into the same report, it gives the same figures, using the Excel file's mappings |
| Messy amounts | Trailing minus, brackets, thousands separators, Excel serial dates |
| Bad rows | An invalid date, 4 decimals and `#N/A` written as text, each skipped with its reason, and a total that still reconciles |
| A row in another currency | A `USD` row blocks approval, so its expected result is the preview alone |
| Decimal commas | The sample's rows as semicolon-separated text with decimal commas, such as `1.234,500-`, read with `decimal_separator` set to `","`, give the sample's figures |
| Repeated mapping codes | One exact repeat and one repeat with a different commitment item: no total doubles, and the second blocks approval |
| Two year-to-date files | Replace by period |
| Two overlapping files with line keys | Line-key mode |
| A changed mapping | A GL account moving budget line, before and after rollback |
| The real export | Added once it arrives. During the build, its expected result is SAP's own totals delivered with it: the overall total and the named GL accounts. The validator's hand-checked figures are added at acceptance |

A golden result changes only on purpose, in its own commit, with the reason in the commit message. A rule change that moves any number fails the tests until then.

The real export is client data, so it never goes into Git. It is kept in `real-export/`, with SAP's totals for it, and Git ignores everything there except the folder's README (ENG-01, 6.4). Its test reads them from that folder, and is skipped when the folder holds no export.

### 12.3 The sample file

A script, `tests/fixtures/make_fixtures.py`, makes the sample file and its semicolon-separated copy. It starts from a table of true values, such as `610100`, `4510`, `12400.000` and 4 January 2026. It writes them the way SAP does, and works out the expected results from the same table with `Decimal`. It never reads its own files back and never imports from `ids_audit`, so a bug in the app can't also hide in the expected results.

The sample looks like a real export but has no bad rows. Invalid dates, `#N/A`, extra decimals and other currencies have golden files of their own (12.2). Every spend row in it is clean and counts.

**Sheets**

| Sheet | Holds |
| --- | --- |
| README | Sentences in column A: that the file is made up, how to make it again, and every exception and subtotal row it contains. It has no recognised header, so it's ignored (4.2) |
| Transactions | The title on row 1, the selection on row 2 ("Company code 1000, posting date 01.07.2025 to 30.06.2026"), row 3 blank, and the header on row 4. Then the spend rows, sorted by GL account and posting date, with a subtotal row after each GL account and the total row last |
| GL Mapping | G/L Account, G/L Account Name, Commitment Item, Commitment Item Name |
| Cost Center Mapping | Cost Center, Cost Center Name, Department Code, Department Name. IDS cost centers only |

**Transactions columns**

| Header | Written as |
| --- | --- |
| CoCd | Text: `1000` |
| G/L Acct | Text, zero-padded to 10 digits: `0000610100` |
| DocumentNo | Text, 10 digits: `5100000123`, and `0100000045` for journals |
| Line Item | Text, 3 digits: `002` |
| Type | Text: `KR` for an invoice, `KG` for a credit memo, `SA` for a journal. Not read (4.4) |
| Doc. Date | Text, `dd.mm.yyyy`. Not read (4.4) |
| Pstng Date | Text, `dd.mm.yyyy` |
| Year | A number cell: the fiscal year, which is the calendar year (13, item 8) |
| Period | A number cell: the month |
| Cost Ctr | Text, zero-padded to 10 digits: `0000004510` |
| Reference | Text: the vendor's invoice number. Not read (4.4) |
| Text | As written. Three rows are in Arabic |
| Amount in LC | Text, as SAP writes it: `12,400.000`, `1,250.000-` |
| LCurr | Text: `OMR`, the local currency (4.4) |

A subtotal row holds only the G/L Acct, the amount and `OMR`. The total row holds `Total` in column A, the sum of every spend row, and `OMR`. It's a value, not a formula: XlsxWriter doesn't calculate formulas, and saves their result as `0`.

On the mapping sheets, codes are number cells, so `4510` stored as a number must match `0000004510` stored as text (5).

**Codes**

| Commitment item | GL accounts |
| --- | --- |
| `2101` Software and licences | `610100` Software licences, `610200` Software support, `610300` Cloud services |
| `2201` Hardware | `620100` Hardware purchases, `620200` Hardware maintenance |
| `2301` Telecom and network | `630100` Telecommunications, `630200` Data lines |
| `2401` Professional services | `640100` IT consulting |
| `2501` Training and other | `650100` IT training, `660100` IT supplies |
| Listed with an empty commitment item | `650200` Conferences and travel |
| Not on the GL Mapping sheet | `640200` IT contractors |

| Department | Cost centers |
| --- | --- |
| `APP` Applications | `4510` Application development, `4520` Enterprise applications |
| `INF` Infrastructure | `4530` Data center, `4540` Network |
| `SEC` Cyber security | `4550` Security operations |
| `GOV` IT governance | `4560` IT governance office |
| Not on the Cost Center Mapping sheet | `3100` and `3200`, outside IDS |

**Cases**

| Rows | Prove |
| --- | --- |
| `640200` on IDS cost centers, and one row on `3200` | No budget line (8.2). The `3200` row is in both exception lists and counts once in every total |
| `650200` | Listed without a commitment item (8.2) |
| `3100` on `610100` | Outside IDS on a mapped GL account (8.2), and the outside IDS filter (ENG-03, 3.1) |
| Document date 28.12.2025, posting date 04.01.2026 | The posting date is used (4.4), by the year filter too (ENG-03, 3.1) |
| One document with lines `002` and `003` | The line item is part of `line_key` (2) |
| Document `5100000123` in fiscal years 2025 and 2026 | The fiscal year is part of `line_key` |
| Journals numbered `0100000045` | Leading zeros are removed from document numbers (5) |
| Three item texts in Arabic | Kept as written (5), and shown right to left (ENG-03, 6.2) |
| `1,150,000.000` | Thousands separators (5) |
| Amounts that use all three decimals, such as `3,456.789` | Totals are exact (5) |
| Credits with a trailing minus, on several GL accounts | Negative amounts (5) |
| A posting dated `03.04.2026` | Dates are read day first (5) |
| `610100` is the largest GL account, spread over several departments and `3100` | Spend by cost center and department on the GL account screen (ENG-03, 4) |
| `620200`: a `2,000.000` cost and a `3,500.000-` credit memo | A GL account with a negative total |
| Postings in every month from July 2025 to June 2026, and spend in every department | Months and the running total (ENG-03, 5 and 7), with no empty month (11.2) |
| 12 GL accounts, each with a different total | The overview's 10 largest and one row for all the others (ENG-03, 4) |

**Files**, all in `tests/fixtures/`

| File | Holds |
| --- | --- |
| `make_fixtures.py` | The script. `uv run python -m tests.fixtures.make_fixtures` makes every file below again |
| `sample.xlsx` | The four sheets |
| `sample.expected.json` | Its expected result, as the first upload into an empty report |
| `sample_semicolon.csv` | The Transactions sheet as text: `;` between fields, UTF-8 with a byte-order mark, CRLF line endings, and every line padded to 14 fields, so line *n* is Excel row *n*. Values are as on the sheet |
| `sample_semicolon.expected.json` | Its expected result when uploaded after `sample.xlsx` into the same report: the same figures, using the Excel file's mappings (8.1), with every posting of `sample.xlsx` replaced (7.3) |

**Expected result**

Each expected result is a JSON object. Every amount is a string with three decimal places, such as `"12400.000"`, so reading it never passes through a float.

| Key | Holds |
| --- | --- |
| `fixture`, `scenario` | The file name, and the upload it describes |
| `header_row` | `4` |
| `rows` | `to_commit`, `skipped`, and `other` for the subtotal rows |
| `period` | `first_posting` and `last_posting` (11.1) |
| `reconciliation` | `control_total`, `to_commit`, `skipped` and `difference` (9.2) |
| `actual_spend` | `total`, `inside_ids` and `outside_ids` (11.1) |
| `by_gl`, `by_ci`, `by_department`, `by_month` | Actual spend per GL account, per commitment item with `none` for no budget line, per department with `outside` for outside IDS (ENG-03, 6.1), and per posting month as `2025-07` |
| `exceptions` | `missing_from_gl_mapping`, `listed_without_ci` and `outside_ids`: each code's amount and Transactions row numbers. `other_rows`: the subtotal row numbers |
| `skipped_rows` | Empty |
| `replaced` | The CSV only: the number and amount of postings replaced (7.3) |

Running the script again gives byte-identical files. The workbook's creation date is fixed, and Git never changes the fixtures' line endings (`docs/CONTRIBUTING.md`, section 9).

**Tested by**

- Running the script again leaves every file in `tests/fixtures/` byte for byte unchanged.
- In each expected result, `by_gl`, `by_ci`, `by_department` and `by_month` each add up to `actual_spend.total`, so do `inside_ids` and `outside_ids`, and the reconciliation `difference` is `0.000`.

**Done when** every test passes on a clean checkout with `uv run pytest`, and the sample file's totals match its expected result to the baisa. Once the real export arrives, its overall and GL totals must also match SAP's to the baisa.

## 13. To confirm against the real export

Every assumption the rules above depend on is listed here, including each one marked **Confirm with the real export**. Items confirmed by opening the file are checked when it arrives. Until then, the sample file follows each assumption as written. The others are questions for the client, needed by the day shown, which follows the plan's open questions. Until an answer arrives, the plan's default is what gets built. Each change goes into the part that owns it: reading, cleaning and counting (Phase 1), or duplicates and the control total (Phase 2).

| # | Assumption | Section | Confirmed by | If it's different |
| --- | --- | --- | --- | --- |
| 1 | The file is a real Excel file, not HTML saved as `.xls` | 4.1 | Opening the file | Add an HTML table reader |
| 2 | The headers match the built-in names | 4.4 | Opening the file | Add the names to `header_names` |
| 3 | The GL and cost center mapping sheets are in the file, and the cost center sheet lists only IDS cost centers | 4.2, 8.2 | Opening the file, and the client by Day 2 | Set `outside_ids_rule` to the rule the client gives, before the report's first commit (Section 3) |
| 4 | Company code, fiscal year, document number and line item are in the file | 2, 7.3 | Opening the file, and the client by Day 3 | Line-key mode isn't offered. Replace by period is used |
| 5 | The amount column is in the local currency, OMR, and the currency column read is the local currency's (`LCurr`), not the invoice's | 4.4, 5 | The client, by Day 2 | Point the amount and currency fields at the local-currency columns in `header_names`. An amount in `Val/COArea Crcy` is in the controlling area's currency, so it's used only once the client confirms that's OMR |
| 6 | Credits are negative amounts in the amount column | 5 | Opening the file | Read SAP's debit and credit column (S or H), and make H amounts negative |
| 7 | Dates are day first, or real date cells | 5 | Opening the file | Change `date_order` |
| 8 | The fiscal year is the calendar year, if there's no date column | 5 | The client, by Day 2 | Map periods to the fiscal calendar |
| 9 | Which value-type values mean actual spend, and which don't | 3, 5 | The client, by Day 2 | Set `actual_spend_rule` |
| 10 | Each export is year-to-date, or covers whole months | 7.3 | The client, by Day 3 | Use line-key mode, if item 4 allows it |
| 11 | The spend sheet has a total row | 9.1 | Opening the file, and the client by Day 3 | The uploader types the total from SAP |
| 12 | The date, document number and line item columns hold SAP's posting date, accounting document number and its line item, not the document date, the reference or a purchasing item | 4.4 | Opening the file | Point those fields at the right columns in `header_names` |
| 13 | The export is SAP's G/L line item report (FAGLL03), laid out like the sample file | 4.4, 12.2 | The client, before acceptance | Add the real report's header names to `header_names`, and update the sample file to match |
| 14 | A text export is in UTF-8, or in UTF-16 with its byte-order mark | 4.1 | Opening the file, if it's text | Add a `text_encoding` setting to the ruleset (Section 3) |
| 15 | Amounts in a text export use a point for decimals, such as `12,400.000` | 3, 5 | Opening the file, if it's text | Set `decimal_separator` to `","` |
