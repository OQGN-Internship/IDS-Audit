# ENG-01 Architecture, Security and Operations

Implements: Phases 1, 2 and 4 of [IDS Spend Audit Delivery Plan](../IDS%20Spend%20Audit%20Delivery%20Plan.md).

## 1. Purpose and scope

This document describes how IDS Spend Audit is built, secured and run on the developer's machine. It covers the components, technology choices, code structure, request flow, security and operations.

The delivery plan stays the single source of decisions. Where a section implements one of the plan's rules, it names that rule instead of repeating it.

Two things are covered elsewhere:

- **ENG-02 Data model and import:** the tables, file reading, cleaning rules, duplicates, reconciliation and rollback.
- **ENG-03 Reports, FAQ and exports:** what each screen shows, the queries behind the FAQ answers, and the Excel export format.

## 2. System overview

The whole system runs on the developer's machine as two containers, the application and the database, started together by Docker Compose. Users open it in a browser on the same machine. Inside its container the application listens on port 8000, and Docker publishes that port on the machine's 127.0.0.1 only, so other machines can't reach it. Docker publishes the database's port on 127.0.0.1 only as well, so the developer's tests and `manage.py` can reach it from the same machine (7.5).

![Everything runs on one machine, reachable only from it](../images/eng01-system-overview.png)

The browser reaches the application only through 127.0.0.1. The application is the only component that talks to the database and the file store.

| Component | Job | Runs as |
| --- | --- | --- |
| Browser | Shows the pages. HTMX swaps parts of a page in place, and ECharts draws the charts | The user's browser at `http://localhost:8000` |
| Application | Django, served by Gunicorn. Handles sign-in, uploads, checks, preview, approval, reports and exports | The `app` container |
| Database | PostgreSQL. Holds reports, uploads, postings, mappings, users, roles, report access and the audit log | The `db` container, with its data in the `pgdata` volume |
| File store | Every uploaded file, saved unchanged under its SHA-256 hash | The `uploads` volume, mounted into the `app` container |
| Migration job | Applies database changes using the owner role, then exits | A one-off `migrate` container that runs before `app` starts |

## 3. Technology choices

Every choice favours exact arithmetic, few moving parts and long support. Exact versions of every library are pinned in `uv.lock`. JavaScript libraries are copied into the repository instead of loaded from the internet.

| Layer | Choice | Version | Why | Considered instead |
| --- | --- | --- | --- | --- |
| Language | Python | 3.12 | Mature Excel libraries, and exact decimal arithmetic built in | Node.js: no built-in decimal type, so amounts risk float errors |
| Web framework | Django | 5.2 LTS | Built-in sign-in, admin screens, forms and migrations, with security fixes until April 2028 | FastAPI: sign-in, admin and migrations would have to be assembled |
| Application server | Gunicorn | Pinned | Runs several Django processes and replaces any that crash | Django's development server, which isn't meant for real use |
| Static files | WhiteNoise | Pinned | Serves CSS and JavaScript from Django, with no separate web server | Nginx, which is added only with a final host |
| Database | PostgreSQL | 17 | Exact `NUMERIC` amounts, transactions, views, `COPY` for bulk loading, and role permissions | SQLite: weak with concurrent writes, and no role permissions |
| Database driver | psycopg | 3 | Supports `COPY` from Python | psycopg2, the older driver |
| Page updates | HTMX | 2, in the repository | The server renders HTML and HTMX swaps parts of the page, so there is no second front-end codebase | React, which needs a separate app and an API |
| Small behaviours | Alpine.js | 3, in the repository | Dropdowns and "click again to confirm" without custom JavaScript | Plain JavaScript |
| Charts | Apache ECharts | Pinned, in the repository | Fast with long series, with built-in tooltips | Chart.js, which is weaker with large data |
| Excel reading | python-calamine, with openpyxl as a fallback | Pinned | calamine is fast. openpyxl handles unusual files | pandas, which turns amounts into floats by default |
| Excel writing | XlsxWriter | Pinned | Cell formats, frozen headers, and a memory-saving mode for large exports | openpyxl, which is slower for large exports |
| Sign-in lock-out | django-axes | Pinned | Counts failed sign-ins and locks accounts, configured by settings | A hand-written counter |
| Password hashing | Argon2, through argon2-cffi | Pinned | Django's recommended hasher | PBKDF2, Django's default |
| Settings file | python-dotenv | Pinned | Django reads `.env` itself when it runs outside Docker, so `manage.py`, pytest and the editor's test button all see the same settings (7.5) | uv's `--env-file` option, which every command would have to repeat, and which stops with an error when `.env` is missing |
| Packaging | Docker Compose | Current | One command starts the application and database the same way on any machine | Installing Python and PostgreSQL by hand |
| Development tools | uv, ruff, pytest, pytest-django, Hypothesis, Playwright | Pinned | Locked dependencies, linting, and unit, property and browser tests | None |

