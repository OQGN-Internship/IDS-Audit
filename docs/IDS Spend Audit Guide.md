# IDS Spend Audit Guide

A short guide for the people who use IDS Spend Audit and the people who run it. What the application does, and why, is in [IDS Spend Audit Delivery Plan](IDS%20Spend%20Audit%20Delivery%20Plan.md). How it's built and run in detail is in the engineering documents, which this guide names where they help.

## For everyone

### Signing in

1. Open `http://localhost:8000` in the browser on the machine that runs the application.
2. Sign in with the username and the temporary password your administrator gave you.
3. The first time, the application asks you to choose your own password: at least 12 characters, not all digits, not a common password, and not like your username or name. It can't be the temporary one.

Things to know:

- After 5 wrong passwords, your account is locked for 15 minutes. Wait, or ask an administrator to unlock it.
- After 30 minutes without using the application, you're signed out.
- Everyone uses the same machine, so **sign out when you finish**, with **Sign out** under your name at the bottom of the sidebar on every page, or at the top of the page on a narrow screen, and **never let the browser save your password**. After you sign out, the browser's Back button shows the sign-in page, not your reports.
- **Change password**, under your name, changes your password whenever you want.
- If you forget your password, ask an administrator to reset it. You then choose a new one at your next sign-in.
- Everything you do is recorded in the audit log under your name.

### Your reports

**Reports**, at the top of the sidebar on every page, lists the reports you can use, each with its actual spend, the upload its figures are as of, and any upload waiting for approval. You see only the reports an administrator gave you a role on, and you can do only what that role allows on each (see "Roles" below).

Each report has these screens, linked in the sidebar on its pages under the report's name:

| Screen | Shows |
| --- | --- |
| Overview | Total actual spend, inside and outside IDS, the largest GL accounts, and spend by month |
| GL accounts | Every GL account with its budget line, spend, share and cost centers |
| Departments | Every IDS department's spend, with No department and Outside IDS as rows of their own |
| Budget lines | Every budget line (commitment item) with its GL accounts and spend |
| Exceptions | GL accounts with no budget line, cost centers outside IDS, IDS cost centers with no department, budget-line and department changes between files, and rows skipped while reading |
| FAQ | The eight questions in the plan, each answered on its own page |
| Uploads | Every file uploaded to the report, and its state. It says **Waiting** in the sidebar while an upload awaits approval or needs its columns picked |
| Rules | How the report's files are read and what counts, explained, with every earlier version |
| Audit log | Who did what on this report, and when, with a search and filters to narrow it. Only for users whose role holds View audit log |

On every screen:

- **Year** shows one year's postings, or all years. **Outside IDS** includes or excludes cost centers outside IDS. The line under the filters says which are on.
- **The as-of line** says which upload the figures come from, and who approved it, with a link to that upload. When another upload is waiting for approval and isn't counted yet, a notice says so, with a link to it if your role lets you open it.
- **Every amount is a link** to the rows of the SAP export behind it, with the file, sheet and row each came from.
- **Export to Excel**, at the end of the filters, downloads what's on screen, with a Source sheet naming the filters and the uploads used. It's there only if your role holds Export, and every export is recorded in the audit log.
- **Printing** a screen from the browser leaves out the sidebar, the filters and the buttons, and keeps the report's name, the filter and as-of lines, and every figure.

### A report's rules

**Rules**, in the sidebar after Uploads, shows how the report reads its files and what it counts. Everyone who can open the report sees them, so before you send a file you can check it will read. Each rule is a card saying what it decides, what it's set to, an example, and the default:

| Rule | Decides |
| --- | --- |
| Header names | Which column headers the report recognises for each field, such as GL account or Amount, and on which sheets each is needed |
| Date order | Whether a date such as `03.04.2026` is 3 April or 4 March |
| Decimal separator | Whether an amount written as text, such as `12,400.500`, uses a point or a comma for its decimals |
| Currency | The currency every amount must be in. A row in another is skipped |
| Actual spend | Whether every row counts as actual spend, or only rows whose value type is listed as actual spend |
| Mapping rule | Which mapping gives a line its budget line and department when the mapping changes between files |
| Outside IDS | Which cost centers are outside IDS: those missing from the cost center mapping |

