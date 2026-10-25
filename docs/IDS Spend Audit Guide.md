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
- Everyone uses the same machine, so **sign out when you finish**, with **Sign out** at the bottom of the sidebar on every page, and **never let the browser save your password**. After you sign out, the browser's Back button shows the sign-in page, not your reports.
- If you forget your password, ask an administrator to reset it. You then choose a new one at your next sign-in.
- Everything you do is recorded in the audit log under your name.

### Your reports

**Reports**, in the sidebar at the start of every page, lists the reports you can use. You see only the reports an administrator gave you a role on, and you can do only what that role allows on each (see "Roles" below).

Each report has these screens, linked in the sidebar on its pages:

| Screen | Shows |
| --- | --- |
| Overview | Total actual spend, inside and outside IDS, the largest GL accounts, and spend by month |
| GL accounts | Every GL account with its budget line, spend, share and cost centers |
| Departments | Every IDS department's spend, with No department and Outside IDS as rows of their own |
| Budget lines | Every budget line (commitment item) with its GL accounts and spend |
| Exceptions | GL accounts with no budget line, cost centers outside IDS, IDS cost centers with no department, budget-line and department changes between files, and rows skipped while reading |
| FAQ | The eight questions in the plan, each answered on its own page |
| Uploads | Every file uploaded to the report, and its state |
| Audit log | Who did what on this report, and when. Only for users whose role holds View audit log |

On every screen:

- **Year** shows one year's postings, or all years. **Outside IDS** includes or excludes cost centers outside IDS. The line under the form says which filters are on.
- **The as-of line** says which upload the figures come from, and who approved it. It also says when another upload is waiting for approval and isn't counted yet.
- **Every amount is a link** to the rows of the SAP export behind it, with the file, sheet and row each came from.
- **Export to Excel** downloads what's on screen, with a Source sheet naming the filters and the uploads used. It's there only if your role holds Export, and every export is recorded in the audit log.

### Uploading a file

If your role holds Upload:

1. Open the report's **Uploads**, then **Upload a file**.
2. Choose the SAP export: an Excel file with its GL and cost center mapping sheets, or a CSV of spend lines. If the file has no total row, type SAP's total for it.
3. **Upload and read** reads the file and opens its preview: rows read and skipped, new rows, duplicates, exceptions, and whether the file's rows add up to its control total.
4. If a column couldn't be found, **Pick the columns** shows the sheet's first rows, and you choose the column for each field.
5. The upload then waits for an approver. It doesn't count in the report until it's approved. Until then you can withdraw it, with a reason.

A report has one open upload at a time. A file already approved in the report is refused if it's sent again.

### Approving, rejecting and rolling back

If your role holds Approve:

- Open an upload awaiting approval from **Uploads** and read its preview. **Approve upload** adds it to the report. **Reject upload** closes it, with a reason, and changes nothing.
- Approval is refused while something blocks it, such as a control total that doesn't match. The preview names what blocks it, and the refused attempt is kept in the audit log.
- On a report's first approval, you also choose how later files replace earlier ones: by the period they cover, or by line item where the file allows it. This can't change later.

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

Administrators are fixed accounts, created from the command line. They see every report, hold every permission on each, and open **Administration** from the sidebar on every page.

### Users

**Administration**, then **Users**, lists every account: whether it's a fixed administrator, disabled, locked out or waiting for its first password, and how many reports it has a role on.

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

**Administration**, then **Roles**, shows each role's permissions and how many times it's given. **New role** makes one, and a role's page changes its name or permissions, which applies to everyone who has it at their next click. Every role holds View. A role still given to anyone can't be removed: change their access first.

### The whole audit log

**Administration**, then **Audit log**, lists every event, newest first, sign-ins and account changes included. Nobody can change or delete an event.

### Fixed administrators, from the command line

Each command runs in a terminal in the repository folder, as `docker compose exec app python manage.py <command>`. Each one that changes something is recorded in the audit log as a command-line action.

| Command | Does |
| --- | --- |
| `create_admin <username>` | Creates a fixed administrator. It asks for the full name and an initial password, which they change at first sign-in |
| `list_admins` | Lists every fixed administrator, and whether each is active |
| `disable_admin <username>` | Disables a fixed administrator |
| `changepassword <username>` | Resets a fixed administrator's forgotten password to a temporary one |
| `axes_reset_username <username>` | Unlocks a locked-out fixed administrator |
| `show_ruleset <report>` | Prints the report's ruleset: the settings its files are read with, such as the header names it recognises |
| `change_ruleset <report> --by <username>` | Makes a new ruleset version from the changes it reads as JSON, run in Git Bash as `docker compose exec -T app python manage.py change_ruleset "<report>" --by <username> < change.json`, or in PowerShell, which has no `<`, as `Get-Content change.json | docker compose exec -T app python manage.py change_ruleset "<report>" --by <username>`. A field of header names given in the file replaces that field's whole list, so copy its current names from `show_ruleset` and add the new one. Used with the validator when the real export needs a setting changed (ENG-02, 3 and 13) |

## Running the application

ENG-01, Section 7, has every step and setting. The ones needed every day:

- **Start:** `docker compose up -d`. **Stop:** `docker compose stop`.
- **Back up** at the end of every working day while OQGN's data is on the machine, and before every update: `scripts/backup.sh`, in Git Bash. It writes a dated folder in `backups/`. Copy it to the encrypted drive, keep the last 7, and delete older ones (the plan's open question on copies of OQGN's data).
- **Restore:** `scripts/restore.sh backups/<folder>`. It first backs up what it replaces, then reports whether every total and every file matches the backup. A restore brings back the backup's audit log too, and records itself in it.
- **Update:** back up, then follow ENG-01, 7.3.
- **When something goes wrong:** ENG-01, 7.4 lists the usual problems and their fixes.
