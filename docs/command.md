# IDS Spend Audit: manage.py commands

Every command `python manage.py help` lists, what it does here, and whether to use it. The project's own commands come first, then the ones from django-axes and Django. Many of Django's are for other kinds of project, and a few would damage this one, so each says plainly when not to use it.

The guide, `IDS Spend Audit Guide.md`, has the everyday commands for administrators. ENG-01 6.1, 7.2 and 7.4 and ENG-02 3 have the rules behind them.

## Two ways to run a command

| Where | How | Settings | Connects to the database as |
| --- | --- | --- | --- |
| **In Docker**, the usual way | `docker compose exec app python manage.py <command>` | `config.settings.production` | `ids_app`, the application's role. It can't change the tables' structure, empty a table, or change an event in the audit log (ENG-01 6.3) |
| **On your machine**, for development | `uv run python manage.py <command>` | `config.settings.dev` | `ids_owner`, which owns every table and can do anything to them (ENG-01 7.5) |

Both reach **the same database**, the one in the `db` container, and so do you, through the application. So a command run with `uv run` changes the data you see at `http://localhost:8000`, with the owner's full rights. Prefer the Docker form for anything an administrator does. Keep `uv run` for development: tests, migrations, the shell.

Two more things to know:

- **Run commands in Git Bash,** in the repository folder. A command that reads a file from standard input, such as `change_ruleset`, needs Git Bash's `<`, which PowerShell doesn't have.
- **Uploaded files are only in Docker.** They live in the `uploads` volume, which the `app` container sees and your machine doesn't. Run `check_uploads` in Docker.

**The commands at a glance**

| Command | What it's for | Use it? |
| --- | --- | --- |
| `create_admin` | Make a fixed administrator | Yes |
| `list_admins` | List the fixed administrators | Yes |
| `disable_admin` | Disable a fixed administrator | Yes, when one leaves |
| `changepassword` | Reset a forgotten password | Yes, for a fixed administrator |
| `axes_reset_username` | Unlock a locked-out username | Yes, for a fixed administrator |
| `createsuperuser` | Refuses, and names `create_admin` | No |
| `show_ruleset` | Print a report's ruleset | Yes |
| `change_ruleset` | Change a report's ruleset | Yes, with the validator |
| `check_uploads` | Check every uploaded file is intact | Yes |
| `report_totals` | Print every report's totals | Yes |
| `apply_grants` | Give `ids_app` its database rights again | Only through `migrate` |
| `record_restore` | Record a restore in the audit log | Never by hand: `restore.sh` runs it |
| `axes_list_attempts` | List failed sign-ins | Yes, to look |
| `axes_reset`, `axes_reset_ip`, `axes_reset_ip_username` | Unlock without a record | No: use the Unlock button or `axes_reset_username` |
| `axes_reset_logs`, `axes_reset_failure_logs` | Delete old sign-in logs | Rarely, as housekeeping |
| `clearsessions` | Delete expired sessions | Yes, as housekeeping |
| `check` | Check the project's configuration | Yes |
| `showmigrations`, `sqlmigrate` | Show migrations and their SQL | Yes, to look |
| `migrate` | Apply migrations | Through Docker's `migrate` job |
| `makemigrations` | Write a migration after a model change | Developers only, with `uv run` |
| `shell` | A Python prompt inside the project | Developers only, to read |
| `findstatic` | Find a static file | Yes, to look |
| `runserver` | Django's development server | Developers only |
| `collectstatic` | Gather static files | Only the Docker build |
| `dbshell` | Open `psql` | Use `docker compose exec db psql` instead |
| `diffsettings` | Print settings | **Never**: it prints the passwords |
| `dumpdata`, `loaddata` | Export or import rows as JSON | **Never**: use the backup scripts |
| `flush` | Delete every row | **Never** |
| Every other Django command | Other kinds of project | No |

## The project's own commands

