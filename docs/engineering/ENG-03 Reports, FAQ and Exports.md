# ENG-03 Reports, FAQ and Exports

Implements: Phase 3 of [IDS Spend Audit Delivery Plan](../IDS%20Spend%20Audit%20Delivery%20Plan.md). Builds on [ENG-01](ENG-01%20Architecture,%20Security%20and%20Operations.md) and [ENG-02](ENG-02%20Data%20Model%20and%20Import.md).

## 1. Purpose and scope

This document defines what each screen, FAQ page and export shows, and exactly how each figure is calculated. It's written for the validator, who checks the figures against SAP, and for the developer.

It covers where every figure comes from, the filters, the screens, the charts, drill-down to source rows, the FAQ pages, the exceptions screen, the Excel export, speed and testing. Which postings count, and how they get there, is in ENG-02. Sign-in, permissions and the audit log are in ENG-01.

The FAQ pages answer the plan's eight questions, which came from the client, and more can be added when the client needs them (7.1). Section 12 lists the items still to confirm.

## 2. Where every figure comes from

Every figure on every screen, FAQ page and export comes from one set of functions in `reports/queries.py`, run over `posting_resolved` (ENG-02, 8.4). PostgreSQL adds the amounts as exact decimals. No view, template or chart adds amounts itself, and `manage.py report_totals` (ENG-01, 7.2) calls the same functions. The functions for the total and the groups in 2.3 are built in Phase 1, for its gate. The rest of this document is Phase 3 (ENG-01, 4, Build order).

### 2.1 What's counted

Every figure starts from the same rows:

| Condition | Why |
| --- | --- |
| The posting counts (ENG-02, 10.2) | Rolled-back and replaced postings never appear |
| `is_actual` is true | Only actual spend is spend |
| It belongs to the report in the address | Each report is separate |
| It matches the filters (Section 3) | The screen says which filters are on |

### 2.2 Measures

| Measure | Definition |
| --- | --- |
| Actual spend | The sum of `amount` over the rows above |
| Lines | The number of those rows |
| Share | A group's actual spend divided by the total under the same filters, rounded half up to one decimal place. Shares aren't adjusted to add up to exactly 100.0%. When that total is zero or negative, there's no share: the screen shows `–` and the export leaves the cell empty, since a share of a total that isn't spend would mislead |
| Running total | Actual spend added up month by month, in posting-date order, from the first month the filters include |

A sum over no rows is `0.000`, never empty. Each share is worked out in `reports/queries.py` from the two sums PostgreSQL returns, with `Decimal`, so no screen or template divides.

### 2.3 Groups

| Group by | Key | Name shown | Catch-all group |
| --- | --- | --- | --- |
| GL account | `gl_code` | From the GL mapping in force (ENG-02, 8.1) | None: every posting has a GL account |
| Budget line | `ci_code` | From the GL mapping in force | No budget line |
| Cost center | `cost_center_code` | From the cost center mapping in force | None: every posting has a cost center |
| Department | `dept_code` | From the cost center mapping in force | No department, for a cost center inside IDS that its mapping row gives no department code (ENG-02, 5 keeps such a row). Outside IDS, for a cost center outside IDS |
| Month | Month of `posting_date` | `Mar 2026` | None |

Every posting falls into exactly one group, so the groups on a screen always add up to its total. A code with no name in the mapping is shown as the code alone. Within one upload's mapping, each code has one name, the first one given (ENG-02, 5, Mapping rows). The `own_upload` rule (ENG-02, 8.1) can give one code names from different uploads' mappings, and the group then shows the name that sorts last, so it still has one row.

**Several in force at once.** Under `own_upload`, one GL account's postings can also get different budget lines, and one cost center's different departments, from different uploads' mappings. A list of GL accounts then shows "Several (*n*)" as that account's budget line, and the GL account screen splits its spend by budget line, one row each. A list that shows a cost center with its department gives each pair a row of its own. Under the default `latest` rule, each code has one budget line or department, so this never shows.

**The functions.** Every function takes the report and the filters (Section 3), and the detail screens' functions also take the keys of 6.1, so a figure and its drill-down count the same rows. `totals` returns the actual spend in all, inside IDS and outside IDS, and its lines. `by_gl_account`, `by_budget_line`, `by_department` and `by_month` each return one group per key: its key, the name shown, its actual spend and its lines. A catch-all's key is the value its address uses (6.1): `none` for No budget line and for No department, and `outside` for Outside IDS. A clean code is never lower case (ENG-02, 5), so a catch-all is never taken for a code. Groups come in key order with the catch-alls last, and a screen sorts them as Section 4 says. Outside IDS is decided by `is_outside_ids` (ENG-02, 8.4), so a cost center inside IDS with no department code is in No department, never Outside IDS. Each function runs one query, with every sum and count done by PostgreSQL (Section 10).

### 2.4 Formats

| Value | Format | Example |
| --- | --- | --- |
| Amount | OMR, 3 decimal places, comma for thousands, minus for negatives | `-1,250.000` |
| Share | One decimal place | `12.4%` |
| Date | Day first, as in SAP exports | `03.04.2026` |
| Time | Muscat time | `03.04.2026 14:05` |
| Code | As cleaned (ENG-02, 5) | `610100` |

Amounts are formatted on the server from `Decimal`. They're already exact to 3 decimal places, so no amount is ever rounded.

**Tested by**

- For the sample file, every figure equals the expected result from the script that generated it.
- On every grouped screen, the groups add up to the total exactly.
- A rolled-back upload's postings appear nowhere.
- A posting with `is_actual` false appears nowhere.
- `report_totals` gives the same figures as the overview. For the sample file, its figures equal the expected result's actual spend and groups.
- A report with no committed upload has every total at `0.000` and no groups.
- A share is empty when the total under its filters is zero or negative, and is rounded half up otherwise.
- A cost center inside IDS whose mapping row has no department code is in No department, and its postings are in neither Outside IDS nor any department.
- Under `own_upload`, a GL account whose postings get two budget lines shows "Several (2)" on the GL accounts screen, and its screen splits its spend between the two.

## 3. Filters and the as-of line

### 3.1 Filters

There are two filters, as the plan's Phase 3 lists. They apply to every screen, FAQ page and export, except where Section 8 says otherwise.

| Filter | In the address | Values | Default |
| --- | --- | --- | --- |
| Year | `year` | A calendar year of the posting date, as four digits such as `2026`, or `all` | `all`, since the goal is cumulative spend |
| Outside IDS | `exclude_outside` | `0`: included and flagged. `1`: excluded | `0`, as the plan's scope says: flagged, with an option to exclude |

- **In the address.** Filters are part of the address (ENG-01, 5.2), so a view can be bookmarked, and its export uses exactly the filters on screen. A filter left out of the address takes its default.
- **Always shown.** Every screen states the filters in words above the figures, for example "Year: 2026. Outside IDS: excluded." or "Year: all. Outside IDS: included." An export states them on its Source sheet (9.2).
- **The choices offered.** The year list offers All years and each year in which the report has actual spend, newest first. A year in the address with no actual spend is offered too, so the list always shows the year chosen.
- **The FAQ list.** `/reports/<id>/faq` takes the two filters and refuses a wrong one, like any screen, though it shows no figures (7.1).
- **Refused when wrong.** A value outside the table returns 400 Bad Request, and so does a key given twice, or a key the screen doesn't take. A screen takes the two filters, and its own keys where this document gives it some: the source rows' keys (6.1) and the FAQ pickers (7.2). The app only builds valid addresses, so a wrong one has been typed or edited.
- **Nothing matched.** A year with no postings is still a valid filter. When the report has a committed upload but the filters match no actual spend, the screen says "No actual spend for these filters." in place of its figures, rather than zeros that read like a finding. An export of it has each sheet's header and total rows only.

### 3.2 The as-of line

Every screen, FAQ page and export says which data it shows:

- "Figures as of upload *n*, *file name*, approved by *user* on *date and time*." This names the most recently committed upload: the last committed upload in the order of commits (ENG-02, 10.2). It depends on no filter.
- If an upload is awaiting approval, a second line says: "Upload *n* is awaiting approval and isn't included."
- A report with no committed upload shows "No approved uploads yet" in place of figures, and a link to Uploads for users with **upload**.

**Tested by**

- With `year=2025`, only postings dated in 2025 are counted.
- With `exclude_outside=1`, cost centers `3100` and `3200` disappear from the sample, and the total falls by exactly their amount.
- The export of a filtered view states the same filters.
- After a rollback, the as-of line names the upload before it.
- `year=abc`, `year=26`, `exclude_outside=2`, a key given twice and an unknown key each return 400.
- A year with no postings shows "No actual spend for these filters.", and the year list offers the years with actual spend, and that year as chosen.

## 4. Screens

Every screen belongs to one report and sits under `/reports/<id>/`. Each one shows its heading, the filter line and the as-of line (Section 3), then the figures.

![Every figure leads down to the rows it came from](../images/eng03-screen-map.png)

Every screen except Uploads and the Audit log also exports to Excel, at its own address plus `/export`, with the same filters (Section 9).

| Screen | Address under `/reports/<id>/` | Shows | Needs |
| --- | --- | --- | --- |
| Overview | `overview` | Total actual spend, split into IDS and outside IDS, and the number of lines. The 10 largest GL accounts and one row for all the others, as a table and a chart. The running total by month, as a table and a chart | **view** |
| GL accounts | `gl` | Each GL account with its name, budget line, actual spend, share, lines and number of cost centers | **view** |
| GL account | `gl/<code>` | Its budget line, its spend by cost center and department, and its spend by month with the running total | **view** |
| Departments | `departments` | Each department's actual spend, share and lines, with No department and Outside IDS as rows of their own (2.3) | **view** |
| Department | `departments/<code>` | Its spend by GL account and by cost center, and its spend by month with the running total | **view** |
| Budget lines | `budget-lines` | Each commitment item with its GL accounts and actual spend, with No budget line as its own row | **view** |
| Budget line | `budget-lines/<code>` | Its GL accounts and their spend | **view** |
| Source rows | `rows` | The postings behind any figure (Section 6) | **view** |
| Exceptions | `exceptions` | Section 8 | **view** |
| FAQ | `faq` and `faq/<slug>` | Section 7 | **view** |
| Uploads | `uploads` | Each upload with its state and preview (ENG-02, Sections 6 and 11). Its addresses are in the table below | **view**. The preview of an upload that was never committed also needs **upload** or **approve**. Each action needs its own permission |
| Audit log | `audit` | The report's events, as below | **view\_audit\_log** |

The report list at `/reports/` shows only the reports the user can access (ENG-01, 6.2), by name, each as a card. A card links the report's name to `/reports/<id>/`, which opens its overview, and shows the report's actual spend, all years with outside IDS included, linked to its source rows like any amount. Under it, the card says which upload the figures are as of, as "As of upload *n*, approved on *date and time*", or "No approved uploads yet", and names an open upload (ENG-02, 6) as "Upload *n* is awaiting approval" or "Upload *n* needs its columns picked", linked for a user who may open it (the Uploads table below). Fixed administrators also see **New report** there. It opens `/reports/new`, which asks only for the report's name (ENG-02, 3), then opens the new report's Uploads. Anyone else gets 403 from that address (ENG-01, 8).

**Each screen's page**