**Optional components.** Each is added only when its trigger happens.

| Component | Added when | Tied to |
| --- | --- | --- |
| Background worker (Procrastinate, which uses PostgreSQL as its queue) | Files are large enough that users would wait too long during import | The plan's open question on rows per file |
| Nginx with HTTPS | The application moves off the developer's machine | The plan's open question on where it runs after sign-off |

## 4. Code structure

The repository is `IDS-Audit`, and the application code lives in the Python package `ids_audit`. Python names can't contain hyphens.

```text
IDS-Audit/
├── config/            Django settings (base, dev, test, production), URLs, WSGI entry point
├── ids_audit/
│   ├── pipeline/      plain Python: reading files, cleaning rows, reconciliation, duplicate rules
│   ├── accounts/      users, roles, report access, sign-in rules, the access check
│   ├── imports/       uploads, file storage, staging, preview, approve, reject, rollback
│   ├── ledger/        postings, mapping versions, the resolved view
│   ├── reports/       reports, report screens, FAQ queries, chart data, Excel exports
│   ├── audit/         the append-only audit log
│   ├── templates/     pages and the partial templates HTMX swaps in
│   └── static/        CSS, and the HTMX, Alpine.js and ECharts files
├── sql/               database roles and grants
├── tests/             unit, golden-file, permission and browser tests
│   └── fixtures/      the generated sample files, their script and expected results (ENG-02, 12.3)
├── scripts/           backup.sh and restore.sh
├── docs/              the plan, engineering/, CONTRIBUTING.md, and the guide for users and administrators
├── backups/           backups made by scripts/backup.sh (7.2). Git ignores everything here except its README
├── real-export/       the client's real SAP export and SAP's totals for it (ENG-02, 12.2). Git ignores everything here except its README
├── Dockerfile
├── compose.yaml
├── .env               the real settings and passwords (7.1). Git ignores it
└── .env.example       every setting the application needs, with no real values
```

**Boundary rules**

1. **`pipeline/` imports nothing from Django.** It takes rows in and returns clean rows, skipped rows and check results. The riskiest logic is tested without a database.
2. **Only `imports/` writes postings,** and only inside the single transaction that runs at approval (see 5.1).
3. **Every view that touches a report calls the one access check in `accounts/`** (see 6.2). No view decides access on its own.
4. **`audit/` exposes one function, `record()`,** which every other part calls. Nothing else writes to the audit log.
5. **Amounts are `Decimal` in Python and `NUMERIC(18,3)` in PostgreSQL.** They never pass through a float.

## 5. How requests flow

### 5.1 An upload, from file to committed rows

1. The browser sends the file to `imports/`. Django checks that the user has Upload on the chosen report, that the file is within the size limit, and that its first bytes match an accepted format (see 6.4).
2. The application computes the file's SHA-256 hash. A file whose hash matches a committed upload in the same report is refused here, before an upload exists (ENG-02, 7.1). So is any file sent while the report has an open upload (ENG-02, 6). Otherwise the application saves the file to the uploads volume under its hash and creates the upload at `reading`.
3. `pipeline/` reads the sheets, finds the header rows and columns, and cleans every row. It returns clean rows, skipped rows with their reasons, and the file's mapping sheets. If a required column isn't found, the upload stops at `needs_columns` until the uploader picks it. If the file can't be read, it ends at `failed` (ENG-02, 6).
4. In one transaction, the clean rows are bulk-loaded with `COPY` into a staging table, and the skipped rows and the file's mapping rows are saved, all tagged with the upload's ID. Duplicate detection and reconciliation run against the staged rows, and the upload moves to `awaiting_approval`.
5. The preview shows rows read, rows skipped, new rows, duplicates, exceptions and the reconciliation result. ENG-02 defines each figure.
6. A user with Approve on the report approves. One database transaction then:
   1. copies the staging rows not marked `is_duplicate` into postings
   2. checks their count and total against the preview
   3. marks the upload `committed`
   4. writes the audit event

   ENG-02, 10.1, gives every step. If any step fails, the whole transaction is undone and the report doesn't change.