### accounts: the fixed administrators

Fixed administrators are made and changed only from the command line, so the person at the machine's shell is the one who controls them (ENG-01 6.1). Each command that changes something writes its event to the audit log, as a command-line action.

**`create_admin <username>`** makes a fixed administrator. It asks for the full name, then twice for an initial password, which it doesn't show. The password needs at least 12 characters, not all digits, not a common one, and not like the username. The new administrator must choose their own password at the first sign-in. It refuses a username already taken before asking anything.

```bash
docker compose exec app python manage.py create_admin abalsalmi
```

Accounts are never deleted, and a fixed administrator can't be disabled from the screens, so make one only for a real person.

**`list_admins`** lists every fixed administrator: username, full name, and whether the account is active or disabled. It changes nothing.

```bash
docker compose exec app python manage.py list_admins
```

**`disable_admin <username>`** disables a fixed administrator, for example when they leave. They can't sign in again. The account stays, with its history in the audit log. It refuses a username that isn't a fixed administrator, and one already disabled. No command enables one again.

```bash
docker compose exec app python manage.py disable_admin someone
```

**`changepassword <username>`** resets a forgotten password. It asks twice for a temporary password, and the user must choose their own at the next sign-in. It writes `password_reset`. It's the project's own version of Django's command, which would neither force the change nor record it. It works for any account, but for a user who isn't a fixed administrator, Reset password on their page under Administration does the same from the screens.

```bash
docker compose exec app python manage.py changepassword abalsalmi
```

**`axes_reset_username <username>...`** unlocks one or more usernames locked out after 5 failed sign-ins, so they can sign in at once instead of waiting 15 minutes. It writes `account_unlocked`. A username with no failed sign-ins is said to have none. It replaces django-axes' command of the same name, which doesn't record anything. For a user who isn't a fixed administrator, the Unlock button on their page does the same.

```bash
docker compose exec app python manage.py axes_reset_username abalsalmi
```

**`createsuperuser`** makes no account. Django's would make an account that's neither a fixed administrator nor in the audit log, so the project's version refuses and names `create_admin`.

### imports: rulesets and uploaded files

**`show_ruleset <report>`** prints a report's latest ruleset version as JSON: the report's name, the version number, and every setting, the header names for each field included. The report is found by its name, in any case. It changes nothing.

```bash
docker compose exec app python manage.py show_ruleset "IDS 2026"
```

**`change_ruleset <report> --by <username>`** writes the report's next ruleset version. It reads the settings that change as a JSON object from standard input, so the file comes after `<`, and `-T` lets it reach the command. `--by` names the active fixed administrator making the change, whom the version and its `ruleset_changed` event record.

```bash
docker compose exec -T app python manage.py change_ruleset "IDS 2026" --by abalsalmi < change.json
```

A change to the date order, the decimal separator and the actual-spend rule, as `change.json`:

```json
{
  "date_order": "month_first",
  "decimal_separator": ",",
  "actual_spend_rule": {"actual": ["KR", "KG"], "not_actual": ["SA"]}
}
```

- **Header names:** each field given replaces that field's whole list. To add a name and keep the others, copy the field's names from `show_ruleset` and add the new one, such as `{"header_names": {"amount": ["Amount in LC", "Amount", "Value in Local Currency", "Val/COArea Crcy", "Amt in Loc.Cur."]}}`. Fields left out keep theirs.
- **The settings and their values:** `header_names` (a list of names for each field: `gl_code`, `cost_center_code`, `amount`, `posting_date`, `fiscal_year`, `period`, `document_number`, `line_item`, `company_code`, `value_type`, `currency`, `item_text`, `ci_code`, `gl_name`, `ci_name`, `dept_code`, `cc_name`, `dept_name`), `actual_spend_rule` (`null`, or the two lists `actual` and `not_actual`), `date_order` (`day_first` or `month_first`), `decimal_separator` (`.` or `,`), `currency` (three capital letters), `mapping_rule` (`latest` or `own_upload`) and `outside_ids_rule` (only `missing_from_cc_map` for now).
- **What it refuses, saving nothing:** a change that changes nothing, a setting or field that doesn't exist, a value outside its form, a header name under two fields, and a change to `mapping_rule` or `outside_ids_rule` while the report has an open or approved upload. It says why. So decide `mapping_rule` before the report's first upload.
- **When it applies:** to files received after the change. A file already received keeps the version it was read with: withdraw it and upload it again to read it with the new one.

