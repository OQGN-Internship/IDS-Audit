"""Changing a report's ruleset: the check every new version passes, the
version and event a change writes, and the two commands (ENG-02 3).
"""

import io
import json
import threading

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.db import connection

from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports import services
from ids_audit.imports.commit import commit_upload
from ids_audit.imports.models import StagingRow, Upload
from ids_audit.imports.services import (
    RulesetRefused,
    change_ruleset,
    receive_file,
)
from ids_audit.imports.stage import stage_upload
from ids_audit.ledger.models import Posting
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS, HEADER_NAMES, merged, problems
from ids_audit.reports.models import Ruleset
from tests.factories import commit, make_report, make_upload, make_user
from tests.files import csv_file, line, total_row

# The check of a version's settings (pipeline, no database)


def test_the_default_settings_pass_the_check() -> None:
    """Verify version 1, a copy of the defaults, is in its form."""
    assert problems(DEFAULT_SETTINGS) == []


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"date_order": "year_first"}, "date_order must be one of"),
        ({"decimal_separator": ";"}, "decimal_separator must be one of"),
        ({"mapping_rule": "fiscal_year"}, "mapping_rule must be one of"),
        ({"outside_ids_rule": "every_cost_center"}, "outside_ids_rule must be one"),
        ({"currency": "omr"}, "currency must be three capital letters"),
        ({"currency": "OMRR"}, "currency must be three capital letters"),
        ({"rounding": "half_up"}, "There's no setting called rounding."),
        (
            {"actual_spend_rule": {"actual": ["Actual"]}},
            "actual_spend_rule must be null, or hold",
        ),
        (
            {"actual_spend_rule": {"actual": ["Actual "], "not_actual": ["actual"]}},
            "The value type 'actual' is in both lists.",
        ),
        (
            {"actual_spend_rule": {"actual": [""], "not_actual": []}},
            "actual_spend_rule's actual must be a list of value types.",
        ),
        ({"header_names": ["Amount"]}, "header_names must be an object"),
        ({"header_names": {"vendor": ["Vendor"]}}, "no field called vendor"),
        ({"header_names": {"amount": "Amount"}}, "amount must be a list"),
        ({"header_names": {"amount": ["--"]}}, "'--' has no letter or digit"),
        (
            {"header_names": {"amount": ["Amount", "G/L Acct"]}},
            "'G/L Acct' is listed under gl_code and amount",
        ),
    ],
)
def test_settings_out_of_their_form_are_found(changes: dict, problem: str) -> None:
    """Verify each value out of ENG-02 3's form, and a setting that doesn't
    exist, is named.
    """
    found = problems(merged(DEFAULT_SETTINGS, changes))
    assert any(problem in p for p in found), found


def test_a_missing_setting_or_field_is_found() -> None:
    """Verify a version without a setting, or header_names without a field,
    is named.
    """
    without = {k: v for k, v in DEFAULT_SETTINGS.items() if k != "currency"}
    assert problems(without) == ["The setting currency is missing."]
    names = {k: v for k, v in HEADER_NAMES.items() if k != "item_text"}
    assert problems({**DEFAULT_SETTINGS, "header_names": names}) == [
        "header_names has no list for item_text."
    ]


def test_header_names_change_field_by_field() -> None:
    """Verify a change naming one field replaces that field's list and keeps
    the others, while any other setting is replaced whole.
    """
    new = merged(DEFAULT_SETTINGS, {"header_names": {"amount": ["Betrag"]}})
    assert new["header_names"] == {**HEADER_NAMES, "amount": ["Betrag"]}
    rule = {"actual": ["Actual"], "not_actual": []}
    assert merged(DEFAULT_SETTINGS, {"actual_spend_rule": rule}) == {
        **DEFAULT_SETTINGS,
        "actual_spend_rule": rule,
    }


# A change (database)


def events(report) -> list[AuditEvent]:
    """Return the report's ruleset_changed events."""
    return list(
        AuditEvent.objects.filter(
            report=report, action=AuditEvent.Action.RULESET_CHANGED
        )
    )


