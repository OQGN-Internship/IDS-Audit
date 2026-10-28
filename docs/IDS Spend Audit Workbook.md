# IDS Spend Audit Workbook

The shape of the Excel workbook that gives IDS Spend Audit its most complete figures, with nothing left to fix: its three sheets, the columns on each, and the value each column expects. A workbook in this shape is read with a new report's default settings, skips no row, reconciles to `0.000`, and has nothing that blocks approval.

It sums up the reading and cleaning rules in [ENG-02 Data Model and Import](engineering/ENG-02%20Data%20Model%20and%20Import.md), sections 4 to 9. Where the two differ, ENG-02 is right. The made-up sample, `tests/fixtures/sample.xlsx`, is in this shape, with title lines, subtotal rows and a few columns the application doesn't read added on purpose to test them (ENG-02 12.3).

## 1. The workbook

- **An Excel workbook (`.xlsx`),** saved by Excel or exported by SAP as a real Excel file. An HTML page saved as `.xls`, which some SAP exports are, is refused (ENG-02 4.1). The largest file accepted is 50 MB, unless the developer raises it.
- **Three sheets:** Transactions, GL Mapping and Cost Center Mapping. The application finds each sheet by its header row, never by its name or its place in the workbook, so the names are for people.
- **Any other sheet,** such as a README, is ignored, as long as none of its first 25 rows has a GL account or a cost center header. A pivot or summary sheet with those headers can count as a second spend or mapping sheet, and the file then fails.
- **One header row on each sheet,** best on row 1. Title lines above it are allowed, as long as the header is within the first 25 rows.
- **Each header once.** Headers are compared ignoring case, spaces and punctuation, so `G/L Account` and `gl account` are the same. A column whose header isn't in the tables below, such as `Doc. Date`, `Reference` or `Type`, is ignored. A second column with a recognised header, such as `Date` beside `Pstng Date`, or `Item Text` beside `Text`, matches a field already matched, and the upload stops at Needs columns until the uploader picks one.
- **Saved values.** Only a formula's saved result is read. A workbook saved in Excel keeps them, but one written by a program that doesn't calculate may not, and a missing result reads as an empty cell. `#N/A` and other error cells also read as empty.
- **A CSV file** can be uploaded instead, but it holds only the Transactions sheet, so its postings use the report's latest approved mapping. It must be saved as CSV UTF-8 (ENG-02 4.1).
- **The mapping sheets on their own,** in a workbook without Transactions, are sent with **Upload a mapping** whenever they change (ENG-02 8.5). The files after it then need only Transactions.

## 2. Transactions: one row per SAP line item

This is the spend sheet. A row is a spend row when both its GL account and its cost center cells hold something. Every other row is the total, a subtotal or a label.

| Header | Also recognised | Needed | Expected value | Example |
| --- | --- | --- | --- | --- |
| `CoCd` | `Company Code` | For the line key | The SAP company code | `1000` |
| `G/L Acct` | `G/L Account`, `GL Account`, `Cost Element`, `Account` | On every row | The GL account. Leading zeros and number cells are fine: `0000610100` and `610100` are the same account. Only English letters, digits, `-`, `_` and `.`; anything else, such as `#N/A` written as text, skips the row | `0000610100` |
| `DocumentNo` | `Document Number`, `Doc. No.` | For the line key | SAP's accounting document number, not the reference or the vendor's invoice number | `5100000123` |
| `Line Item` | `Item` | For the line key | The line's number within its document | `002` |
| `Pstng Date` | `Posting Date`, `Date` | On every row | The posting date, not the document date. An Excel date cell is best. Text is read day first, as `dd.mm.yyyy`, `dd/mm/yyyy` or `dd-mm-yyyy`, and `yyyy-mm-dd` as written. An empty date, or one that isn't a real day such as `31.02.2026`, skips the row | `04.01.2026` |
| `Year` | `Fiscal Year` | For the line key | The fiscal year, as a whole number. `FY26` skips the row | `2026` |
| `Cost Ctr` | `Cost Center`, `Cost Centre`, `CCtr` | On every row | The cost center, by the same rules as the GL account. A line with no cost center isn't spend: it's listed as an other row, and its amount stops the file reconciling | `0000004510` |
| `Text` | `Item Text`, `Description` | No | The line's description, kept exactly as written, Arabic included | `Annual licence renewal` |
| `Amount in LC` | `Amount`, `Value in Local Currency`, `Val/COArea Crcy` | On every row | The amount in the local currency, OMR, with at most three decimal places. A number cell, or text as SAP writes it, with a point for decimals and commas between thousands. Credits are negative, with a leading minus, a trailing minus as SAP writes it, or brackets. An empty or unreadable amount, or a fourth decimal place such as `12.3456`, skips the row. `Val/COArea Crcy` is in the controlling area's currency, so it's right only if that's OMR | `12,400.000` or `1,250.000-` |
| `LCurr` | `Local Currency`, `Company Code Currency` | No, but best kept | The local currency: `OMR` on every row. A row in another currency is skipped and blocks approval. Without the column, every row is taken as OMR. The invoice's own currency column, such as `Currency` or `Crcy`, isn't read | `OMR` |