7. If the user rejects instead, the upload becomes `rejected`, its staging rows are deleted, and the audit log records the rejection. The original file stays in the file store.

Steps 3 and 4 run inside the web request. If files turn out to be too large for that, they move to the background worker and the page checks the upload's status every few seconds (see Section 8).

### 5.2 A report view, from filter click to page

1. The user changes a filter. HTMX sends a request with the filters in the address, for example `/reports/7/overview?year=2026&exclude_outside=1`.
2. Django checks that the user has View on report 7.
3. It runs aggregate SQL over that report's resolved view (ENG-02), with those filters. PostgreSQL adds the amounts as exact decimals.
4. Django renders only the changed panel. HTMX swaps it into the page and updates the address, so the view can be bookmarked or shared.
5. Chart figures arrive inside the panel as data, and ECharts draws them. The browser never calculates a figure.

## 6. Security

### 6.1 Authentication

**Implements:** the plan's "Users, sign-in and access", the Sign-in and Administrators parts.

The application uses a custom user model from its first migration, as Django recommends. Besides Django's standard fields, it adds two flags: `is_fixed_admin` and `must_change_password`.

| Rule in the plan | How it is implemented |
| --- | --- |
| Own accounts, created only by administrators | There is no sign-up page. Accounts are created in the administration screens at `/admin/`, which only fixed administrators can open |
| Username and password, stored hashed | Django sign-in, with Argon2 as the first entry in `PASSWORD_HASHERS` |
| Passwords of at least 12 characters | Django's password validators: minimum length 12, plus the common-password, all-numeric and similar-to-username checks |
| New users change their temporary password at first sign-in | Creating a user requires a temporary password and turns on `must_change_password`. Until the user changes it, every page redirects to the change-password page |
| Reset by an administrator, with a new password chosen at next sign-in | A reset sets a new temporary password and turns `must_change_password` on again |
| Locked for 15 minutes after 5 failed attempts | django-axes, with `AXES_FAILURE_LIMIT = LOCKOUT_ATTEMPTS`, `AXES_COOLOFF_TIME = timedelta(minutes=LOCKOUT_MINUTES)`, `AXES_LOCKOUT_PARAMETERS = ["username"]` and `AXES_RESET_ON_SUCCESS = True`. None of these is django-axes' default. Lock-out is by username because every user signs in from the same machine, so a lock-out by address would lock everyone. A successful sign-in resets the count |
| Session ends after 30 minutes without activity, or at sign-out | `SESSION_COOKIE_AGE = SESSION_IDLE_MINUTES * 60`, and `SESSION_SAVE_EVERY_REQUEST = True`, so each request restarts the 30 minutes. Session cookies are HttpOnly and SameSite=Lax |
| Disabling keeps the history | Disabling sets `is_active = False`. Users are never deleted, so their audit history stays linked |
| Sign-ins and failures are logged | Django's `user_logged_in`, `user_logged_out` and `user_login_failed` signals each write an audit event |

**Redirects in HTMX requests.** A panel request that has to go to the sign-in page, after a time-out, or to the change-password page gets an `HX-Redirect` header instead of a redirect. HTMX then moves the whole page, so neither page lands inside a panel.

**Fixed administrator accounts**