@pytest.mark.django_db
def test_a_change_writes_the_next_version_and_one_event() -> None:
    """Verify version 2 holds the change and every other setting, version 1
    stays as it was, and one event keeps the version and each setting
    changed, before and after, under the administrator who made it.
    """
    report = make_report()
    admin = make_user()
    version = change_ruleset(
        report,
        admin,
        {"date_order": "month_first", "header_names": {"amount": ["Betrag"]}},
        command="change_ruleset",
    )
    assert (version.version, version.created_by) == (2, admin)
    assert version.settings == {
        **DEFAULT_SETTINGS,
        "date_order": "month_first",
        "header_names": {**HEADER_NAMES, "amount": ["Betrag"]},
    }
    assert report.rulesets.get(version=1).settings == DEFAULT_SETTINGS
    (event,) = events(report)
    assert event.user == admin
    assert event.details == {
        "version": 2,
        "changed": {
            "date_order": {"before": "day_first", "after": "month_first"},
            "header_names": {
                "before": HEADER_NAMES,
                "after": {**HEADER_NAMES, "amount": ["Betrag"]},
            },
        },
        "command": "change_ruleset",
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changes",
    [
        {"date_order": "day_first"},
        {"header_names": {"amount": HEADER_NAMES["amount"]}},
        {},
        {"rounding": "half_up"},
        {"mapping_rule": "fiscal_year"},
    ],
)
def test_a_change_refused_writes_nothing(changes: dict) -> None:
    """Verify a change that leaves every setting as it was, names a setting
    that doesn't exist, or gives a value out of its form is refused, with no
    version and no event.
    """
    report = make_report()
    with pytest.raises(RulesetRefused):
        change_ruleset(report, make_user(), changes)
    assert report.rulesets.count() == 1
    assert events(report) == []


@pytest.mark.django_db
def test_only_an_active_fixed_administrator_changes_a_ruleset() -> None:
    """Verify a user who isn't a fixed administrator is refused, and so is a
    disabled fixed administrator.
    """
    report = make_report()
    disabled = make_user()
    User.objects.filter(pk=disabled.pk).update(is_active=False)
    disabled.refresh_from_db()
    for user in (make_user(fixed_admin=False), disabled):
        with pytest.raises(PermissionDenied):
            change_ruleset(report, user, {"date_order": "month_first"})
    assert report.rulesets.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "refused"),
    [
        (Upload.Status.READING, True),
        (Upload.Status.NEEDS_COLUMNS, True),
        (Upload.Status.AWAITING_APPROVAL, True),
        (Upload.Status.COMMITTED, True),
        (Upload.Status.REJECTED, False),
        (Upload.Status.FAILED, False),
    ],
)
def test_the_rules_shown_with_the_report_change_only_without_uploads(
    status: str, refused: bool
) -> None:
    """Verify mapping_rule and outside_ids_rule can't change while the report
    has an open or a committed upload, and can with only a rejected or failed
    one, while a header name can change either way.
    """
    report = make_report()
    upload = make_upload(report)
    if status == Upload.Status.COMMITTED:
        commit(upload, 0)
    else:
        Upload.objects.filter(pk=upload.pk).update(status=status)
    admin = make_user()
    change_ruleset(report, admin, {"header_names": {"amount": ["Betrag"]}})
    if refused:
        with pytest.raises(RulesetRefused, match="mapping_rule can't change"):
            change_ruleset(report, admin, {"mapping_rule": "own_upload"})
        assert report.rulesets.count() == 2
    else:
        change_ruleset(report, admin, {"mapping_rule": "own_upload"})
        assert report.rulesets.order_by("-version").first().version == 3