**The line key.** With the company code, fiscal year, document number and line item on every row, each line has a key such as `1000-2026-5100000123-2`. The key must be unique within the file: a repeated one skips the second row. A key on every row also lets the approver choose line-key mode on the report's first upload (ENG-02 7.3).

**Columns best left out:**

- **`Period`** isn't needed while there's a posting date. It's read, with the fiscal year, only when the sheet has no date column.
- **`Value Type`** is needed only if the export holds rows that aren't actual spend, such as commitments. A fixed administrator then sets the report's `actual_spend_rule`, and every row needs a value the rule names (ENG-02 3). By default every row counts as actual spend.
- **Commitment item, department and name columns** belong on the mapping sheets, which decide each GL account's budget line and each cost center's department. On Transactions they're read as mappings too, which only adds disagreements to check.

**The rows:**

- **One row per line item,** in any order. Blank rows are ignored.
- **The total row, last.** One row below the last spend row, with the word `Total` in a cell (column A is usual), no GL account and no cost center, and the sum of every spend row in the amount column. It's the file's control total, and the spend rows must add up to it exactly. A `SUM` formula is fine, since Excel saves its result, and its tiny rounding errors, such as `1234.5000000001`, are allowed. Without a total row, the uploader types SAP's total on the upload form.
- **No subtotal rows.** They're allowed, but each is listed in the preview as an other row, which is only more to read.
- **Amounts worked out by a formula** are rounded to three decimals with `ROUND(..., 3)`. A cell formatted with three decimals can hold more: `=12.345*1.05` shows `12.962` but holds `12.96225`, and that row is skipped. The cell's value is read, never what its format shows.

## 3. GL Mapping: one row per GL account

| Header | Also recognised | Needed | Expected value | Example |
| --- | --- | --- | --- | --- |
| `G/L Account` | `GL Account`, `GL Acct`, `Cost Element`, `Account` | On every row | The GL account, by the same rules as on Transactions | `610100` |
| `G/L Account Name` | `GL Name` | No, but best kept | The account's name, shown beside its code on every screen | `Software licences` |
| `Commitment Item` | `Cmmt Item` | The column, yes | The budget line the account counts under. An empty cell means the account has none: it's shown as listed without a commitment item | `2101` |
| `Commitment Item Name` | `Cmmt Item Name` | No, but best kept | The budget line's name | `Software and licences` |

- **No amount or cost center column.** With either, the sheet is no longer a GL mapping sheet.
- **Every GL account IDS spends on, in every mapping,** not only the accounts in one file. The latest approved mapping applies whole, so an account it leaves out shows No budget line, in earlier months too (ENG-02 8.1).
- **Each account once.** An account listed twice with the same commitment item changes nothing. With two different ones, approval is blocked.
- **One name for each code.** A code named differently on two rows is listed as an exception, and its first name is used everywhere.
- **No note rows.** A row with no GL account is listed in the preview as an other row.

## 4. Cost Center Mapping: one row per IDS cost center

| Header | Also recognised | Needed | Expected value | Example |
| --- | --- | --- | --- | --- |
| `Cost Center` | `Cost Centre`, `Cost Ctr`, `CCtr` | On every row | The cost center, by the same rules as on Transactions | `4510` |
| `Cost Center Name` | `Cost Centre Name` | No, but best kept | The cost center's name | `Application development` |
| `Department Code` | `Dept Code`, `Department` | The column, yes | The IDS department the cost center belongs to. An empty cell means it's inside IDS with no department | `APP` |
| `Department Name` | `Dept Name` | No, but best kept | The department's name | `Applications` |