- **The toolbar.** Above the figures, a toolbar holds a form with the two filters (3.1), and on FAQ pages 4 and 6 the picker (7.2), and at its end the Export link. Changing any of them sends them with HTMX, which swaps the panel and updates the address (ENG-01, 5.2). Without JavaScript, the form's Show button loads the page with them.
- **What the figures are.** Under the toolbar, the filter line and the as-of line (Section 3) say which filters are on and which upload the figures are as of. The as-of line links its upload. An upload awaiting approval is a notice (OQGN's theme, below), which links the upload for a user who may open it.
- **The panel holds everything the filters change:** the toolbar itself, the filter line, the as-of line, the tables, the chart data, and every link that carries the filters, which are the drill-down links, the pages of source rows and the Export link. So after a change nothing on screen keeps the old filters, and Export downloads what's shown. The form is in the panel too because a picker's choices follow the filters, largest first, so the form is drawn again with them. Export is a plain link, never an HTMX request, since a download can't be swapped into a page.
- **A panel or a page.** An HTMX request gets the panel alone. Any other request gets the whole page, and so does HTMX's request for a page that's no longer in its history, after the user goes back.
- **Navigation.** On every page of a report, the sidebar names the report and links its screens in two groups: Overview, GL accounts, Departments, Budget lines, Exceptions and FAQ, which show the figures, then Uploads, and the Audit log for users with **view\_audit\_log**, which show where the figures came from. While the report has an open upload, awaiting approval or needing its columns picked (ENG-02, 6), its Uploads link says "Waiting" after its name.
- **One source for a screen and its export.** Each screen's tables are built once, in `reports/screens.py`, from the functions in `reports/queries.py`. The page shows them and the export writes them (Section 9), so the two can't differ.

| Screen | Its tables, each also a sheet of its export |
| --- | --- |
| Overview | *Actual spend*: on IDS cost centers, outside IDS, and the total, with its lines. *Largest GL accounts*: GL account, name, actual spend, share and lines, for the 10 largest and all the others. *By month*: month, actual spend, running total and lines |
| GL accounts | *GL accounts*: GL account, name, budget line, actual spend, share, lines and cost centers |
| GL account | *Budget lines*: budget line, name, actual spend and lines. *Cost centers*: cost center, name, department, actual spend and lines. *By month* |
| Departments | *Departments*: department, name, actual spend, share and lines |
| Department | *GL accounts*: GL account, name, actual spend and lines. *Cost centers*: cost center, name, actual spend and lines. *By month* |
| Budget lines | *Budget lines*: budget line, name, its GL accounts, actual spend and lines |
| Budget line | *GL accounts*: GL account, name, actual spend and lines |
| Source rows | *Source rows*: the columns in 6.2 |
| Exceptions | One table for each list in Section 8 |

**Uploads and each upload**

| Address under `/reports/<id>/` | Shows or does | Needs |
| --- | --- | --- |
| `uploads` | Every upload, newest first: its number, file name, state, who uploaded it and when, and who decided it and when. An open upload says beside its state when it waits for the signed-in user: "Waiting for your approval" to a user who may approve it, and "Waiting for you to pick columns" to a user who may pick them. Opening it first marks any interrupted read `failed` (ENG-02, 6) | **view** |
| `uploads/new` | The upload form: the file, and SAP's total for a file with no total row (ENG-02, 9.1). Sending it receives the file and reads it, then opens the upload | **upload** |
| `uploads/<n>` | The upload: its file, state and decisions, the reason given for a rejection, withdrawal or rollback, its column problems at `needs_columns`, its error at `failed`, its preview once read (ENG-02, 11), and a form for each action the user may take | **view**. For an upload never committed, also **upload** or **approve** |
| `uploads/<n>/exceptions/<type>` | The items of one exception type in the preview, each with its rows by sheet and row number | As `uploads/<n>` |
| `uploads/<n>/approve` | Approves it. On a report's first upload, the approver also chooses the duplicate mode: replace by period, or line key where the preview offers it (ENG-02, 7.3) | **approve** |
| `uploads/<n>/reject` | Rejects it, with a reason | **approve** |
| `uploads/<n>/withdraw` | Withdraws it, with a reason | **upload**, as its uploader (ENG-01, 6.2, rule 6) |
| `uploads/<n>/columns` | At `needs_columns`: each problem, with the header row and the next 5 rows of its sheet under their column letters, and a column to pick for each field (ENG-02, 4.5) | **upload**, as its uploader |
| `uploads/<n>/roll-back` | The confirmation, with the report's actual spend now and once rolled back (ENG-02, 10.3), and a reason. Confirming rolls the upload back | **roll\_back** |

- **The upload's page, in reading order.** First its state, as a badge beside the heading, and who uploaded and decided it. Once it's read, a summary of four tiles from its saved preview: the difference from the control total, or No control total; the report's actual spend after approval, with the figure before; the rows to commit, with the duplicates and skipped rows; and the number of exception types, with how many block approval. A tile showing something that blocks approval has the problems' bar and says so in words. Then its column problems at `needs_columns`, its preview, with its figures side by side on a wide screen, and its file. The actions come last, so an approver reaches Approve after the preview, and while the user may take one, the summary links to them. The summary's figures are the preview's own, so the two can't differ.
- **Actions are forms.** Each action is a form sent with POST, which then opens the upload again with the result. A refused action changes nothing, and the page says why, in the refusal's own words, such as "Upload 3 is still open (awaiting approval)." The actions work without JavaScript.
- **Only what the user may do.** An action's form appears only for a user who may take it, and its address checks the permission again (ENG-01, 6.2).
- **Approve is offered while an upload awaits approval,** with any exceptions that block it named beside the button. Approving a blocked upload is refused by the commit's own check and kept as `approval_refused` (ENG-02, 10.1), so the screen holds no second copy of what blocks approval.
- **Long exception lists open on request.** One exception type can list thousands of rows, so the preview shows each type's row count and amount, and its items open on request: in place, as an HTMX panel, or without JavaScript as their own page.
- **Amounts and times** follow 2.4.

**The audit log**

A report's Audit log lists the events that name the report (ENG-01, 6.3). Fixed administrators also have the whole log at `/admin/audit` (ENG-01, 6.1), which adds sign-ins, account and role changes, and a column naming each event's report. Both are read-only, and neither has an export.

- **Newest first, 100 to a page,** by the event's number, so the order never changes. The address takes only `page`, as source rows do (6.1): any other key, or a page that isn't a whole number from 1, returns 400, and a page past the last 404.
- **Columns:** the time, to the second in Muscat time, since several events can share a minute; who acted, as "Full name (username)"; the action, in words, such as "Upload approved"; the upload's number, if the event names one; and the details.
- **Who acted, when no user did:** "Command line" for an event that keeps its `command`, "Unknown username" for a failed sign-in or a lock-out of a username no account has, and "The application" for a change it made on its own, such as marking an interrupted read `failed` (ENG-01, 6.3).
- **The details** are the event's `details`, each as its name and value, by name. PostgreSQL keeps `details` as `jsonb`, which doesn't keep the order they were written in. A value that holds more, such as an export's filters, is written as JSON, with its names in order too. They're escaped like every other value (ENG-01, 6.4).
- **Navigation.** The report's navigation links the Audit log only for users with **view\_audit\_log**. The sidebar's Administration group links the whole log, as Whole audit log, and so does the administration home.

**Rules**

- Every amount links to its source rows (Section 6).
- Lists are sorted by actual spend, highest first, then by code, so the order never changes between visits. The catch-all rows come last whatever their amount, No budget line, No department, then Outside IDS, so none is read as one of the codes. Month lists run oldest first instead, so the running total reads in order.
- The overview's row for all the other GL accounts appears only when there are more than 10, and names how many it covers, such as "All other GL accounts (2)". PostgreSQL adds its amount over every posting outside the 10 largest, so it's a sum like any other figure.
- Every list ends with a total row, equal to the figure the list breaks down. For a list of the whole report, that's the overview total under the same filters. For a detail list, it's the amount of the GL account, department or budget line the list is about. For an exceptions list, it's the sum of its own rows. PostgreSQL adds every total row, as every figure (Section 10), so no screen adds up its own rows.
- A code links to its own screen, and a budget line or department beside it to that one's screen.
- The Export button appears only for users with **export**. The export address checks **view** and **export** again (ENG-01, 6.2).
- Screens are in English. Names and item text are shown exactly as written, Arabic included.
- **One look for every screen.** Every page extends one base template, which holds the navigation, and takes its look from two stylesheets: Tabler's (ENG-01, 3), which gives the layout and the components, and the application's own, `static/css/app.css`, which gives them OQGN's theme (the plan, Phase 4). A page uses their classes, never styles written into it. So a change to the theme changes the base template and `app.css`, and every screen follows. Tabler, the logo and the icons are kept in the repository like the JavaScript libraries, and no font file is used, so no screen loads anything from outside (ENG-01, 6.4).

**OQGN's theme**

The plan's Phase 4 gives every screen OQGN's colours and logo once the screens are complete. They come from OQGN's brand guide, which the developer received on 2026-10-25: colours taken from the stylesheets of oqgn.om, the official logo file, and accessible variants worked out from those colours. The colours, the type and the sidebar change only the base template and `static/css/app.css` (the rule above). The overview's tiles change how its first table is drawn, and the charts read their colours by the theme's names.

On 2026-10-26 the developer asked for clearer navigation and a more modern, polished look that stays professional. On 2026-10-27 they chose Tabler, a set of components built on Bootstrap 5, so screens are quicker to build and share one finish. Tabler gives the page's layout, the sidebar, the page header, cards, tables, badges, alerts, buttons and form fields, with its spacing and type sizes. OQGN's theme keeps what the brand decides: the colours, square corners, no shadows, the logo, the font and the focus ring. oqgn.om is built on Bootstrap too, with square corners, so the two meet. The layout of each page, the kinds of button, the badges and notices, the tables, narrow screens and printing are below.

| Part | Token | Colour | Used for |
| --- | --- | --- | --- |
| Page | surface | `#FFFFFF` | The background of every page, the sidebar, tiles, cards, notices and messages |
| Text | ink | `#000000` | Text, headings, a tile's figure, and every link on a band of surface-alt: table headings, the sidebar's screen open now and the link under the pointer, and the "No approved uploads yet" box |
| Secondary text | ink-subtle | `#706F6F` | The filter line, the as-of line, hints, captions, the legend, the crumbs, labels such as the sidebar's group names and a tile's name, and an empty list's None, on white only |
| Links | green-text | `#587D39` | Links on white, the focus ring, and the bar beside the screen open now in the sidebar |
| Logo line | green-alt | `#73A846` | The 4px line under the logo |
| Bands | surface-alt | `#EDEDED` | Table headings, the sidebar's screen open now and the link under the pointer, badges, and the "No approved uploads yet" box |
| Lines | line | `#D1D3D4` | Lines between table rows, around tiles, cards, notices and messages, under the page header and the toolbar, and at the end of the sidebar |
| Controls | ink-subtle | `#706F6F` | The border of every field and list, and of a quiet button |
| Buttons | green-dark | `#344E26` | A main button's background, with white words, and a secondary button's border and words |
| Problems | danger | `#E63B48` | A 4px bar at the start of an error, a refusal, what blocks approval and a summary tile that shows it, and on a failed upload's badge. The words stay ink |
| Success | green-text | `#587D39` | The same bar on a success message, and on a committed upload's badge |
| Notices | orange-text | `#AB5B16` | The same bar on a notice, such as an upload awaiting approval, and on the badge of an upload that's reading, needs its columns or awaits approval |
| Closed | ink-subtle | `#706F6F` | The same bar on the badge of a rejected or rolled-back upload |
| Charts | green-text and green-dark | `#587D39` and `#344E26` | Bars, and the running-total line |
| Outside IDS | orange-text | `#AB5B16` | Its bars, and its swatch in the legend |

- **Colours are custom properties.** Each colour above is defined once, in `:root` at the top of `static/css/app.css`, named after its token, such as `--oqgn-green-text`. `app.css` sets Tabler's colour variables to them, such as `--tblr-primary` to green-dark and `--tblr-border-color` to line, and no other rule writes a colour value. The charts read theirs from these too (Section 5). Tabler's own colours, such as the tinted backgrounds of its alerts, are overridden or left unused, so every colour drawn on a screen is one of the table's.
- **Readable.** Text and its background have a contrast of at least 4.5:1, and a border or chart colour that carries meaning at least 3:1 against white (WCAG 2.1 AA). So the brand's green `#76A64C` and orange `#F58220`, at 2.9:1 and 2.6:1 on white, are never used for text, a control's border or a chart, and white text never sits on them, though oqgn.om does both. The logo's line is decoration, so it can be light green. On surface-alt, green-text and ink-subtle fall to 4.1:1 and 4.3:1, so text and links on a band are ink, and no table row changes colour under the pointer, which would put its green links on a band.
- **Meaning never rests on colour alone.** An error, a refusal or what blocks approval says so in words, a badge and a notice say their state in words, the screen open now is also marked with `aria-current` and in bold, and Outside IDS is named in its chart's labels and legend (Section 5). A link is underlined only under the pointer, as Tabler draws it: its green-text has 4.4:1 against the ink of the words around it, above the 3:1 WCAG asks of a link told apart by its colour.
- **Focus.** Every link, button and field focused from the keyboard shows a 2px solid ring in green-text, 2px outside it. The guide asks for the brand's green, but that is under 3:1 on white.
- **Square corners.** Nothing has rounded corners, as on oqgn.om, whose buttons, labels, cards and menus are all square. `app.css` sets Tabler's corner radii to 0.
- **The logo.** The official file, `oqgn-logo.svg` (188 by 47), is kept unchanged as `static/brand/oqgn-logo.svg`. Every page shows it at the top of the sidebar, or of the page's panel when there's no sidebar, linking to the home page, at most 155px wide, with "OQ Gas Networks" as its text alternative and clear space around it at least the height of its "O". It's never redrawn, recoloured or rebuilt in CSS. It holds its texture as embedded images, so it's shown as an image file, never written into a page.
- **Type.** Text is set in "Helvetica Neue", Arial, sans-serif, the guide's fallbacks for OQGN's font, Aktiv Grotesk, which the application has no licence for. No font file is kept or loaded, and never the trial files oqgn.om loads. Sizes are Tabler's: body and table text 14px, a card's title 16px, and the page's heading 20px, all headings bold. A label that names a group or a figure, such as Report and Administration in the sidebar or a tile's name, is 12px capitals, spaced apart, in ink-subtle, as Tabler's `subheader`.
- **Icons.** Each sidebar link, and each link or button that starts an action, has a small line icon before its words, in the colour of its words. They're Tabler Icons (ENG-01, 3), drawn to go with Tabler. Only the icons the application uses are copied, unchanged, as symbols in one file, `static/icons/icons.svg`, with the notice their MIT licence asks for, so nothing is loaded from outside. They're hidden from screen readers, since the words say the same.
- **The sidebar.** Every page of a signed-in user has a sidebar at its start, Tabler's vertical navbar, 16rem wide, on white, with a line at its end. From the top: the logo with its line; Reports; on a report's pages the label Report, the report's name, and its screens in their two groups (Navigation, above); for fixed administrators, the label Administration with Users, Roles and Whole audit log; and at the bottom the signed-in user, as their full name over their username, with Change password and Sign out. The application's name isn't written there, since the logo, the browser's tab and the sign-in page carry it. The screen open now, a detail screen counting as its list, carries `aria-current="page"` and shows in bold on surface-alt, with a 4px green-text bar at its start. The administration home is reached from the crumbs of each administration page, so nothing in the sidebar is marked on it.
- **Pages without the sidebar.** The sign-in page, and an error page shown to someone not signed in, have no sidebar. One card in the middle of the page, with a line around it, holds the logo, the application's name, and the form or the message.
- **The page header.** Each page starts with Tabler's page header: its crumbs, its heading and its actions, with a line under them. The crumbs link the pages above it, such as Reports / IDS spend 2026 / GL accounts on a GL account's screen, so every page under a report names the report, on screen and on paper. The heading is the page's one `h1`. The actions are links that start something, at the end of the heading's row: New report, Upload a file, New user and New role.
- **Buttons.** There are three kinds, Tabler's buttons, each as wide as its words and never stretched to fill a space. A main button, `btn btn-primary`, green-dark with white words, does what its form is for: Sign in, Upload and read, Approve, Save and the rest. A secondary button, `btn btn-outline-primary`, white with green-dark words and border, closes or removes something beside a main action: Reject, Withdraw, Disable and Remove. A quiet button, a plain `btn`, white with ink words and an ink-subtle border, is for Show, the filter form's button for when JavaScript is off, and Sign out. A link that starts an action looks like the kind of button its action would be: New report, Upload a file, Pick the columns, New user and New role like a main button, and Export to Excel and Roll back upload like a secondary one.
- **Badges, notices and messages.** An upload's state shows as Tabler's badge: its words in ink on surface-alt, with a 4px bar at the start in the colour its state has in the table above. A notice tells of something waiting, such as an upload awaiting approval. It's Tabler's alert, with its tinted background replaced: its words in ink on white, with a line around them and an orange-text bar at the start. A message after an action, an error and a refusal are alerts too, with a green-text or danger bar, and an icon.
- **Tiles and cards.** The overview's tiles, an upload's summary, the report list's cards and the administration home's links are Tabler's cards: white boxes with a line around them, holding a label or a name, a figure in large bold ink where there is one, and a line of detail. A list of facts, such as who uploaded a file and when, or an account's state, is Tabler's datagrid: each fact's name as a label over its value.
- **Denser tables.** Tables are Tabler's, with their text 14px and their cells padded 4px by 8px, the first and last of a row too, so a list of a few dozen GL accounts fits on one screen. Their headings are 12px capitals in ink on surface-alt. Numbers keep their tabular figures, right-aligned. A total row is bold, with a 2px line of ink above it. On a screen at least 62rem wide, Tabler's large size, a table on a report's screen, and the audit log, scrolls inside a box no taller than the window, so on a long list its headings stay at the top of the box and its total row at the bottom. Source rows keep each value on one line, except the item text, which wraps at 16rem or wider. A list with no rows says "None." in ink-subtle, in a box with a line around it.
- **The overview as a dashboard.** The *Actual spend* table shows as tiles across the top of the overview: Actual spend, On IDS cost centers, and Outside IDS while it's included, each with its amount, linked to its source rows like any amount, and its lines. The tiles are the same table drawn another way, so the export's *Actual spend* sheet doesn't change. On a screen at least 75rem wide, the *Largest GL accounts* and *By month* charts each sit beside their table, and narrower, above it, as on every other screen.
- **Narrow screens.** Below 62rem wide, the sidebar becomes a bar across the top of the page: the logo, the user's name, Change password and Sign out in one row, then every link in one row that scrolls sideways, with the report's name before its screens and without the icons and group labels. The bar is at most 10rem tall, and it has no menu to open: Tabler's narrow navbar folds its links into a menu that needs Tabler's JavaScript, so the bar is drawn by `app.css` instead and needs none. The page itself never scrolls sideways.
- **Printing.** A printed page leaves out the sidebar, the toolbar, every button and form, and the pager, and keeps the crumbs, the heading, the filter line, the as-of line and the figures, so the paper names the report, the filters and the upload. Tables print whole, out of their boxes, with their headings repeated on each sheet, and links print as plain text.
- **Left out.** No Font Awesome, oqgn.om's icons (ENG-01, 3), and no Tabler JavaScript, since no screen needs a menu, a dialog or a tooltip that opens (ENG-01, 3, Optional components). No shadows, gradients or animation: `app.css` turns off Tabler's shadows and transitions. A radio button stays round, as every radio button is, so it isn't taken for a checkbox. The guide also asks for every screen in Arabic, but the screen language stays English (Section 12). `app.css` sets margins and padding by start and end, such as `margin-inline-start`, rather than left and right, and Tabler has a right-to-left build of its stylesheet, so Arabic screens wouldn't need a new layout.

**Tested by**

- The access matrix test (ENG-01, 6.2) covers every address in the table and every export address.
- A Viewer gets 403 on the preview of an upload awaiting approval, and can open the preview of a committed one.
- A fixed administrator creates a report, uploads a file, picks a missing column, and approves, rejects, withdraws or rolls back an upload, each from its screen. Each refusal shows its message and changes nothing.
- Opening Uploads marks `failed` an upload received more than 10 minutes ago whose read never finished, and leaves one received 5 minutes ago.
- The preview screen shows every figure saved in the upload's preview, and each exception type's items.
- The rollback confirmation shows the figure the rollback then gives.
- Every list's total row equals the figure it breaks down, as the rules above say.
- The same screen lists its rows in the same order every time.
- No template holds a `style` attribute or a `<style>` block, and no page loads a file from outside the application.
- Every colour in `static/css/app.css` is a custom property in `:root`, each holds its colour from the theme's table, and every Tabler colour variable it sets names one of them.
- `static/css/tabler-1.6.1.min.css` is Tabler 1.6.1's stylesheet, checked by its SHA-256, and loads nothing from outside. It's copied from the npm package after checking the package against the registry's published hash, with only its last line, a comment naming a source map that isn't kept, removed, since collecting the static files would look for that map.
- In the browser, on each kind of page, every colour drawn, as text, a background, a border or an outline, is one from the theme's table; all text has a contrast of at least 4.5:1 with the background it's drawn on; and the controls', problems' and charts' colours have at least 3:1 against white.
- In the browser, on each kind of page, nothing but a radio button has a rounded corner, and nothing casts a shadow, draws a gradient or moves. `app.css` sets no margin or padding by left or right.
- `static/brand/oqgn-logo.svg` is the official file, checked by its SHA-256, and every page shows it, linking home, with its text alternative.
- In the browser, the logo is drawn, and a link, a button and a field focused from the keyboard each show the ring, not Tabler's own.
- In the browser, a message after an action and a refused sign-in draw only the theme's colours, with readable text.
- In the browser, every cell of a report's tables and of an upload's preview, the first and last of a row too, is padded 4px by 8px, and each total row has its 2px ink line above it.
- Every page of a signed-in user has the sidebar, with no application name in it, the screen open now marked with `aria-current="page"`, a detail screen marking its list, and the Administration group only for fixed administrators. The sign-in page has no sidebar.
- Every icon a page uses is in `static/icons/icons.svg`, which draws only in the colour of the words and carries Tabler Icons' licence notice, and every icon is hidden from screen readers.
- Only Show and Sign out are quiet buttons, and only Reject, Withdraw, Disable and Remove are secondary ones. Every button, and every link drawn as one, has one of the three kinds' classes, and the theme gives each kind its colours.
- Each page's crumbs link the pages above it, and every page under a report names the report there.
- A report's Uploads link says "Waiting" while the report has an open upload, and not once it's decided.
- The report list shows each report's actual spend, linking to source rows that add up to it, the upload its figures are as of, and its open upload, linked only for a user who may open it.
- The as-of line links its upload, and the notice of an upload awaiting approval links it only for a user with **upload** or **approve**.
- Uploads says "Waiting for your approval" only to a user who may approve the upload, and "Waiting for you to pick columns" only to a user who may pick them.
- An upload's summary shows its saved preview's difference, the report's actual spend before and after, its rows and its exception types, and marks a tile that blocks approval. It comes before the preview, which comes before the actions, and links to the actions only for a user who may take one.
- An upload's state shows as a badge in words, with the bar its state has.
- In the browser, no button is stretched past its words, a long table's headings stay in view while it scrolls, and below 62rem the bar across the top is at most 10rem tall and the page doesn't scroll sideways.
- A printed page has no sidebar, toolbar, button or form, and its tables aren't in a box.
- The overview shows its *Actual spend* as tiles, each amount linking to source rows that add up to it, and its export still has the *Actual spend* sheet.
- An HTMX request for a screen gets its panel, holding the filter line and an Export link with the new filters, and a request without HTMX, or HTMX's request for a page after going back, gets the whole page.
- `/reports/<id>/` opens the overview, and the base template links the report's screens on each of them.
- For the sample file, each screen shows the figures in its expected result, and each amount links to source rows with the keys and filters of its group.
- A report with no committed upload shows "No approved uploads yet" on every screen, with a link to Uploads only for a user with **upload**.
- A report's Audit log lists only the events that name it, newest first, with no sign-ins and none of another report's, and the whole log lists every event, for fixed administrators only.
- The Audit log shows each event's time to the second in Muscat time, its user as "Full name (username)", "Command line", "Unknown username" or "The application", its action in words, its upload, and its details escaped.
- The Audit log comes 100 events to a page, refuses a wrong address with 400 and a page past the last with 404, and has no form or export.
- The report's navigation links the Audit log for an Approver, and not for a Viewer.

## 5. Charts

A chart is a picture of a table on the same screen, never a source of figures on its own. Charts are drawn by ECharts, which is kept in the repository (ENG-01, 3), so they work with no internet access.

| Chart | Screen | Shows | Answers |
| --- | --- | --- | --- |
| Spend by GL account | Overview | Horizontal bars, highest first: the 10 largest GL accounts, and one bar for all the others | Q2 |
| Running total | Overview, GL account, Department | A line of actual spend added up month by month | Q7 |
| Spend by department | Departments | Horizontal bars, highest first, with Outside IDS as its own bar | Q5 |
| Spend by month | GL account, Department | One column per month | Q7 |

**Rules**

- **The table is the record.** Every value a chart draws is also in a table on the same screen.
- **Same query, same panel.** Chart data arrives inside the same HTMX panel as its table, from the same function (ENG-01, 5.2). The browser never calculates a figure.
- **Labels come from the server.** Each bar and point carries its amount already formatted (Section 2.4), and the tooltip shows that text. ECharts gets numbers only to draw lengths and positions.
- **Nothing hidden.** Bars start at zero, and a negative total, from credits, draws to the left of zero.
- **Clickable.** Clicking a bar or point opens its source rows (Section 6). A running-total point opens every month up to it, with `through`, and the bar for all the others opens the GL accounts screen (6.2).
- **Outside IDS stands out.** It has its own colour wherever it appears, and the legend names it.
- **Built from the table.** `reports/screens.py` builds each chart's data from the rows of the table it pictures, so the two can't differ: for each bar or point its label, its name for the tooltip, its amount as text for drawing, its amount formatted for the tooltip, its link, and whether it's Outside IDS.
- **Data, never code.** The data reaches the page through Django's `json_script`, and the application's own `static/js/charts.js` reads it with `JSON.parse`. No chart's data is written into a script or an HTML string. Names come from uploaded mapping sheets, so the tooltip, which ECharts writes as HTML, escapes every text with `echarts.format.encodeHTML`, and a name such as `<img src=x onerror=...>` shows as written (ENG-01, 6.4).
- **Drawn after every swap.** `charts.js` draws the charts of the page when it loads and of each panel HTMX swaps in, through `htmx.onLoad`.
- **Colours from the stylesheet.** A chart takes its colours from custom properties in `static/css/app.css`, so OQGN's theme changes them with the rest of the look (Section 4).
- **One pinned copy.** ECharts 5.6.0 is kept as `static/js/echarts-5.6.0.min.js`, copied from the npm package after checking it against the registry's published hash. Only pages with a chart load it.

**Tested by**

- For each chart, the values it receives equal the table beside it.
- A GL account named `<img src=x onerror=alert(1)>` shows that text in its chart's data and table, escaped, and in the browser the tooltip shows it as text and runs nothing.
- In the browser, clicking a bar or a running-total point opens source rows that add up to its amount, except the bar for all the others.
- With the network cut off, the overview still draws its charts.
- A GL account whose credits exceed its spend draws to the left of zero.

## 6. Drill-down to source rows

Every amount opens the postings behind it. This is how the plan's Traceable principle is met: any figure can be followed to the file, sheet and row it came from.

### 6.1 The address

The source rows screen takes the filters (Section 3) plus the keys of the group that was clicked. For example, `/reports/7/rows?gl=610100&cc=4510&year=2026`.

| Key | Matches | Special value |
| --- | --- | --- |
| `gl` | A GL account |  |
| `ci` | A budget line | `none`: No budget line |
| `cc` | A cost center |  |
| `dept` | A department | `outside`: Outside IDS. `none`: No department |
| `month` | A month, as `2026-03` |  |
| `through` | Every month up to and including this one, as `2026-03`, for a running total |  |
| `upload` | The postings of one committed upload that count, by its number |  |
| `page` | Which page of 100 rows to show, from `1` |  |

A code is given as cleaned (ENG-02, 5), and `upload` and `page` as whole numbers from `1`. Any other key, a value in the wrong form, a key given twice, or `month` and `through` together returns 400 Bad Request. A page past the last returns 404 Not Found.

The detail screens' addresses take the same values: `gl/<code>`, `departments/<code>`, `departments/outside`, `departments/none`, `budget-lines/<code>` and `budget-lines/none`. A code in the right form with no actual spend under the filters opens its screen with "No actual spend for these filters.", since it may have spend in another year.

### 6.2 What it shows

| Column | From |
| --- | --- |
| Posting date, GL account, cost center, value type, amount, item text, document number | The posting (ENG-02, 2) |
| Budget line, department | The mapping in force (ENG-02, 8.1) |
| Upload | Its number and original file name |
| Sheet, row | The posting's place in the original file |

**Rules**

- Above the rows, the screen says in words which keys chose them, such as "GL account 610100, cost center 4510", and shows their count and total. The total covers every page, and equals the figure that was clicked.
- Rows are shown 100 to a page, sorted by posting date, then upload number, then sheet and row number.
- The bar and the table row for "all the others" on the overview open the GL accounts screen instead, since they cover many accounts.
- Item text is shown with its direction set automatically, so Arabic reads right to left.

**Tested by**

- For every amount on the overview, GL accounts and departments screens of the sample file, except the "all the others" row, the source rows add up to that amount.
- A row's sheet and row number point to the same values in the original file.
- `ci=none` lists exactly the postings for `640200` and `650200`.
- `dept=outside` lists exactly the postings for `3100` and `3200`, and `dept=none` exactly those of the cost centers inside IDS with no department code.
- An Arabic item text displays right to left, unchanged.
- `gl=0610100`, `month=2026-3`, `page=0`, and `month` with `through` each return 400, and a page past the last returns 404.

## 7. FAQ pages

There's one page per agreed question. Each page shows, in this order: the question, a one-sentence answer with the figure in it, the table behind the answer, a line saying exactly what was counted, and how to check the answer by hand.

### 7.1 Where they're defined

The list is fixed in code, in `reports/faq.py`, since the client confirmed that a fixed FAQ is enough (the plan, Phase 3). Each entry has:

| Field | Holds |
| --- | --- |
| `slug` | The page's address, such as `faq/total-spend` |
| `counted` | The line saying exactly what was counted |
| `question` | The question, as agreed with the client |
| `query` | The function that builds the page's tables, from the functions in `reports/queries.py` and through the screens' own table builders (Section 4), so an FAQ table and its screen's table are one |
| `answer` | The sentence pattern, filled from the table's own figures |
| `check` | How to check the answer by hand |

The client can ask for more questions as the need comes up. Adding or changing one is a code change, with its own test, and a question can be answered only from the postings and mappings the import keeps (ENG-02, 2).

`/reports/<id>/faq` lists the questions in order, each linking to its page. It's an index, so it has no export. Each page shows the filter line and the as-of line like any screen, then the question, the answer, its tables, what was counted, and the check by hand. The pages' slugs are, in the plan's order, `total-spend`, `largest-gl-accounts`, `gl-budget-lines`, `gl-account-cost-centers`, `department-spend`, `department-gl-accounts`, `spend-over-time` and `unmatched-spend`.

### 7.2 The questions

These are the plan's eight questions, in its order. They came from the client.

| # | Question | Answer, for example | Table on the page | How to check it by hand, in the original export |
| --- | --- | --- | --- | --- |
| 1 | How much has IDS spent in total? | "Actual spend is 1,250,400.000 OMR across 412 lines: 1,246,500.000 OMR on IDS cost centers and 3,900.000 OMR outside IDS." With outside IDS excluded: "Actual spend on IDS cost centers is 1,246,500.000 OMR across 405 lines. Cost centers outside IDS are excluded." | Total, IDS and outside IDS | Keep only actual-spend rows, then add up the amount column |
| 2 | Which GL accounts has IDS spent the most on? | "The largest is Software licenses (610100), with 182,300.500 OMR, 14.6% of the total shown." | GL accounts, highest first | A pivot table of amount by GL account, sorted largest first |
| 3 | Which budget line does each GL account belong to? | "12 GL accounts map to 6 budget lines, and 2 have no budget line." | Each GL account with its budget line, one row for each budget line it's counted under | Look up each GL account in the GL mapping sheet |
| 4 | Which cost centers and departments used a GL account? | "Software licenses (610100) was used by 6 cost centers in 4 departments, and by 1 cost center outside IDS." | Cost center, department and spend, for one GL account | Filter on the GL account, then pivot amount by cost center |
| 5 | How much has each department spent? | "The largest is Applications (APP), with 402,100.000 OMR." | Departments, with No department and Outside IDS as rows of their own | Add each row's department from the cost center mapping sheet, then pivot by department |
| 6 | What has a department spent its money on? | "Applications (APP) spent most on Software licenses (610100): 96,200.000 OMR." | GL accounts and spend, for one department | Filter on the department's cost centers, then pivot by GL account |
| 7 | How has spending built up over time? | "Spending reached 1,250,400.000 OMR by the end of September 2026." | Month, spend in the month, and running total | Pivot amount by posting month, and add a running total |
| 8 | What couldn't be matched to a budget line or an IDS department? | "12,400.000 OMR has no budget line, and 3,900.000 OMR is outside IDS. 870.000 OMR is in both, so 15,430.000 OMR couldn't be matched in all." | The three lists from the exceptions screen: GL accounts with no budget line, cost centers outside IDS, and cost centers inside IDS with no department | Add up the GL accounts missing from the GL mapping or listed there without a commitment item. Then, separately, add up the cost centers missing from the cost center mapping, and those listed there without a department code. Add up the rows in both once, and take them from the sum of the two |

Each hand check leaves out the rows its upload's preview lists as skipped (ENG-02, 11.2), since a skipped row is in no figure (ENG-02, 9.3). SAP's own totals include them, so any skipped amount is a difference the validator checks, as the plan's open question on rows that can't be read says.

The figures in the answers above are only examples of the sentence pattern. A part of a sentence with nothing to say is left out:

- Q1 leaves out the outside IDS part when it's excluded, as shown.
- Q2 leaves out the share when there isn't one (2.2).
- Q3 adds "*k* GL accounts have more than one budget line." when `own_upload` gives some several (2.3), and leaves out "and 0 have no budget line".
- Q4 leaves out the outside IDS part when no cost center outside IDS used the account.
- Q8 adds "*x* OMR is on IDS cost centers with no department." when there is any, and leaves out the "in both" part when nothing is in both.

Q3 counts GL accounts and budget lines among the table's rows: a budget line once however many GL accounts share it, and No budget line as none. Q4 counts the table's cost centers inside IDS, the different departments among them, and its cost centers outside IDS. Q8's "in both" is the actual spend with no budget line that is also outside IDS or in no department, and "in all" is the actual spend in any of the three lists, counted once. PostgreSQL adds both, as every figure (Section 10).

Pages 4 and 6 have a picker for the GL account or department. The choice goes in the address, as `gl` or `dept`, and the largest one is shown first. Page 6 offers every row of the Departments screen, No department and Outside IDS included, with the same keys as 6.1. A value in the wrong form returns 400, and a code in the right form with no actual spend shows "No actual spend for these filters."

**Rules**

- An answer sentence is filled from its table's figures, never typed.
- With no data, the page says "No approved uploads yet", never a zero that reads like a finding. With data that the filters don't match, it says "No actual spend for these filters." (3.1).
- Each page shows the filter line and the as-of line, and exports like any other view.
- The hand checks are written for a report built from one file. Every amount check counts only actual-spend rows, so the total row and subtotals stay out (ENG-02, 5). It also leaves out the skipped rows, since they're never in the report (ENG-02, 9.3). Each upload's preview, linked from the exceptions screen (Section 8), lists them by sheet and row (ENG-02, 11).
- With several files, check against the latest file for each month in replace-by-period mode (ENG-02, 7.3). In line-key mode, check against every committed file, counting each line once, with its amount from the first committed file that has it. Budget lines and departments come from the mapping in force (ENG-02, 8.1). By default, the GL mapping and the cost center mapping each come from the latest committed file that has one.

**Tested by**

- For the sample file, every answer equals the expected result.
- Each figure in an answer sentence equals the figure it comes from in its table. For Q1, that's the total, its lines, and the IDS and outside IDS rows, which add up to the total. For Q2, Q5 and Q6, it's the top row. For Q3 and Q4, it's a count of rows, or of the different budget lines or departments in them. For Q7, it's the last running total, and for Q8, each list's total, with "in all" equal to the lists' totals less "in both".
- Each part a sentence leaves out is left out only when the rule above says, for example Q2's share when the total shown is zero.
- Every entry in `faq.py` has a page, an export and a test of its own.

## 8. Exceptions screen

The exceptions screen shows what's wrong or unmatched in the report as it stands now, across its committed uploads (ENG-02, 10.2). The preview's exceptions (ENG-02, 11) cover one upload before approval, and this screen covers the report after it.

| List | Shows | Built from |
| --- | --- | --- |
| GL accounts with no budget line | Code, name if known, actual spend and lines, split into missing from the GL mapping and listed without a commitment item | `posting_resolved` |
| Cost centers outside IDS | Code, actual spend, lines, and the GL accounts they used | `posting_resolved` |
| Cost centers with no department | Each cost center inside IDS whose mapping row gives no department code: code, name, actual spend and lines | `posting_resolved` |
| Budget-line changes between files | Each GL account whose commitment item differs between the GL mappings of committed uploads: each committed upload whose mapping lists it, the item it gave, and that upload's actual spend on the account that still counts. That's `0.000` for an upload whose months a later file replaced, which is still listed, since its mapping is part of the report's history | Mapping rows of the committed uploads (ENG-02, 8.3), and `posting_resolved` for the spend |
| Department changes between files | The same for each cost center whose department differs | Mapping rows of the committed uploads, and `posting_resolved` |
| Skipped rows | For each committed upload: the number and amount skipped, by reason. A year-to-date file can skip a row that an earlier file skipped too, and each is listed under its own upload | `skipped_row` (ENG-02, 5) |

A committed upload is one at `committed`, never a rolled-back one (ENG-02, 10.2). One whose postings a later file replaced still counts as committed, so a change between files stays listed until one of the two uploads is rolled back.

**Rules**

- Every amount links to its source rows, with the `upload` key for an upload's spend, or for skipped rows, to the upload's preview.
- The year filter applies to the first three lists, and to the spend shown in the changes between files. The skipped rows and the changes themselves have no posting date, so they're listed for every committed upload, whatever the year. The screen says so.
- The outside IDS filter doesn't apply on this screen or on the question 8 FAQ page, since excluding outside IDS would empty its own list. The screen and the page say so.
- Links to source rows from this screen and the question 8 FAQ page always use `exclude_outside=0`, so the rows add up to the amount clicked (6.2).
- A list with nothing in it says "None", so an empty list can't be mistaken for one that failed to load.
- The plan's question 8 is answered from the first three lists.

**Tested by**

- In the sample file, `640200` shows as missing from the GL mapping, `650200` as listed without a commitment item, and `3100` and `3200` as outside IDS, each with its expected amount. The list of cost centers with no department says "None".
- A cost center listed in the mapping with no department code shows in its own list, and in no other.
- A second file that moves `610100` to another budget line appears in the budget-line changes, with both uploads. After a rollback, it's gone.
- A second year-to-date file that replaces every month of the first, and moves `610100`, lists the first upload in the change with `0.000` still counting.
- The skipped rows for each upload match its preview.
- With `exclude_outside=1`, the `3100` amount on the exceptions screen opens source rows that add up to it.

## 9. Excel export

An export holds exactly what its screen shows: the same filters, rows, order and totals, but every row rather than one page. Exports are written with XlsxWriter.

### 9.1 What can be exported

Every screen in Section 4 except Uploads and the Audit log: the overview, the list and detail screens, the source rows, the exceptions screen and every FAQ page. The FAQ's list of questions is an index, not a page, so it has none (7.1). The address is the screen's own address with `/export` added, so it carries the same filters, keys and pick, and takes the same values as the screen, refused the same way (3.1). Source rows' export holds every row, so it doesn't take `page`. The Export link on each screen is built from the address on screen, with an FAQ page's pick written out even when it's the default, so the Source sheet names it.

### 9.2 The workbook

| Sheet | Holds |
| --- | --- |
| One data sheet per table on the screen, in the screen's order | A frozen header row, one row per line on screen, and a total row at the bottom. Each sheet has a fixed name of at most 31 characters, such as *GL accounts*, *By month* or *Skipped rows*, never one built from a code, a name or a question. A chart isn't exported: its table is |
| Source, last | The report, the view with its address, the filters in words, the keys of source rows in words, and the as-of line, with the line for an upload awaiting approval. Every committed upload (ENG-02, 10.2), in the order of commits: its number, file name, SHA-256, rows read, who approved it and when, its ruleset version, and the lines and actual spend it still counts under the export's filters. An upload whose postings a later file replaced is listed with `0` lines, since its mapping may still be in force. Who exported it, as "Full name (username)" (ENG-01, 6.1), and when, in Muscat time, and the app's version tag (ENG-01, 7.1). An FAQ export adds the question and the answer sentence |

An export of a report with no committed upload, or of filters that match nothing, still has every sheet: each data sheet holds its header and total rows only, and the Source sheet's filters and as-of line say why.

### 9.3 Cells

| Content | Written as | Excel format |
| --- | --- | --- |
| Amounts | Numbers, converted from `Decimal` | `#,##0.000;-#,##0.000` |
| Totals | Values the app calculated, not formulas, so they match the screen | The same, in bold |
| Codes and document numbers | Text, so a long number doesn't turn into scientific notation | Text |
| Dates | Excel dates | `dd.mm.yyyy` |
| Shares | Numbers, rounded as on screen, such as `0.124`. An empty cell where there's no share (2.2) | `0.0%` |
| Months | Text, as on screen, such as `Mar 2026` | Text |
| Names and item text | Text, exactly as written |  |
| Lines, rows and upload numbers | Whole numbers | `0` |
| A skipped row's amount, kept as read (Section 8) | A number | `#,##0.000###;-#,##0.000###` |

Excel holds 15 significant digits, so amounts below 1,000,000,000,000 OMR keep every baisa.

### 9.4 Safety and limits

- **No formulas from data.** XlsxWriter's `strings_to_formulas`, `strings_to_urls` and `strings_to_numbers` options are off, so text such as `=HYPERLINK(...)` stays text (ENG-01, 6.4).
- **Large exports.** XlsxWriter's `constant_memory` mode writes rows as they come, so memory use stays flat.
- **Row limit.** An export of more than 1,048,574 data rows on a sheet, Excel's limit once the header and total rows are counted, is refused before anything is written. The page says how many rows it has and asks to narrow the filters, and no event is written, since nothing was downloaded.
- **File name.** `<report>_<view>_<yyyymmdd-hhmm>.xlsx` in Muscat time, where *view* is the screen's address under the report with `/` written as `-`, such as `gl-610100` or `faq-total-spend`. Anything other than the letters A to Z and a to z, digits, hyphens and underscores is removed, and a report name with nothing left is written as `report` and its number.
- **Written, then sent.** The workbook is written to a temporary file, which the response then sends and deletes.
- **Permission and audit.** The export address needs **view** and **export**, and every export writes one `export_downloaded` event once the workbook is written (ENG-01, 6.3).

**Tested by**

- Every export, read back with openpyxl, has the same rows, order and totals as its screen.
- The Source sheet lists the filters and every committed upload, and the lines and actual spend it gives add up to the overview's under the same filters.
- A cell holding `=1+1` opens as text.
- A 10-digit document number opens as text, unchanged.
- Each export writes exactly one `export_downloaded` event.
- A user without **export**, or without **view**, gets 403 from the export address.
- An export with more rows than the limit is refused with its message, writes no event, and its file isn't sent. The test lowers the limit, since a million rows would take minutes.
- The file name is the report's name, cleaned, the view and the time, and a report named only in Arabic gets `report` and its number.
- The Source sheet shows the app's version tag, and the user as "Full name (username)".
- The Export link appears only for a user with **export**, and carries the filters on screen.

## 10. Speed

The plan's default is files of up to tens of thousands of rows. The speed test below uses 200,000 postings, well past that, and all the adding happens in the database.

**Rules**

- **Totals in SQL.** Every figure is a `SUM` or `COUNT` in PostgreSQL. Python never loops over postings to add them.
- **A fixed number of queries.** Each screen runs the same number of queries, however many rows the report has.
- **Pages.** Source rows are fetched 100 at a time (Section 6).

**Indexes**

| Table | Index | Serves |
| --- | --- | --- |
| `posting` | `(report_id, posting_date)` | The year filter, months and the running total |
| `posting` | `(report_id, gl_code)` | GL account screens and drill-down |
| `posting` | `(report_id, cost_center_code)` | Department screens and drill-down |
| `posting` | `(upload_id)` | Deciding which postings count (ENG-02, 10.2) |
| `gl_ci_map` | `(upload_id, gl_code)`, unique (ENG-02, 2) | The GL mapping in force |
| `cc_dept_map` | `(upload_id, cc_code)`, unique (ENG-02, 2) | The cost center mapping in force |

`upload` has no index for finding a report's committed uploads in order. A report has a few uploads a month, and PostgreSQL reads so small a table whole, so an index there would never be used. At 200,000 postings, the speed test sees PostgreSQL use each of the three `posting` indexes above for its own drill-down: by GL account, by cost center and by month.

**How `posting_resolved` stays fast.** Every screen reads the view, so its form decides most of a screen's time. A later migration of the view (ENG-02, 8.4) returns exactly the same rows as the first, in a form PostgreSQL can work out in one pass over a report's postings:

- **Replacement by date.** A posting is replaced when an upload committed after its own covers its month (ENG-02, 10.2). The view compares the posting's date with the first and last day of the months the later upload covers, rather than working out a month for every posting.
- **One condition.** Line-key mode, which replaces nothing, is decided where the pairs of uploads are listed, not beside the replacement check, so that check stands alone and PostgreSQL can make it a single anti-join. Those pairs are worked out once per query.
- **One row per report.** The mapping rule and the uploads whose mappings the latest rule takes are joined to each posting as one row, not three.

The first form took about 0.3 seconds a query over 200,000 postings, and the overview about 2.2 seconds. This form takes about 0.1 seconds a query.

**The speed test's data.** `tests/speed_data.py` makes a report of 10 committed uploads, each covering a month of its own, with 45 GL accounts and 30 cost centers, mapped with gaps so every catch-all and exception list has rows. Its postings go in with `COPY`. The 100-posting report used for counting queries has the same uploads, codes and mappings. After loading, the test runs `ANALYZE`, which PostgreSQL's autovacuum does by itself after a large insert, since a query planned without statistics can take ten times longer.

**Measured on the developer's machine,** with 200,000 postings: every screen answered in 0.2 to 1.2 seconds, and an export of every source row, 196,000 of them, took about 20 seconds. At that rate an export at Excel's limit takes about 2 minutes, inside `GUNICORN_TIMEOUT`'s 5 minutes (ENG-01, 7.1), so exports run inside the request and Excel's limit stays the export's limit (9.4). Its memory stayed at about 7 MB however many rows it wrote.

**If it's still too slow.** `posting_resolved` becomes a materialized view, refreshed inside the commit and rollback transactions (ENG-02, 10), so it's never out of date. This is done only if the speed test fails. The refresh runs as `ids_app`, and PostgreSQL 17 lets only the owner or a role with the `MAINTAIN` privilege refresh a materialized view, so `sql/grants.sql` then grants `MAINTAIN` on it to `ids_app`.

**Tested by**

- With 200,000 generated postings over 10 uploads, every screen responds within 2 seconds on the developer's machine.
- The number of queries per screen is the same with 100 postings as with 200,000.
- An export of the 200,000 postings' source rows runs at a rate that would write Excel's limit within `GUNICORN_TIMEOUT`.
- An export of about 20,000 rows holds no more memory than one of about 4,000, give or take 5 MB.
- The view's own tests (ENG-02, 8.4) pass on the later form unchanged, and so does the sum rule (Section 11).
- At 200,000 postings, a drill-down by GL account, by cost center and by month each uses its own `posting` index.

## 11. Testing

Each section above ends with the tests that prove it. This section is how they're organised. It follows ENG-02, Section 12, and uses the same golden files.

| Kind | Covers | Tool |
| --- | --- | --- |
| Query | Each function in `reports/queries.py`, against the golden files | pytest-django |
| Sum rule | Groups add up to their total, and every drill-down adds up to the figure clicked, for random postings and filters | pytest-django, Hypothesis |
| Export | Each export read back and compared with its screen | pytest, openpyxl |
| Access | The matrix test from ENG-01, 6.2, extended to every address in Section 4, export addresses included | pytest-django |
| Browser | Overview to GL account to source rows, both filters, and an export download | Playwright |
| Speed | Section 10 | pytest |

**Expected results.** Each golden file from ENG-02 gains the expected figures for every screen and FAQ answer. For the sample file, they come from the script that generated it. For the real export, they're SAP's own totals during the build, and the validator's hand-checked figures once acceptance starts. Both are client data, kept beside the export in `real-export/` and never committed (ENG-02, 12.2).

**Done when** the plan's Phase 3 gate is met: every FAQ answer for the sample file, and for the real export once it arrives, is on screen, each total equals the sum of its source rows, and the Excel export matches the screen. All the tests above pass with `uv run pytest`.

## 12. Open decisions and To confirm

**Open decisions**

Until each is decided, the default is what gets built.

| Decision | Options | Default | Section |
| --- | --- | --- | --- |
| Year filter's default | All years, or the latest year | All years, since the goal is cumulative spend | 3.1 |
| Outside IDS default | Included and flagged, or excluded | Included and flagged, as the plan's scope says | 3.1 |
| Overview GL chart | Every GL account, or the 10 largest and one bar for the rest | The 10 largest and one bar for the rest, since the GL accounts screen lists them all | 5 |
| Screen language | English, or English and Arabic | English, with names and item text shown as written | 4 |

**To confirm**

| # | Assumption | Section | Confirmed by | If it's different |
| --- | --- | --- | --- | --- |
| 1 | Year means the calendar year of the posting date | 3.1 | The client by Day 2, with ENG-02's item 8 on the fiscal year | Filter by fiscal year instead |
| 2 | The hand checks match how the validator checks figures in SAP | 7.2 | The validator, at acceptance | Rewrite those checks |
| 3 | Amounts are shown in OMR with 3 decimal places | 2.4 | The validator, at acceptance | Change the format, which is set in one place |