ENG-02 3 has the rules, and ENG-02 13 says which setting answers which difference in the real export.

**`check_uploads`** checks that every upload's file is in the uploads volume, unchanged, by its SHA-256. It names each upload whose file is missing or changed, and ends with an error if there's any. Otherwise it says how many files it checked. `restore.sh` runs it after a restore (ENG-01 7.2). Run it in Docker, where the volume is.

```bash
docker compose exec app python manage.py check_uploads
```

### reports

**`report_totals`** prints every report's totals as JSON: its actual spend in all, inside and outside IDS, its number of lines, and its actual spend by GL account, budget line, department and month, under the default filters. `backup.sh` saves its output as `totals.json`, and `restore.sh` compares a new run with it (ENG-01 7.2). It's also a quick way to see the figures without the screens. It changes nothing.

```bash
docker compose exec app python manage.py report_totals
```

The output is OQGN's figures once real data is in, so don't paste it anywhere outside the machine.

### audit

**`apply_grants`** runs `sql/grants.sql`, which gives `ids_app` its rights on every table and view and takes back the ones it must not have, such as changing an event in the audit log (ENG-01 6.3). Docker's `migrate` job runs it after every migration, as the owner. It fails in the `app` container, whose role can't grant rights. Run it again through the job:

```bash
docker compose run --rm migrate
```

That runs `migrate` and then `apply_grants`, as `docker compose up` does. After a `uv run python manage.py migrate` on your machine, run `uv run python manage.py apply_grants` too.

**`record_restore <backup> --totals-match yes|no --files-match yes|no`** writes a `database_restored` event: which backup was restored, and whether its totals and files matched. `restore.sh` runs it last (ENG-01 7.2). **Never run it by hand.** It would record a restore that didn't happen, and nothing can remove an event.

## django-axes: failed sign-ins

django-axes counts failed sign-ins and locks a username after 5, for 15 minutes (ENG-01 6.1). The project replaces its `axes_reset_username` with its own, above.

**`axes_list_attempts`** lists the failed sign-ins it's counting: the address, the username tried and how many failures. It changes nothing.

```bash
docker compose exec app python manage.py axes_list_attempts
```

**`axes_reset`**, **`axes_reset_ip <ip>...`** and **`axes_reset_ip_username <ip> <username>`** clear failed sign-ins: all of them, or by address, or by address and username. **Don't use them.** They unlock accounts without writing `account_unlocked`, so the audit log would miss it. The application locks by username, not address, so the address forms don't fit it anyway. Use `axes_reset_username`, or the Unlock button on a user's page.

**`axes_reset_logs [--age <days>]`** and **`axes_reset_failure_logs [--age <days>]`** delete django-axes' own logs of sign-ins and failures older than the given number of days, 30 if none is given. They're housekeeping, and touch neither the lock-outs nor the audit log, which keeps every sign-in for good. There's rarely a reason to run them on one machine.

## Django: the ones this project uses

**`check`** checks the project's configuration and reports problems. `check --deploy` also checks the security settings; while the application runs over plain HTTP on this machine, only the HTTPS warnings should show (ENG-01 6.4).

```bash
docker compose exec app python manage.py check --deploy
```

**`showmigrations`** lists every migration, with `[X]` for those applied. **`sqlmigrate <app> <number>`** prints the SQL one migration runs, such as `sqlmigrate ledger 0005`. Neither changes anything.