- **Created from the command line:** `manage.py create_admin <username>` asks twice for an initial password, without showing it, and checks it against the 12-character rule. It creates the account as a fixed administrator with `must_change_password` on, so the administrator chooses their own password at first sign-in. The audit log records the creation as a command-line action.
- **No passwords in files:** no administrator username or password is kept in `.env`, or anywhere else outside the database.
- **Removing one:** `manage.py disable_admin <username>` disables the account. Its history stays in the audit log.
- **Checking who they are:** `manage.py list_admins` shows every fixed administrator, and whether the account is active.
- **No changes from inside the application:** it refuses to delete, disable or remove rights from a fixed administrator, both in the screens and in the model's save and delete logic.
- **Recovery:** a fixed administrator's forgotten password is reset only from the command line, with `manage.py changepassword <username>`. A locked administrator is unlocked with `manage.py axes_reset_username <username>`.
- **Who can run these commands:** only someone with shell access to the developer's machine. That's the same boundary that protects `.env` and the database.

**Tested by**

- Five wrong passwords lock the account. With the clock moved forward 15 minutes, it signs in again.
- Four wrong passwords, a right one, then four more wrong ones don't lock the account.
- Five wrong passwords for one username don't stop another user signing in.
- With the clock moved forward 31 minutes of inactivity, the user is signed out, and an HTMX request gets an `HX-Redirect` to the sign-in page.
- An 11-character password is refused, including by `create_admin`.
- A new user, and a user whose password was reset, can't reach any page except the change-password page until they change it. An HTMX request from them gets an `HX-Redirect` to the change-password page.
- `create_admin` creates a fixed administrator who must change the initial password at first sign-in, and refuses a username that already exists.
- After `disable_admin`, that administrator can't sign in, and `list_admins` shows the account as disabled.
- A fixed administrator can't be deleted or disabled from inside the application.

### 6.2 Authorization

**Implements:** the plan's "Users, sign-in and access", the Permissions and Roles and report access parts.

The six permissions are fixed in code: `view`, `export`, `upload`, `approve`, `roll_back` and `view_audit_log`. Each one applies to a single report. Django's built-in permissions apply to a whole table, not one report, so they aren't used for this.

| Table | Holds | Rules |
| --- | --- | --- |
| `Role` | A name and a set of the six permissions | Viewer, Uploader and Approver are created by a data migration. Administrators can edit them like any other role. A role still assigned to anyone can't be removed |
| `ReportAccess` | A user, a report and a role | At most one row per user and report. No row means no access |
| `User.can_create_reports` | An account-level flag | Off by default. Whether anyone besides administrators gets it is an open question in the plan |

**How the check is enforced**

1. Every view that touches a report calls one check, `require(user, report, *permissions, any_of=(), upload=None)`, through a decorator such as `@report_permission("approve")`. The user must hold every permission in `permissions`: an export needs `view` and `export` (ENG-03, 9.4). When a page needs one of several permissions, its view lists them in `any_of`, and the user must hold at least one. The preview of an upload that was never committed is checked this way (ENG-03, 4). The `upload` argument is only for rule 6. HTMX partial requests go through the same check as full pages.
2. A user with no access to a report gets **404 Not Found**, so the report's existence isn't revealed. A user with access but without the permission gets **403 Forbidden**.
3. For everyone except fixed administrators, report lists, search and exports are queried through `ReportAccess`, so other reports never appear.
4. Fixed administrators pass every check and see every report.
5. A fixed administrator can upload and approve the same file (rule 4). Anyone else can only if their role on that report holds both permissions, as the plan states. The audit log records who uploaded and who approved.
6. Picking columns for an upload and withdrawing it (ENG-02, 6) are open only to the user who uploaded it, who must still hold `upload` on the report. For these two actions the check also takes the upload, as in `require(user, report, "upload", upload=upload)`. Fixed administrators pass it too, so they can clear an upload its uploader left.

**Tested by**

- A matrix test calls every report address, export addresses included, as five users: no access, Viewer, Uploader, Approver and a fixed administrator. It checks each response is 404, 403 or 200 as expected.
- A user with access to report A gets 404 on every address of report B.
- A fixed administrator with no `ReportAccess` rows sees every report at `/reports/`.
- Picking columns for an upload, or withdrawing it, succeeds for its uploader and for a fixed administrator. Another user with `upload` gets 403, and so does the uploader once their role loses `upload`.
- A fixed administrator can approve an upload they made.
- Removing a role that is still assigned is refused.