Every file is read with the rules in force when it's received, and keeps them, so its upload's page says which version it was read with, linked to that version. A change makes a new version, which applies only to files received after it. The page's history lists every version, who made it, when, and what it changed.

**When a column isn't found,** the upload says Needs columns. Picking the column reads that one file. If the export always uses that header, ask an administrator to add it to the field's header names in the rules, so every later file reads on its own.

### Uploading a file

If your role holds Upload:

1. Open the report's **Uploads**, then **Upload a file**.
2. Choose the SAP export: an Excel file, with or without its GL and cost center mapping sheets, or a CSV of spend lines. A file without mapping sheets uses the report's latest approved mapping. [IDS Spend Audit Workbook](IDS%20Spend%20Audit%20Workbook.md) gives the best shape for it. If the file has no total row, type SAP's total for it.
3. **Upload and read** reads the file and opens its preview: rows read and skipped, new rows, duplicates, exceptions, and whether the file's rows add up to its control total.
4. If a column couldn't be found, the upload says **Needs columns**, and Uploads says **Waiting for you to pick columns**. **Pick the columns** shows the sheet's first rows, and you choose the column for each field.
5. The upload then waits for an approver. It doesn't count in the report until it's approved. Until then you can withdraw it, with a reason.

A report has one open upload at a time. A file already approved in the report is refused if it's sent again.

### Uploading a mapping

When the validator gives new GL or cost center mapping sheets, send them on their own, if your role holds Upload:

1. Open the report's **Uploads**, then **Upload a mapping**.
2. Choose an Excel file holding the GL mapping sheet, the cost center mapping sheet, or both, and no spend lines.
3. **Upload and read** opens its preview: which budget lines and departments change, and the spend each change moves, spend already approved included.

It's approved, rejected, withdrawn and rolled back as a file is. Once approved, every line in the report takes the new mapping, in earlier months too. A mapping sheet replaces the whole mapping, so a GL account it leaves out has no budget line, and a cost center it leaves out is outside IDS.

### Approving, rejecting and rolling back

If your role holds Approve:

- Open an upload awaiting approval from **Uploads**, where it says **Waiting for your approval**. Its summary at the top shows the difference from the control total, the report's actual spend before and after, the rows to commit and the exceptions, and marks anything that blocks approval. Read the preview under it. The actions come last: **Approve upload** adds it to the report. **Reject upload** closes it, with a reason, and changes nothing.
- Approval is refused while something blocks it, such as a control total that doesn't match. The preview names what blocks it, and the refused attempt is kept in the audit log.
- On a report's first approval of a spend file, you also choose how later files replace earlier ones: by the period they cover, or by line item where the file allows it. This can't change later.

If your role holds Roll back, **Roll back upload** on the newest approved upload removes it from the report's figures, with a reason. The confirmation shows the report's actual spend now and once rolled back. Uploads are rolled back newest first, and nothing is ever deleted: a rolled-back upload stays in the list and the audit log.

### Roles

An administrator gives you one role on each report you may use. The three preset roles are:

| Role | Can |
| --- | --- |
| Viewer | See the report's screens and FAQ pages, and export to Excel |
| Uploader | The same, and upload files, see their preview and withdraw them |
| Approver | See and export, approve or reject uploads, roll back, and see the audit log |

Administrators can change these and make others. The plan's "Users, sign-in and access" lists the six permissions a role is made of.

## For administrators

Administrators are fixed accounts, created from the command line. They see every report, hold every permission on each, and find **Users**, **Roles** and **Whole audit log** under **Administration** in the sidebar on every page.

### Users

**Users** lists every account: whether it's a fixed administrator, disabled, locked out or waiting for its first password, and how many reports it has a role on.

- **New user:** a username, the person's full name as they write it, their email if any, and a temporary password twice. Give them the temporary password in person. They choose their own at their first sign-in. Usernames can't be changed later.
- **On a user's page:**
  - **Report access** lists every report. Choose the user's role on each, or No access, and press **Save** on that row.
  - **Details** corrects their full name or email.
  - **Reset password** sets a new temporary password. Any session they have ends, and they choose their own at their next sign-in.
  - **Disable** stops the account from signing in, at once. Its history stays in the audit log. **Enable** lets it sign in again. Accounts are never deleted.
  - **Unlock** appears while the account is locked out, and lets it sign in again at once.
