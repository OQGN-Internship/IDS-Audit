# IDS Spend Audit: Contributing and Git Conventions

These rules apply to every change to the `IDS-Audit` repository: code, tests, scripts and documents. They keep the history readable, the figures traceable, and the client's data out of Git.

## 1. Read the spec before changing anything

The documents in `docs/` decide what the application does:

| Document | Decides |
| --- | --- |
| `docs/IDS Spend Audit Delivery Plan.md` | Scope, phases, permissions, open questions. The single source of decisions |
| `docs/engineering/ENG-01 Architecture, Security and Operations.md` | Components, code structure, roles and grants, sign-in, audit log, running, backup and restore |
| `docs/engineering/ENG-02 Data Model and Import.md` | Tables, reading files, cleaning rules, upload states, duplicates, reconciliation, rollback |
| `docs/engineering/ENG-03 Reports, FAQ and Exports.md` | Screens, filters, drill-down, FAQ answers, Excel exports |

- Before writing code, read the section it implements. Name that section in the PR description, for example "ENG-02 5".
- If the code and a document disagree, fix one of them in the same PR. Never leave them out of step.
- A change of decision goes into the plan first, then the ENG document that owns the topic, then the code.
- During the build, the files here are the working copy of the documents, so every document change is made here. The copies kept as Claude Docs are brought up to date once, at the end of the project.

## 2. Branches

`main` is the only long-lived branch. It must always start, migrate and pass the tests.

Work happens on short-lived task branches cut from `main`:

```
<type>/<scope>-<short-slug>
  feat/pipeline-clean-codes
  fix/imports-hash-refusal
  docs/engineering-grants-order
```

- `<type>` and `<scope>` come from the lists in section 3.
- Delete the branch once it's merged.

**Phases are milestones, not branches.** Each phase of the plan (Phase 1: Correct numbers, Phase 2: Audit-grade import, Phase 3: Reports, Phase 4: Permissions and handover) is a GitHub milestone. Each PR is assigned to the phase it belongs to. A phase is complete when its "Done when" in the plan is met.

## 3. Commit messages

Every commit follows Conventional Commits:

```
type(scope): subject

[optional body]

[optional footer]
```

**Type:**

| Type | Use for |
| --- | --- |
| `feat` | A new capability or screen |
| `fix` | A bug fix |
| `docs` | Documents only |
| `refactor` | A code change that neither fixes a bug nor adds a feature |
| `test` | Adding or correcting tests, including golden files |
| `chore` | Tooling, housekeeping |
| `build` | `Dockerfile`, `compose.yaml`, dependencies in `pyproject.toml` and `uv.lock` |
| `ci` | CI workflows |
| `perf` | A performance improvement |

**Scope:** the part of the repository that changes, from this fixed list (the layout in ENG-01 4):

| Scope | Covers |
| --- | --- |
| `pipeline` | `ids_audit/pipeline/`: reading, cleaning, reconciliation, duplicate rules |
| `accounts` | `ids_audit/accounts/`: users, roles, report access, sign-in, the access check |
| `imports` | `ids_audit/imports/`: uploads, staging, preview, approve, reject, rollback |
| `ledger` | `ids_audit/ledger/`: postings, mapping versions, `posting_resolved` |
| `reports` | `ids_audit/reports/`: report screens, FAQ queries, charts, Excel exports |
| `audit` | `ids_audit/audit/`: the audit log |
| `ui` | `ids_audit/templates/` and `ids_audit/static/` |
| `config` | `config/`: settings, URLs, WSGI |
| `db` | `sql/`: database roles and grants |
| `ops` | `scripts/`, `Dockerfile`, `compose.yaml`, `.env.example` |
| `tests` | Shared test setup, fixtures and golden files |
| `docs` | The plan and the user guide |
| `engineering` | `docs/engineering/` |
| `contributing` | This file |
| `repo` | Root files such as `README.md`, `.gitignore`, `.gitattributes`, `pyproject.toml` |

A change that spans several scopes uses the main one.

**Subject:** imperative, lower case, no full stop, at most 72 characters. "add hash refusal before upload", not "Added hash refusal."

**Body:** wrap at about 72 columns. Explain what changed and why, not how.

**Footer:** name the spec section when the commit implements one, for example `Spec: ENG-02 7.1`.

**Golden results change only on purpose:** a commit that changes an expected result in a golden file does nothing else, and its body says why the figure moved (ENG-02 12.2).

Examples:

```
feat(pipeline): strip leading zeros from GL and cost center codes
fix(imports): refuse a file whose hash matches a committed upload
test(tests): add semicolon CSV golden file
docs(engineering): set default privileges before the first migration
```

**Not allowed:**

- No `Co-Authored-By:` trailers and no AI or tool attribution, in commit messages or PR descriptions.
- No "WIP", "fixup", "oops" or "typo" commits on `main`. They're squashed away before merging (section 4).

## 4. One task, one commit

A task is one item of a phase in the plan, such as "reading the real export" in Phase 1.

- Commit as often as you like on the task branch.
- The PR is squash-merged into `main`, so each task lands as one commit. The PR title becomes that commit's subject, so it follows section 3.
- Keep each PR to one task.

## 5. Pull requests

Every change reaches `main` through a PR, never a direct push.

Each PR has:

- a title in Conventional Commits form;
- a description naming the spec sections it implements and how it was tested;
- the phase milestone;
- the checks in section 6 passing.

While the project has one developer, the author reviews and merges their own PR. Read the full diff before merging. Once a second developer joins, every PR needs one approval from someone other than its author.

## 6. Checks before merging

Run these from the repository root. Each must pass:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

- The tests must pass on a clean checkout, without the real export present. The real-export test is skipped when `real-export/` holds no export (ENG-02 12.2).
- Any change to a figure, a permission or a migration needs a test that would fail without it.
- There is no CI yet. When it's added, it runs the same three commands.

## 7. Client data and secrets

The application handles OQGN's real spend data. These rules have no exceptions:

- **The real SAP export never goes into Git**, nor anything taken from it: not a copy, a sample of its rows, a screenshot, or its expected results. It's kept in `real-export/`, which Git ignores except its README.
- **Backups and uploads stay out of Git.** Git ignores `backups/` except its README, and the uploads volume lives in Docker.
- **No secrets in Git.** The real `.env` is excluded. `.env.example` is committed with every setting name and no real values.
- **The working copy stays outside any synced or cloud folder** such as OneDrive, because it holds `.env`, `real-export/` and `backups/` (ENG-01 6.4).
- **Never run `git clean -x` or `git clean -X`.** They delete ignored files, and that includes the real export and the backups.
- **Test data is made up.** The sample file and the golden files are generated, and their README says how.
- If client data or a secret is ever committed, stop and tell the project owner. Removing it from the history needs a force-push and rotating every exposed secret.

## 8. Code rules

These come from ENG-01 4 and apply in every review:

1. `ids_audit/pipeline/` imports nothing from Django.
2. Amounts are `Decimal` in Python and `NUMERIC(18,3)` in PostgreSQL. They never pass through a float.
3. Only `imports/` writes postings, and only inside the approval transaction.
4. Every view that touches a report calls the one access check in `accounts/`.
5. Only `audit.record()` writes to the audit log.

## 9. Files and line endings

- Shell scripts and SQL files must use LF line endings, or they fail inside the Linux containers. Add `*.sh text eol=lf` and `*.sql text eol=lf` to `.gitattributes` before the first script is committed.
- Generated test fixtures are compared byte for byte (ENG-02 12.3), so Git must never change them. Add `tests/fixtures/*.csv -text`, `tests/fixtures/*.json -text` and `*.xlsx binary` to `.gitattributes` before the first fixture is committed.
- JavaScript libraries are copied into `ids_audit/static/` at a pinned version, never loaded from the internet.
- Dependencies are added with `uv add`, and `uv.lock` is committed in the same commit.

## 10. Versions

- No version tags are used during the build.
- The commit handed over at the end of Day 5 is tagged `v0.1.0`.
- The commit the client accepts is tagged `v1.0.0`.
- Tags are annotated and made on `main`.

## 11. The rules at a glance

1. Read the spec section first. Code and documents never disagree.
2. Branch from `main` as `type/scope-slug`, and delete the branch after merging.
3. Use Conventional Commits from the fixed type and scope lists, with an imperative subject of at most 72 characters.
4. One task, one squash-merged commit.
5. No `Co-Authored-By:` or AI attribution.
6. Every change goes through a PR, with ruff and pytest passing.
7. A golden result changes only in its own commit, with the reason.
8. Never commit the real export, backups, uploads or `.env`.
9. Keep amounts in `Decimal`, and keep Django out of `pipeline/`.
10. Phases are milestones, not branches.

Changes to these conventions go through a `docs(contributing)` PR like any other change.