### 6.3 Audit log

**Implements:** the plan's scope item "An audit log of every sign-in, upload, approval, rollback, export and access change".

Every event is one row in `audit_event`, written only through `audit.record()`.

| Field | Holds |
| --- | --- |
| `id` | A sequence number |
| `at` | The time in UTC, displayed in Muscat time |
| `user` | Who acted. A change made while a file is read records the user whose request started the read. Empty for a failed sign-in with an unknown username, or for a command-line action. Also empty for a change the application makes on its own, such as marking an interrupted upload `failed` |
| `action` | One of the actions listed below |
| `report` | The report concerned, if any |
| `upload` | The upload concerned, if any |
| `details` | Structured data such as the username tried, the role granted, the file hash, or the filters used in an export |

**Actions recorded:** `sign_in`, `sign_out`, `sign_in_failed`, `account_locked`, `user_created`, `user_disabled`, `password_reset`, `role_created`, `role_changed`, `role_removed`, `access_granted`, `access_changed`, `access_removed`, `report_created`, `ruleset_changed`, `upload_received`, `upload_needs_columns`, `upload_columns_picked`, `upload_awaiting_approval`, `upload_failed`, `upload_approved`, `upload_rejected`, `upload_withdrawn`, `upload_rolled_back`, `export_downloaded`.

The `upload_` actions match the state changes in ENG-02, Section 6, one action for each. `upload_received` is the upload being created at `reading`.

**Nobody can change or delete an event.** The database enforces this, not just the application, and it protects the other tables that are written once (ENG-02, 2) the same way:

- **`ids_owner`** owns the application database and its tables. The migration job connects as it, and so does Django on the developer's machine, for the tests and `manage.py` (7.5). It has `CREATEDB`, so pytest-django can create the test database. It's also a member of `ids_app`, so a grant test can switch to `ids_app` with `SET ROLE`.
- **`ids_app`** is the role the application connects as. Default privileges, set by `sql/init-roles.sh` before the first migration (7.1), give it `SELECT`, `INSERT`, `UPDATE` and `DELETE` on every table `ids_owner` creates. It has `TRUNCATE` on no table.
- **Insert-only tables:** on `audit_event`, `posting`, `gl_ci_map`, `cc_dept_map` and `skipped_row`, `ids_app` has only `SELECT` and `INSERT`.
- **`sql/grants.sql`** runs after every migration, and can run any number of times. It grants `ids_app` its rights on every existing table and sequence, then revokes `UPDATE` and `DELETE` on the insert-only tables, so a new migration can't reopen them. It also revokes `INSERT`, `UPDATE` and `DELETE` on every view, such as `posting_resolved` (ENG-02, 8.4). A simple view passes writes through to its table, with the rights of the view's owner, `ids_owner`.
- **The admin screens** show the audit log as read-only.

**Who can read it:** fixed administrators see every event. A user with View audit log on a report sees that report's events.

**Tested by**

- An `INSERT` as `ids_app` works on every insert-only table.
- An `UPDATE`, a `DELETE` and a `TRUNCATE` as `ids_app` each fail on every insert-only table.
- An `UPDATE` and a `DELETE` through `posting_resolved` as `ids_app` each fail, and `posting` is unchanged.
- Each action in the list writes exactly one event, with one test per action.
- A Viewer can't open the audit log.

### 6.4 Data protection