- **Fixed administrators** are changed only from the command line, so their page has no forms.

Before the client names the first users, the plan's default is one demo account for each preset role, used only for the demo and disabled before real use.

### Roles

**Roles** shows each role's permissions and how many times it's given. **New role** makes one, and a role's page changes its name or permissions, which applies to everyone who has it at their next click. Every role holds View. A role still given to anyone can't be removed: change their access first.

### Changing a report's rules

On a report's **Rules**, each rule you can change has **Change** on its card, and each field of the header names has its own **Change**.

- **Before a report's first upload,** open its export and check it against the rules: the headers against the header names, how it writes dates, the decimal mark in a text file, which rows are actual spend, and above all the mapping rule, which can't change once the report has an upload open or approved. A new report opens on its rules for this.
- **To change a rule,** choose its new value on the form, then **Check the change**. The next page shows the rule now and after, in words, and when the change applies. Nothing is saved until you press **Save as version** *n*. A refused change says why, and nothing is saved.
- **Header names** change one at a time: type a header in **Add a header name**, or press **Remove** beside one. Capitals, spaces and punctuation don't matter, but the whole header must match, and no header may be under two fields.
- **What can't change.** Outside IDS has one rule until the client gives another. The mapping rule is fixed once the report has an open or approved upload. To read old files with new rules, create a new report and upload them again.

Every change is recorded in the audit log, and the history says it was made on the page.

### The whole audit log

**Whole audit log** lists every event, newest first, sign-ins and account changes included. Nobody can change or delete an event.

To find events, type words in **Search**, such as a username, a file name or a word from the details, or choose an action, who did it, the dates from and to, or a report (No report for sign-ins and account changes), then **Search**. **Clear** shows the whole log again. A report's Audit log has the same search, without the report.

### Fixed administrators, from the command line

Each command runs in a terminal in the repository folder, as `docker compose exec app python manage.py <command>`. [command.md](command.md) explains every `manage.py` command, which to use and which never to. Each one that changes something is recorded in the audit log as a command-line action.

| Command | Does |
| --- | --- |
| `create_admin <username>` | Creates a fixed administrator. It asks for the full name and an initial password, which they change at first sign-in |
| `list_admins` | Lists every fixed administrator, and whether each is active |
| `disable_admin <username>` | Disables a fixed administrator |
| `changepassword <username>` | Resets a fixed administrator's forgotten password to a temporary one |
| `axes_reset_username <username>` | Unlocks a locked-out fixed administrator |
| `show_ruleset <report>` | Prints the report's ruleset: the settings its files are read with, such as the header names it recognises, as JSON. The Rules page shows the same in words |
| `change_ruleset <report> --by <username>` | Makes a new ruleset version from the changes it reads as JSON, run in Git Bash, as the backup scripts are, as `docker compose exec -T app python manage.py change_ruleset "<report>" --by <username> < change.json`. PowerShell has no `<`, and Windows PowerShell passes piped text to a program as ASCII, which would change a header name with a letter outside English. A field of header names given in the file replaces that field's whole list, so copy its current names from `show_ruleset` and add the new one. The Rules page is the usual way to change a rule. The command is for a change written as JSON, such as several header names at once (ENG-02, 3 and 13) |

## Running the application

ENG-01, Section 7, has every step and setting. The ones needed every day:

- **Start:** `docker compose up -d`. **Stop:** `docker compose stop`.
- **Back up** at the end of every working day while OQGN's data is on the machine, and before every update: `scripts/backup.sh`, in Git Bash. It writes a dated folder in `backups/`. Copy it to the encrypted drive, keep the last 7, and delete older ones (the plan's open question on copies of OQGN's data).
- **Restore:** `scripts/restore.sh backups/<folder>`. It first backs up what it replaces, then reports whether every total and every file matches the backup. A restore brings back the backup's audit log too, and records itself in it.
- **Update:** back up, then follow ENG-01, 7.3.
- **When something goes wrong:** ENG-01, 7.4 lists the usual problems and their fixes.