@pytest.mark.django_db
@pytest.mark.usefixtures("uploads_dir")
def test_the_next_upload_is_read_with_the_new_version() -> None:
    """Verify a committed upload's postings keep the dates they were read
    with, and the next upload is read month first with version 2.
    """
    report = make_report()
    admin = report.created_by
    first = receive_file(
        report,
        admin,
        "april.csv",
        csv_file(line(Pstng_Date="03.04.2026"), total_row("1000.000")),
    )
    commit_upload(stage_upload(first, admin), admin)
    change_ruleset(report, admin, {"date_order": "month_first"})
    second = receive_file(
        report,
        admin,
        "march.csv",
        csv_file(line(DocumentNo="9", Pstng_Date="03.05.2026"), total_row("1000.000")),
    )
    second = stage_upload(second, admin)
    assert second.ruleset.version == 2
    (posting,) = Posting.objects.filter(upload=first)
    assert posting.posting_date.isoformat() == "2026-04-03"
    (row,) = StagingRow.objects.filter(upload=second)
    assert row.posting_date.isoformat() == "2026-03-05"


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("uploads_dir")
def test_a_rule_change_waits_for_a_file_being_received(monkeypatch) -> None:
    """Verify the change locks the report's row (ENG-02 3). The file stops
    after it's stored, inside its transaction, with the lock held. The change
    waits for the lock, then finds the file's upload open. Without the lock it
    would pass the check before the upload exists.

    Each thread has its own database connection, so this test commits for real
    and Django empties the tables after it.
    """
    report = make_report()
    inside, release = threading.Event(), threading.Event()
    store_file = services.store_file

    def store_and_wait(file_sha256: str, data: bytes) -> None:
        store_file(file_sha256, data)
        inside.set()
        release.wait(timeout=10)

    monkeypatch.setattr(services, "store_file", store_and_wait)
    results = {}

    def run(name: str, action) -> None:
        try:
            results[name] = action()
        except Exception as error:
            results[name] = error
        finally:
            connection.close()

    data = csv_file(line(), total_row("1000.000"))
    receiving = threading.Thread(
        target=run,
        args=("file", lambda: receive_file(report, report.created_by, "a.csv", data)),
    )
    receiving.start()
    assert inside.wait(timeout=10)
    changing = threading.Thread(
        target=run,
        args=(
            "change",
            lambda: change_ruleset(
                report, report.created_by, {"mapping_rule": "own_upload"}
            ),
        ),
    )
    changing.start()
    changing.join(timeout=1)
    waited = changing.is_alive()
    release.set()
    receiving.join(timeout=10)
    changing.join(timeout=10)
    assert waited, "the change didn't wait for the file's lock"
    assert isinstance(results["file"], Upload), results["file"]
    assert isinstance(results["change"], RulesetRefused), results["change"]


# The commands


@pytest.mark.django_db
def test_show_ruleset_prints_the_latest_version() -> None:
    """Verify the report, found ignoring case, prints its latest version and
    its settings as JSON.
    """
    report = make_report()
    change_ruleset(report, make_user(), {"currency": "USD"})
    out = io.StringIO()
    call_command("show_ruleset", report.name.upper(), stdout=out)
    assert json.loads(out.getvalue()) == {
        "report": report.name,
        "version": 2,
        "settings": {**DEFAULT_SETTINGS, "currency": "USD"},
    }
    with pytest.raises(CommandError, match="No report is called Nowhere"):
        call_command("show_ruleset", "Nowhere")


@pytest.mark.django_db
def test_change_ruleset_reads_its_change_from_standard_input() -> None:
    """Verify the command writes the version as the fixed administrator
    named, keeps the command in its event, and says which version is used.
    """
    report = make_report()
    admin = make_user()
    out = io.StringIO()
    call_command(
        "change_ruleset",
        report.name,
        by=admin.username,
        stdin=io.StringIO('{"decimal_separator": ","}'),
        stdout=out,
    )
    latest = Ruleset.objects.get(report=report, version=2)
    assert latest.settings["decimal_separator"] == ","
    (event,) = events(report)
    assert (event.user, event.details["command"]) == (admin, "change_ruleset")
    assert "ruleset version 2" in out.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("given", "by", "message"),
    [
        ("{not json", None, "isn't valid JSON"),
        ('["date_order"]', None, "must be a JSON object"),
        ('{"date_order": "day_first"}', None, "leaves every setting as it was"),
        ('{"date_order": "month_first"}', "nobody", "No account has the username"),
        ('{"date_order": "month_first"}', "viewer", "isn't an active fixed admin"),
    ],
)
def test_change_ruleset_says_why_it_changed_nothing(
    given: str, by: str | None, message: str
) -> None:
    """Verify bad JSON, a value that isn't an object, a refused change, an
    unknown username and a user who isn't a fixed administrator each stop the
    command with a message, and write nothing.
    """
    report = make_report()
    username = {"viewer": make_user(fixed_admin=False).username}.get(by, by)
    with pytest.raises(CommandError, match=message):
        call_command(
            "change_ruleset",
            report.name,
            by=username or make_user().username,
            stdin=io.StringIO(given),
        )
    assert report.rulesets.count() == 1
    assert events(report) == []