| Risk | Protection |
| --- | --- |
| Oversized or wrong files | A size limit, `MAX_UPLOAD_MB`, set to 50 by default. The file type is identified from its first bytes, not its name. ENG-02 lists the accepted formats |
| An uploaded file used against the application | Files are opened only by the Excel and CSV readers and are never executed. They are stored outside any folder the web server serves, under their hash, not their original name |
| Cross-site request forgery | Django's CSRF token is on every form. HTMX doesn't send it on its own, so the base template sets `hx-headers` on `<body>` with the token, and every HTMX request carries it |
| Cross-site scripting | Templates escape every value by default, and no user data is ever marked as safe |
| SQL injection | All queries go through the ORM or parameterised SQL. No SQL is built by joining strings |
| Clickjacking | Every response carries `X-Frame-Options: DENY` |
| Formula injection in exports | XlsxWriter's `strings_to_formulas` and `strings_to_urls` options are off, so text such as `=HYPERLINK(...)` stays text |
| Secrets in the repository | `SECRET_KEY` and the database passwords are kept in `.env`, which `.gitignore` excludes. `.env.example` holds only the names |
| Client data in Git | The real export, and anything taken from it, stays in `real-export/`. Backups stay in `backups/`. Git ignores both folders except the README in each, and `.dockerignore` keeps them and `.env` out of the application image |
| Client data in cloud-synced folders | The working copy holds `.env`, `real-export/` and `backups/`, so it must sit outside any cloud-synced folder such as OneDrive. `pgdata` and `uploads` stay Docker named volumes, never folders in the working copy |
| Access from other machines | The application's port and the database's port are published on 127.0.0.1 only |
| Client data on the developer's machine | Full-disk encryption must be on, because the machine holds the client's real exports. Backups go to encrypted storage (see 7.2) |
| Calls to outside services | None. The JavaScript libraries are in the repository, and nothing is sent out |
| Error pages exposing details | `DEBUG` is off, so errors never show code or settings |

**Tested by**

- An HTML file renamed to `.xlsx` is refused.
- A file over the limit is refused before it is read.
- A form sent without a CSRF token is refused.
- An HTMX request from a button outside a form succeeds with the token header, and is refused without it.
- A description containing `<script>` displays as plain text.
- An exported cell containing `=1+1` opens in Excel as text.
- Git tracks no file in `backups/` or `real-export/` except its README.
- Git ignores any new file in `backups/` or `real-export/`, including in a sub-folder, but not their READMEs.
- `manage.py check --deploy` reports only the HTTPS warnings, which don't apply while the application runs locally over plain HTTP.

## 7. Operations

### 7.1 Running the application

**Implements:** the plan's Phase 4 deliverable "the application packaged with Docker, starting on the developer's machine with one command".

**Prerequisites**

- Docker Engine with the Compose plugin, or Docker Desktop. Docker Desktop needs a paid subscription in larger organisations. Docker Engine on Linux doesn't. On Windows 11, the developer's machine uses Docker Desktop with its WSL2 backend.
- Git.
- A folder for the working copy outside any cloud-synced folder such as OneDrive (6.4).
- Enough disk space for the exports and their backups.

**Services in `compose.yaml`**

| Service | Image | What it does |
| --- | --- | --- |
| `db` | `postgres:17` | Stores its data in the `pgdata` volume. On first start, `sql/init-roles.sh` creates `ids_owner` and `ids_app` as 6.3 describes, creates the `DB_NAME` database owned by `ids_owner`, and sets the default privileges in 6.3. Compose publishes PostgreSQL as `127.0.0.1:${DB_PORT}:5432`, so only this machine can reach it (7.5). A health check (`pg_isready`) tells the other services when it's ready |
| `migrate` | The application image | Waits until `db` is healthy. Then it runs migrations as `ids_owner`, applies `sql/grants.sql`, and exits |
| `app` | The application image | Starts only after `migrate` finishes successfully. Runs Gunicorn on port 8000 of every interface inside the container, connects as `ids_app`, and mounts the `uploads` volume. Compose publishes the port as `127.0.0.1:${APP_PORT}:8000`, so only this machine can reach it |

**First-time setup**

```bash
git clone <repository-url> IDS-Audit   # outside any cloud-synced folder (6.4)
cd IDS-Audit
cp .env.example .env          # then fill in the settings below
docker compose up -d          # starts db, runs migrate, then starts app
docker compose exec app python manage.py create_admin <username>   # once per administrator
```

Then open `http://localhost:8000` and sign in as that administrator, with the initial password you entered. The application asks for a new password straight away.

**Every day after that**

- Start: `docker compose up -d`
- Stop: `docker compose stop`

**Settings in `.env`**