- **IDS cost centers only.** A cost center on Transactions that this sheet doesn't list is outside IDS. Its spend still counts in the total, and every screen marks it and can leave it out.
- **Every IDS cost center, in every mapping,** for the same reason as on GL Mapping.
- **No amount or GL account column.** Watch for a column headed `Account`, which reads as a GL account.
- **`Department Code` and `Department Name`** are the clearest headers. If the sheet has `Department` and `Department Code`, the one ending in "code" is the code and the other is the name.
- **Each cost center once, one name for each code, and no note rows,** as on GL Mapping.

## 5. An example

These rows are made up.

**Transactions**

| CoCd | G/L Acct | DocumentNo | Line Item | Pstng Date | Year | Cost Ctr | Text | Amount in LC | LCurr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | 0000610100 | 5100000123 | 002 | 04.01.2026 | 2026 | 0000004510 | Annual licence renewal | 12,400.000 | OMR |
| 1000 | 0000620200 | 5100000201 | 001 | 15.03.2026 | 2026 | 0000004530 | Credit memo for returned server | 3,500.000- | OMR |
| Total |  |  |  |  |  |  |  | 8,900.000 | OMR |

**GL Mapping**

| G/L Account | G/L Account Name | Commitment Item | Commitment Item Name |
| --- | --- | --- | --- |
| 610100 | Software licences | 2101 | Software and licences |
| 620200 | Hardware maintenance | 2201 | Hardware |

**Cost Center Mapping**

| Cost Center | Cost Center Name | Department Code | Department Name |
| --- | --- | --- | --- |
| 4510 | Application development | APP | Applications |
| 4530 | Data center | INF | Infrastructure |

## 6. Each new file

- **Whole months, complete, best year to date.** Each file is SAP's complete selection for the months it covers, taken after they close. In the default mode, approving a file replaces every posting already in the report dated in those months (ENG-02 7.3). A year-to-date file therefore restates the year, and a file covering part of a month hides the rest of that month.
- **Both mapping sheets, complete, whenever they change,** with **Upload a mapping**, or in the file. A file without them uses the latest approved mapping.
- **A new file each time.** A file already approved in the report is refused if it's sent again.

## 7. What the preview shows

For a file in this shape, the preview after **Upload and read** shows:

- a difference of `0.000` against the control total;
- no skipped rows and no other rows;
- no exception that blocks approval.

Any exceptions left are facts about the data, not faults in the file: GL accounts with no budget line, cost centers outside IDS or with no department, and mappings that changed since the last file. The approver looks at each before approving.

## 8. When SAP's export is different

Don't edit SAP's export by hand each month. Tell the developer once. Most differences are a setting on the report, changed by a fixed administrator (ENG-02 3), and ENG-02 13 lists each one to confirm against the real export.

| The export has | What's done |
| --- | --- |
| Other headers | They're added to the report's header names, on its Rules page, the only fix for every column. A column pick at Needs columns fixes one file, and not every column (below) |
| Dates month first, such as `04.15.2026` | The report's `date_order` |
| Decimal commas in a text export, such as `1.234,500-` | The report's `decimal_separator` |
| Rows that aren't actual spend | The report's `actual_spend_rule`, read from a Value Type column |
| No total row | The uploader types SAP's total on the upload form |
| An HTML page saved as `.xls` | A reader for it is added to the application |

**Other headers.** A fixed administrator adds each new header to its field's list on the report's Rules page, or with `change_ruleset` ([command.md](command.md)). Every file received after the change then reads, wherever the column sits. Everyone who can open the report can see the header names recognised there, before sending a file.

A column pick is for one file. When a needed column, such as the amount, the posting date, the commitment item or the department code, isn't recognised, the upload stops at Needs columns and the uploader picks it. The pick doesn't add the header to the report's header names. Once that file is approved, the pick is reused for the report's next file whose header row on that sheet is exactly the same, cell for cell (ENG-02 4.5). A column added, removed, renamed or moved means picking again.

The GL account and cost center columns can't be picked, since their headers are how each sheet is found (section 1). When one isn't recognised:

- **On Transactions,** the file fails with "The file has no spend sheet".
- **On GL Mapping or Cost Center Mapping,** that sheet is ignored without a message, and the file still reads with the latest approved mapping, as a file without that sheet does (section 6). A mapping sent with **Upload a mapping** whose sheets are all ignored this way fails, since it has no mapping sheet. On the report's first file there's none, so every GL account has no budget line, or every cost center is outside IDS. Those exceptions don't block approval, so the approver checks them before approving.
