# IDS Spend Audit

A small web application for OQGN that shows how much IDS has actually spent, against which GL account, from which budget line, and by which cost center and department. Every figure can be traced back to the row of the SAP export it came from.

## What it does

1. **Upload:** a user uploads the SAP spend export: an Excel file with its GL and cost center mapping sheets, or a CSV of spend lines.
2. **Check:** the app finds the columns, cleans the codes, flags duplicates and rows it can't use, and reconciles the file against its control total.
3. **Approve:** the user sees a preview. The upload counts only once an approver accepts it, and an approved upload can be rolled back.
4. **Report:** totals, GL account and department views, exceptions, charts and a fixed FAQ, each figure leading down to its source rows. Every screen except Uploads and the Audit log exports to Excel.

Each user has their own account and per-report permissions. Every sign-in, upload, approval, rollback, export and access change goes in an audit log. Amounts are in OMR, to three decimal places, and never pass through a float.

## Status

Phase 1, correct numbers. The client has approved the delivery plan. The real SAP export hasn't arrived yet, so the build starts on a generated sample file made to look like one (ENG-02 12.3). The build runs over Days 2 to 5, followed by the client's acceptance:

| Phase | Day | Delivers |
| --- | --- | --- |
| 1. Correct numbers | 2 | Reading and cleaning the real export, totals that match SAP |
| 2. Audit-grade import | 3 | Preview, approval, duplicates, reconciliation, rollback, audit log |
| 3. Reports | 4 | Screens, FAQ, charts, Excel exports |
| 4. Permissions and handover | 5 | Users and roles, backup and restore, handover |

Phase 1 starts with the sample file and the script that generates it. The application code follows.

## Documentation

Start with the plan. The engineering documents implement it.

| Document | Read it for |
| --- | --- |
| [Delivery Plan](docs/IDS%20Spend%20Audit%20Delivery%20Plan.md) | Scope, phases, users and permissions, open questions, glossary |
| [ENG-01 Architecture, Security and Operations](docs/engineering/ENG-01%20Architecture,%20Security%20and%20Operations.md) | Components, code structure, sign-in, roles and grants, running, backup and restore |
| [ENG-02 Data Model and Import](docs/engineering/ENG-02%20Data%20Model%20and%20Import.md) | Tables, file reading, cleaning rules, upload states, duplicates, reconciliation |
| [ENG-03 Reports, FAQ and Exports](docs/engineering/ENG-03%20Reports,%20FAQ%20and%20Exports.md) | Screens, filters, drill-down, FAQ answers, Excel exports |
| [Contributing](docs/CONTRIBUTING.md) | Branches, commits, PRs, checks, client data rules |

## Built with

Python 3.12, Django 5.2, PostgreSQL 17, HTMX, Alpine.js and ECharts, packaged with Docker Compose. Dependencies are managed with uv; ruff, pytest, Hypothesis and Playwright check the code. ENG-01 explains each choice.

## Getting started

**Now (early Phase 1):** there's no app to run yet. Clone the repository and read the documents above, starting with the plan.

```bash
git clone https://github.com/OQGN-Internship/IDS-Audit.git
```

**Once Phase 1 lands:** you'll need Docker, Python 3.12 and [uv](https://docs.astral.sh/uv/). The whole app then starts with one command (ENG-01 7.1):

```bash
cd IDS-Audit
cp .env.example .env          # then fill in the settings
docker compose up -d          # starts the database, runs migrations, starts the app
docker compose exec app python manage.py create_admin <username>
uv run pytest
```

The app is then at `http://localhost:8000`, reachable only from the same machine.

## Client data

The real SAP export is OQGN's data. Never commit it, any rows taken from it, backups, uploads or `.env`. The real export goes in `real-export/` and backups in `backups/`, which Git ignores, and the working copy stays outside any synced folder such as OneDrive. Tests use generated sample files only. See [Contributing](docs/CONTRIBUTING.md), section 7.

## Contributing

Read the matching section of the spec before changing code, follow [CONTRIBUTING.md](docs/CONTRIBUTING.md), and make sure `ruff` and `pytest` pass before you merge.