| Setting | What it does | Default |
| --- | --- | --- |
| `SECRET_KEY` | Signs sessions and CSRF tokens | None. Must be set to a long random value |
| `POSTGRES_PASSWORD` | The database superuser's password, used only inside the `db` container | None. Must be set |
| `DB_NAME` | The application database, created on first start | `ids_audit` |
| `DB_HOST` | Where Django on the developer's machine finds the database (7.5). `migrate` and `app` use `db` instead | `127.0.0.1` |
| `DB_PORT` | The database's port on the developer's machine. `migrate` and `app` use `5432` instead | `5432` |
| `DB_OWNER_PASSWORD` | Password for `ids_owner`. The `db` container sets it on first start. The migration job signs in with it, and so does Django on the developer's machine (7.5) | None. Must be set |
| `DB_APP_PASSWORD` | Password for `ids_app`. The `db` container sets it on first start, and the application signs in with it | None. Must be set |
| `ALLOWED_HOSTS` | The host names the application answers to | `localhost,127.0.0.1` |
| `APP_PORT` | The port on the developer's machine | `8000` |
| `MAX_UPLOAD_MB` | The largest file accepted | `50` |
| `SESSION_IDLE_MINUTES` | Minutes without activity before sign-out | `30` |
| `LOCKOUT_ATTEMPTS` | Failed sign-ins before an account locks | `5` |
| `LOCKOUT_MINUTES` | How long a lock-out lasts | `15` |
| `GUNICORN_WORKERS` | How many application processes run | `3` |
| `TIME_ZONE` | The time zone for displayed times | `Asia/Muscat` |
| `DEBUG` | Shows developer error pages | `False` |

The session and lock-out defaults are rules in the plan. Changing them changes what the client agreed to, so agree any change with the client first.

**Tested by**

- On a clean machine, the first-time steps reach a working sign-in page.
- `docker compose ps --all migrate` shows that `migrate` exited with code 0.
- A request from another machine on the same network can't reach the application or the database.

### 7.2 Backup and restore

**Implements:** the plan's Phase 4 deliverable "backup and restore of the database and the uploaded files", and its gate "a backup restores with the totals unchanged".

A backup always holds both the database and the uploads volume. Postings point to their source files by hash, so one without the other is incomplete.

**`scripts/backup.sh`** creates a folder `backups/YYYY-MM-DD_HHMM/` containing:

- **`db.dump`:** the database, from `pg_dump --format=custom`
- **`uploads.tar`:** the uploads volume
- **`totals.json`:** each report's overall total and GL totals, from `manage.py report_totals`
- **`SHA256SUMS`:** hashes of the three files above, used to detect damage

**When and where**

- Run a backup at the end of every working day while client data is on the machine, and before every update (see 7.3).
- Copy each backup folder off the machine to encrypted storage, and keep at least the last 7.
- Git ignores everything in `backups/` except its README (6.4).

**`scripts/restore.sh backups/<folder>`** runs these steps in order:

1. Checks the files against `SHA256SUMS`, and stops if any don't match.
2. Stops `app`.
3. Restores the database with `pg_restore --clean --if-exists`.
4. Restores the uploads volume.
5. Starts `app`.
6. Runs `manage.py report_totals` and compares the result with `totals.json`. It reports success only if every total matches.

**Tested by:** the Phase 4 gate. The latest backup is restored into an empty database and an empty volume, and `restore.sh` reports that every total matches.

### 7.3 Updating

Every release is a Git tag, such as `v1.0.0`.

1. Run `scripts/backup.sh`.
2. Get the new version: `git fetch --tags`, then `git checkout <tag>`.
3. Build it: `docker compose build`.
4. Start it: `docker compose up -d`. The migration job runs first, then `app` restarts.
5. Sign in, and open one report to check it.

**Rolling back:** migrations only move forward, so a rollback is a restore. Check out the previous tag, rebuild, and run `scripts/restore.sh` with the backup from step 1.

### 7.4 Logs and troubleshooting

- **Application log:** `docker compose logs -f app`. The application logs to standard output: the time, level, request path, user and duration.
- **Database log:** `docker compose logs db`.
- **What logs never contain:** passwords or file contents. The audit log is separate, and lives in the database.