```bash
docker compose exec app python manage.py showmigrations
```

**`migrate`** applies migrations. In Docker, the `migrate` job does it on every `docker compose up`, as the owner, followed by `apply_grants`. In the `app` container it fails, since `ids_app` can't change tables. With `uv run` it changes the same database as the owner, so follow it with `apply_grants`. The usual way is a rebuild:

```bash
docker compose up -d --build --wait
```

**`makemigrations`** writes a new migration after a model changes. Developers only, on your machine, never in Docker, where the new file would stay inside the container instead of reaching the repository. Afterwards run `uv run ruff format .` and `uv run ruff check --fix .`, and never edit a migration that has been run or committed (CONTRIBUTING 8). `--check --dry-run` only says whether one is needed.

```bash
uv run python manage.py makemigrations --check --dry-run
```

**`shell`** opens a Python prompt with the project loaded. Use `shell --no-imports` when you'll read its output, since Django 5.2 first prints the names it imported. Use it to read, never to change data: a change made there writes no audit event, and on your machine it has the owner's rights.

**`clearsessions`** deletes expired sign-in sessions from the database. Housekeeping, safe to run at any time.

```bash
docker compose exec app python manage.py clearsessions
```

**`findstatic <path>`** says where a static file is found, such as `findstatic css/app.css`. It changes nothing.

**`runserver [port]`** starts Django's development server, on your machine only. The `app` container already uses port 8000, so give it another, then open `http://127.0.0.1:8001`:

```bash
uv run python manage.py runserver 8001
```

It uses the development settings and the same database as the application.

**`collectstatic`** gathers the CSS, JavaScript and logo into one folder for WhiteNoise to serve. The Docker build runs it (ENG-01 7.1), so there's no need to run it yourself.

## Django: never, or not here

**`flush`** deletes every row in every table. **Never run it.** In Docker it fails, because `ids_app` can't empty the audit log. With `uv run` it runs as the owner and would delete every account, report, upload and event from the database the application uses.

**`diffsettings`** prints every setting that differs from Django's defaults, which includes `SECRET_KEY` and the database passwords. **Never run it**: those values must never be printed (CONTRIBUTING 7).

**`dumpdata`** and **`loaddata`** export and import rows as JSON. `dumpdata` writes OQGN's data, password hashes included, to wherever its output goes. `loaddata` writes rows with no audit event and past the application's rules. **Don't use either.** `scripts/backup.sh` and `scripts/restore.sh` are the way to copy the data (ENG-01 7.2).

**`dbshell`** would open `psql`, but neither the application's image nor your machine has it. To look at the database, open `psql` in the database's own container:

```bash
docker compose exec db psql --username=ids_owner --dbname=ids_audit
```

That's the owner, so read, never change: a change there writes no audit event.

**`remove_stale_contenttypes`** deletes Django's records of models that no longer exist. No model has been removed, so there's nothing for it to do.

**`createcachetable`** makes a database table for Django's cache. The project has no database cache, so it does nothing.

**`sqlflush`** and **`sqlsequencereset`** print SQL without running it. Neither is needed here.

**`optimizemigration`** and **`squashmigrations`** rewrite migrations. **Don't use them**: a migration that has been run or committed is never changed (CONTRIBUTING 8).

**`inspectdb`** writes models from an existing database. Every table already has its model.

**`makemessages`** and **`compilemessages`** are for translations. The screens are English only (ENG-03 12), and the image has no GNU gettext to compile them.

**`sendtestemail`** sends a test email. The application sends no email and has none set up.

**`startapp`** and **`startproject`** make a new app or project. The layout is fixed (ENG-01 4), and a new app is decided in the docs first.

**`test`** and **`testserver`** are Django's own test runner and a server for test data. The project uses pytest: `uv run pytest`, with the database running (CONTRIBUTING 6).