Run `manage.py` commands as `docker compose exec app python manage.py <command>`.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| The page doesn't open | The containers aren't running | Check with `docker compose ps`, then run `docker compose up -d` |
| `app` never starts | `migrate` failed | Read `docker compose logs migrate`, fix the cause, then run `docker compose up -d` |
| "Bad Request (400)" | The address isn't in `ALLOWED_HOSTS` | Add it in `.env`, then run `docker compose up -d` |
| An upload is refused as too large | It's over `MAX_UPLOAD_MB` | Raise the limit in `.env` and restart, or export a shorter period |
| An upload is refused as an unknown type | The file isn't in a format ENG-02 accepts, such as a PDF renamed to `.xls` | Export again from SAP in an accepted format |
| A user is locked out | Five failed sign-ins | Wait 15 minutes, or run `axes_reset_username <username>` |
| A fixed administrator forgot their password | Only the command line can reset it | Run `changepassword <username>` |
| A restore reports a total that doesn't match | The backup is damaged or incomplete | Restore the backup before it, and keep the failing one for investigation |

### 7.5 Development and test environment

**Implements:** the plan's Phase 1 gate "the automated tests pass", which every later phase keeps. ENG-02, 12.1 needs those tests on a real PostgreSQL.

During the build, Python runs on the developer's machine and only the database runs in Docker. The editor, its test button and its debugger then use the project's own `.venv`.

**From Phase 1 (Day 2)**

- **The database:** `compose.yaml` starts with only the `db` service from 7.1. It publishes PostgreSQL on `127.0.0.1:${DB_PORT}`, so Django on this machine reaches it at `DB_HOST` and `DB_PORT`, and no other machine can.
- **Settings:** Django reads `.env` itself, through python-dotenv, when it runs outside Docker. A setting already in the environment wins, and a missing `.env` is not an error. `manage.py` uses `config.settings.dev`. pytest uses `config.settings.test`, named in `pyproject.toml`.
- **Role:** Django on this machine connects as `ids_owner`, for the tests, `migrate`, `makemigrations` and the shell (6.3). Nothing connects to the development database as `ids_app` during Phase 1, so it needs no grants yet.

**From Phase 2 (Day 3)**

As soon as there's a sign-in page, the `Dockerfile`, `config.settings.production`, and the `migrate` and `app` services from 7.1 are added. From then on, the application runs in Docker every day as `ids_app`, and `migrate` applies `sql/grants.sql` to the development database after each migration. Phase 4 completes the packaging and meets its gate.

**Tests and the database**

- Tests that don't use the database never connect, so they pass with Docker stopped. The database tests need `docker compose up -d db` first.
- pytest-django creates the test database `test_<DB_NAME>`, migrates it, and drops it at the end.
- Default privileges belong to one database, so they never reach the test database. A session fixture in `tests/conftest.py` applies `sql/grants.sql` to the test database once it's migrated, so the tests see the same grants as the application.
- A grant test switches to the application's role with `SET LOCAL ROLE ids_app` before the statement it checks, so the database refuses that statement as it would refuse the application. The switch ends with the test's transaction.
- Every test that a statement is refused has a matching test that an allowed statement works (6.3), so a refusal can't come from missing grants.

**First-time setup**, after cloning as in 7.1:

```bash
uv sync
cp .env.example .env              # then fill in the settings in 7.1
docker compose up -d db
uv run python manage.py migrate
uv run pytest
```

**Tested by**

- With Docker stopped, the tests that don't use the database pass.
- With `db` running, `uv run pytest` passes on a clean checkout.
- Removing one `REVOKE` from `sql/grants.sql` makes the matching grant test fail.

## 8. Open technical decisions

Each decision waits on an answer. Until it arrives, the default below is what gets built.

| Decision | Waits on | Default until then |
| --- | --- | --- |
| Add the background worker | The plan's open question on rows per file and files per month | No worker. Imports run inside the web request |
| Add Nginx and HTTPS | The plan's open question on where the application runs after sign-off | Not used. The port is published on 127.0.0.1 only |
| Where off-machine backups go | Same question | Encrypted external storage kept by the developer |
| Who can create reports, and what access the creator gets | The plan's open question on creating reports | Only fixed administrators. `can_create_reports` is off for everyone else |
| Which file formats are accepted | ENG-02, once the real export arrives | `.xlsx`, `.xls` and `.csv` |
