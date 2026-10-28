"""A report's rules on screen (ENG-02 3, ENG-03 4): who sees them and who
sees the controls, each kind of change made through its form and its
confirmation, refusals in words, a locked setting, the history and an older
version, and the links that lead to the rules.
"""

import datetime
import io
import re

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports import rules
from ids_audit.imports.models import Upload
from ids_audit.imports.services import change_ruleset, locked_reason
from ids_audit.pipeline import columns
from ids_audit.pipeline.cleaning import clean_amount, clean_date
from ids_audit.pipeline.ruleset import (
    DEFAULT_SETTINGS,
    HEADER_NAMES,
    SETTING_NAMES,
    merged,
)
from ids_audit.reports.models import Ruleset
from tests.factories import commit, grant, make_report, make_upload, make_user

pytestmark = pytest.mark.django_db

# What the event of a change made on screen keeps (ENG-01 6.3).
FROM_SCREEN = {"screen": "rules"}


def signed_in(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def text_of(response) -> str:
    """Return a page's words, without its tags, with spaces made single."""
    html = response.content.decode()
    words = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", words).replace("&#x27;", "'").replace("&quot;", '"')


def events(report) -> list[AuditEvent]:
    return list(
        AuditEvent.objects.filter(
            report=report, action=AuditEvent.Action.RULESET_CHANGED
        )
    )


def send(client: Client, report, address: str, data: dict, confirm: bool):
    """Send a change's form, and with confirm, its confirmation as well: the
    form's fields again, as the confirmation's hidden fields carry them.
    """
    data = {"version": report.rulesets.order_by("-version").first().version, **data}
    if confirm:
        data["confirm"] = "yes"
    return client.post(address, data)


def change_address(report, setting: str) -> str:
    return reverse("rules_change", args=[report.pk, setting])


def headers_address(report, field: str) -> str:
    return reverse("rules_headers", args=[report.pk, field])


def test_each_example_is_what_a_file_would_get() -> None:
    """Verify the date and amount examples are what the cleaning reads with
    each value, and each value's words say so.
    """
    assert clean_date(rules.DATE_EXAMPLE, "day_first") == datetime.date(2026, 4, 3)
    assert rules.date_example("day_first") == "3 April 2026"
    assert rules.date_example("month_first") == "4 March 2026"
    for separator, text in rules.AMOUNT_EXAMPLES.items():
        assert str(clean_amount(text, separator)) == "12400.500"
    assert rules.amount_example(".") == (
        "12,400.500 is read as 12,400.500, twelve thousand four hundred and a "
        "half. 12.400,500 can't be read, and its row is skipped as an "
        "unreadable amount."
    )
    assert rules.amount_example(",").startswith("12,400.500 can't be read")
    labels = rules.choice_labels("date_order")
    assert labels["month_first"] == "Month first: 03.04.2026 is 4 March 2026"


def test_each_field_says_where_it_is_needed() -> None:
    """Verify each field's sheets come from the column check, with ENG-02
    4.4's conditions for the posting date and the value type.
    """
    needed = {row.key: row.needed for row in rules.field_rows(DEFAULT_SETTINGS)}
    assert needed["gl_code"] == "On the spend sheet and the GL mapping sheet"
    assert needed["cost_center_code"] == (
        "On the spend sheet and the cost center mapping sheet"
    )
    assert needed["ci_code"] == "On the GL mapping sheet"
    assert needed["dept_code"] == "On the cost center mapping sheet"
    assert needed["posting_date"] == (
        "On the spend sheet, unless the file has Fiscal year and Period"
    )
    assert needed["value_type"] == (
        "On the spend sheet, while Actual spend counts by value type"
    )
    assert needed["item_text"] == "No, it's read when it's found"
    by_type = merged(
        DEFAULT_SETTINGS, {"actual_spend_rule": {"actual": ["KR"], "not_actual": []}}
    )
    assert rules.needed_on("value_type", by_type) == "On the spend sheet"
    assert columns.SHEET_TYPE_NAME[columns.SPEND] == "spend sheet"


# Who sees what


def test_anyone_who_can_open_the_report_sees_its_rules_in_words() -> None:
    """Verify a Viewer sees every setting of the version in force in words,
    with its example, default and when a change applies, and the header names
    by field, with no control; a user with no access gets 404.
    """
    report = make_report()
    viewer = make_user(fixed_admin=False)
    grant(viewer, report, "Viewer")
    response = signed_in(viewer).get(reverse("rules", args=[report.pk]))
    assert response.status_code == 200
    text = text_of(response)
    for name in SETTING_NAMES.values():
        assert name in text, name
    for words in (
        "How files are read and counted",
        "They're rules version 1.",
        "Before the report's first upload, check",
        "03.04.2026 is read as 3 April 2026.",
        "12,400.500 is read as 12,400.500",
        "Every row counts",
        "The latest mapping",
        "Missing from the cost center mapping",
        "On the spend sheet and the GL mapping sheet",
        "Amount in LC",
        "Only a fixed administrator can change them",
        rules.READ_APPLIES,
        '"date_order": "day_first"',
    ):
        assert words in text, words
    html = response.content.decode()
    assert "rules/change/" not in html and "rules/headers/" not in html
    stranger = signed_in(make_user(fixed_admin=False))
    assert stranger.get(reverse("rules", args=[report.pk])).status_code == 404


def test_a_fixed_administrator_sees_a_change_link_on_each_setting_that_can_change() -> (
    None
):
    """Verify a fixed administrator gets Change on the five settings a form
    changes, and on every field of the header names, but none on Outside IDS.
    """
    report = make_report()
    html = signed_in(make_user()).get(reverse("rules", args=[report.pk])).content
    html = html.decode()
    for setting in rules.CHANGED_BY_FORM:
        assert f'href="{change_address(report, setting)}"' in html, setting
    for field in HEADER_NAMES:
        assert f'href="{headers_address(report, field)}"' in html, field
    assert (
        'outside_ids_rule"'
        not in html.split('id="outside_ids_rule"')[1]
        .split("</section>")[0]
        .split("card-footer")[0]
    )
    assert html.count('class="btn btn-outline-primary"') == len(rules.CHANGED_BY_FORM)


# Each kind of change, on screen


CHANGES = [
    (
        "date_order",
        {"value": "month_first"},
        {"date_order": "month_first"},
        ("Date order", "Day first", "Month first"),
    ),
    (
        "decimal_separator",
        {"value": ","},
        {"decimal_separator": ","},
        ("Decimal separator", "A point", "A comma"),
    ),
    (
        "mapping_rule",
        {"value": "own_upload"},
        {"mapping_rule": "own_upload"},
        ("Mapping rule", "The latest mapping", "Each file's own mapping"),
    ),
    (
        "currency",
        {"currency": " usd "},
        {"currency": "USD"},
        ("Currency", "OMR", "USD"),
    ),
    (
        "actual_spend_rule",
        {"counts": "by_value_type", "actual": "KR\r\nKG\r\n\r\nKR", "not_actual": "SA"},
        {"actual_spend_rule": {"actual": ["KR", "KG"], "not_actual": ["SA"]}},
        (
            "Actual spend",
            "Every row counts",
            "By value type. Actual spend: KR, KG. Not actual spend: SA.",
        ),
    ),
]


@pytest.mark.parametrize(("setting", "sent", "changes", "words"), CHANGES)
def test_a_setting_changed_on_screen_is_confirmed_then_saved_once(
    setting, sent, changes, words
) -> None:
    """Verify sending a setting's form saves nothing and opens its
    confirmation, with the setting before and after in words, and confirming
    writes exactly one version, through change_ruleset(), with one event that
    keeps screen and no command.
    """
    report = make_report()
    admin = make_user()
    client = signed_in(admin)
    address = change_address(report, setting)
    assert client.get(address).status_code == 200
    shown = send(client, report, address, sent, confirm=False)
    assert shown.status_code == 200
    text = text_of(shown)
    assert "Check the change" in text and "Save as version 2" in text
    assert " ".join(words) in text, text
    assert report.rulesets.count() == 1 and events(report) == []
    saved = send(client, report, address, sent, confirm=True)
    assert saved.status_code == 302 and saved.url == reverse("rules", args=[report.pk])
    latest = report.rulesets.get(version=2)
    assert latest.settings == merged(DEFAULT_SETTINGS, changes)
    assert latest.created_by == admin
    (event,) = events(report)
    assert event.user == admin
    assert {k: v for k, v in event.details.items() if k != "changed"} == {
        "version": 2,
        **FROM_SCREEN,
    }
    assert set(event.details["changed"]) == set(changes)


def test_the_confirmation_says_when_the_change_applies() -> None:
    """Verify the confirmation says a setting read with a file applies to
    files received after it, naming an open upload that keeps its version,
    and the mapping rule applies at once, to every figure.
    """
    report = make_report()
    client = signed_in(make_user())
    sent = {"value": "own_upload"}
    mapping = text_of(
        send(client, report, change_address(report, "mapping_rule"), sent, False)
    )
    assert rules.SHOWN_APPLIES in mapping and rules.READ_APPLIES not in mapping
    make_upload(report, status=Upload.Status.AWAITING_APPROVAL)
    dates = text_of(
        send(
            client,
            report,
            change_address(report, "date_order"),
            {"value": "month_first"},
            False,
        )
    )
    assert rules.READ_APPLIES in dates
    assert "Upload 1 is open, and was read with version 1, which it keeps." in dates


def test_every_row_counts_again_once_chosen_on_screen() -> None:
    """Verify the actual-spend rule goes back to counting every row, with the
    lists left out.
    """
    report = make_report()
    admin = make_user()
    change_ruleset(
        report, admin, {"actual_spend_rule": {"actual": ["KR"], "not_actual": []}}
    )
    send(
        signed_in(admin),
        report,
        change_address(report, "actual_spend_rule"),
        {"counts": "every", "actual": "KR", "not_actual": ""},
        confirm=True,
    )
    assert report.rulesets.get(version=3).settings["actual_spend_rule"] is None


@pytest.mark.parametrize(
    ("sent", "names"),
    [
        ({"add": "  Amt in Loc.Cur. "}, [*HEADER_NAMES["amount"], "Amt in Loc.Cur."]),
        ({"remove": "Amount"}, [n for n in HEADER_NAMES["amount"] if n != "Amount"]),
    ],
)
def test_a_header_name_added_or_removed_on_screen(sent, names) -> None:
    """Verify one header name added to a field, or one removed, is confirmed,
    then changes that field's list alone, with one version and one event.
    """
    report = make_report()
    client = signed_in(make_user())
    address = headers_address(report, "amount")
    page = text_of(client.get(address))
    assert "Change the header names for Amount" in page
    assert "On the spend sheet" in page
    shown = text_of(send(client, report, address, sent, confirm=False))
    assert f"Header names for Amount {', '.join(HEADER_NAMES['amount'])}" in shown
    assert report.rulesets.count() == 1
    send(client, report, address, sent, confirm=True)
    latest = report.rulesets.get(version=2)
    assert latest.settings["header_names"] == {**HEADER_NAMES, "amount": names}
    (event,) = events(report)
    assert event.details["screen"] == "rules" and "command" not in event.details


# Refusals, in words, writing nothing


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize(
    ("address", "sent", "words"),
    [
        (
            ("headers", "amount"),
            {"add": "G/L Acct"},
            "Header names: 'GL Acct' under GL account and 'G/L Acct' under "
            "Amount match the same header.",
        ),
        (("change", "currency"), {"currency": "O1R"}, "Currency: must be three"),
        (
            ("change", "date_order"),
            {"value": "day_first"},
            "The change leaves every setting as it was. Nothing was saved.",
        ),
        (
            ("change", "actual_spend_rule"),
            {"counts": "by_value_type", "actual": "", "not_actual": "SA"},
            "Actual spend: at least one value type must be actual spend",
        ),
        (
            ("headers", "value_type"),
            {"remove": "Value Type"},
            "Header names: Value type needs at least one header name.",
        ),
        (
            ("headers", "gl_code"),
            {"add": "g/l account"},
            "g/l account matches the same headers as G/L Account",
        ),
        (("headers", "gl_code"), {"remove": "Nothing"}, "Nothing isn't one of"),
        (("headers", "gl_code"), {}, "Type one header name to add"),
    ],
)
def test_a_refused_change_says_why_and_saves_nothing(
    address, sent, words, confirm
) -> None:
    """Verify a change refused, by the ruleset's check or by its form, shows
    its words on the form's page, at either step, and writes no version and
    no event.
    """
    report = make_report()
    kind, name = address
    url = reverse(f"rules_{kind}", args=[report.pk, name])
    response = send(signed_in(make_user()), report, url, sent, confirm=confirm)
    assert response.status_code == 200
    assert words in text_of(response)
    assert report.rulesets.count() == 1 and events(report) == []


def test_a_change_made_from_an_older_version_is_refused() -> None:
    """Verify a confirmation sent after another change saved a newer version
    is refused, naming it, and saves nothing (ENG-02 3).
    """
    report = make_report()
    admin = make_user()
    client = signed_in(admin)
    address = change_address(report, "date_order")
    stale = {"version": 1, "value": "month_first"}
    assert "Save as version 2" in text_of(client.post(address, stale))
    change_ruleset(report, admin, {"currency": "USD"})
    response = client.post(address, {**stale, "confirm": "yes"})
    assert "The rules were changed to version 2 while this change was being made" in (
        text_of(response)
    )
    assert report.rulesets.count() == 2
    assert report.rulesets.get(version=2).settings["date_order"] == "day_first"


def test_a_locked_setting_shows_why_and_a_forged_change_is_refused() -> None:
    """Verify on a report with a committed upload the mapping rule has no
    Change link and no form, says why in the words a refused change uses, and
    a change sent for it anyway is refused with those words, saving nothing.
    """
    report = make_report()
    commit(make_upload(report), 0)
    client = signed_in(make_user())
    reason = locked_reason("mapping_rule")
    rules_page = client.get(reverse("rules", args=[report.pk])).content.decode()
    assert reason.replace("'", "&#x27;") in rules_page
    assert f'href="{change_address(report, "mapping_rule")}"' not in rules_page
    address = change_address(report, "mapping_rule")
    page = client.get(address).content.decode()
    assert reason.replace("'", "&#x27;") in page and 'name="value"' not in page
    for confirm in (False, True):
        refused = send(client, report, address, {"value": "own_upload"}, confirm)
        assert f"{reason} Nothing was saved." in text_of(refused)
    assert report.rulesets.count() == 1 and events(report) == []


def test_a_setting_no_form_changes_has_no_address() -> None:
    """Verify Outside IDS, header names as a whole and a field that doesn't
    exist have no form.
    """
    report = make_report()
    client = signed_in(make_user())
    for address in (
        change_address(report, "outside_ids_rule"),
        change_address(report, "header_names"),
        headers_address(report, "vendor"),
    ):
        assert client.get(address).status_code == 404, address
        assert client.post(address, {"version": 1}).status_code == 404, address


@pytest.mark.parametrize("role", ["Viewer", "Uploader", "Approver"])
def test_only_a_fixed_administrator_may_send_a_change(role) -> None:
    """Verify each role gets 403 from a change's form and from its
    confirmation, and nothing is written.
    """
    report = make_report()
    user = make_user(fixed_admin=False)
    grant(user, report, role)
    client = signed_in(user)
    for address, sent in (
        (change_address(report, "date_order"), {"value": "month_first"}),
        (headers_address(report, "amount"), {"add": "Betrag"}),
    ):
        assert client.get(address).status_code == 403
        assert send(client, report, address, sent, confirm=True).status_code == 403
    assert report.rulesets.count() == 1 and events(report) == []


def test_a_disabled_administrator_s_change_is_refused() -> None:
    """Verify a fixed administrator disabled after signing in is signed out
    by the next request, so their change is refused and writes nothing.
    """
    report = make_report()
    admin = make_user()
    client = signed_in(admin)
    User.objects.filter(pk=admin.pk).update(is_active=False)
    response = send(
        client,
        report,
        change_address(report, "date_order"),
        {"value": "month_first"},
        confirm=True,
    )
    assert response.status_code == 302 and "/sign-in" in response["Location"]
    assert report.rulesets.count() == 1 and events(report) == []


# The history and an older version


def test_the_history_lists_every_version_with_what_it_changed() -> None:
    """Verify the history lists every version newest first, the latest In
    force, each with who made it, what it changed in words, where it was made
    and how many uploads were read with it.
    """
    report = make_report()
    admin = make_user()
    make_upload(report)
    client = signed_in(admin)
    send(
        client,
        report,
        change_address(report, "date_order"),
        {"value": "month_first"},
        True,
    )
    call_command(
        "change_ruleset",
        report.name,
        by=admin.username,
        stdin=io.StringIO('{"header_names": {"amount": ["Betrag"]}}'),
        stdout=io.StringIO(),
    )
    html = client.get(reverse("rules", args=[report.pk])).content.decode()
    table = html.split('class="table card-table versions"')[1]
    rows = [text_of_html(row) for row in table.split("<tr>")[2:]]
    assert rows[0].startswith("Version 3 In force")
    assert "Header names for Amount: added Betrag, removed Amount in LC" in rows[0]
    assert rows[0].endswith("Command line 0")
    assert rows[1].startswith("Version 2 ") and "In force" not in rows[1]
    assert "Date order: Day first to Month first" in rows[1]
    assert rows[1].endswith("On this page 0")
    assert str(admin) in rows[1]
    assert rows[2].startswith("Version 1 ")
    assert rows[2].endswith(
        "The defaults every new report starts with Created with the report 1"
    )


def text_of_html(html: str) -> str:
    words = re.sub(r"<[^>]+>", " ", html.split("</tr>")[0])
    return re.sub(r"\s+", " ", words).strip().replace("&#x27;", "'")


def test_an_older_version_shows_its_rules_with_no_controls() -> None:
    """Verify an older version's page shows its own settings, when the next
    replaced it, how many uploads were read with it, and no Change link, even
    for a fixed administrator.
    """
    report = make_report()
    admin = make_user()
    make_upload(report)
    change_ruleset(report, admin, {"date_order": "month_first"})
    client = signed_in(admin)
    old = client.get(reverse("rules_version", args=[report.pk, 1]))
    text = text_of(old)
    assert "Rules version 1" in text
    assert "It was in force until version 2 replaced it on" in text
    assert "1 upload was read with it." in text
    assert "03.04.2026 is read as 3 April 2026." in text
    assert "rules/change/" not in old.content.decode()
    assert "rules/headers/" not in old.content.decode()
    now = text_of(client.get(reverse("rules_version", args=[report.pk, 2])))
    assert "It's the version in force now" in now and "4 March 2026" in now
    assert client.get(reverse("rules_version", args=[report.pk, 3])).status_code == 404


# The links that lead to the rules


@pytest.mark.usefixtures("uploads_dir")
def test_needs_columns_and_an_upload_link_the_rules() -> None:
    """Verify the pick page says a pick reads one file and links the header
    names, naming the field in words, and an upload's page links the version
    it was read with.
    """
    from ids_audit.imports.services import receive_file
    from ids_audit.imports.stage import stage_upload
    from tests.files import HEADER, LINE, csv_file, total_row

    admin = make_user()
    report = make_report()
    header = tuple("Value" if name == "Amount in LC" else name for name in HEADER)
    upload = stage_upload(
        receive_file(
            report, admin, "a.csv", csv_file(LINE, total_row("1000.000"), header=header)
        ),
        admin,
    )
    assert upload.status == Upload.Status.NEEDS_COLUMNS
    client = signed_in(admin)
    pick = client.get(reverse("columns", args=[report.pk, upload.number]))
    text = text_of(pick)
    assert "A pick reads this one file." in text
    assert "Pick a column for Amount" in text
    assert "In the file, no column matches Amount." in text
    assert f'href="{reverse("rules", args=[report.pk])}#header_names"' in (
        pick.content.decode()
    )
    page = client.get(reverse("upload", args=[report.pk, upload.number]))
    assert (
        f'<a href="{reverse("rules_version", args=[report.pk, 1])}">Rules version 1</a>'
        in page.content.decode()
    )


def test_a_new_report_opens_on_its_rules_and_empty_uploads_link_them() -> None:
    """Verify creating a report opens its rules with a message to check them,
    an empty Uploads says to check them and links them, and every page of
    the report links Rules in the sidebar.
    """
    client = signed_in(make_user())
    response = client.post(reverse("new_report"), {"name": "Fresh"}, follow=True)
    report = Ruleset.objects.get(report__name="Fresh").report
    assert response.redirect_chain == [(reverse("rules", args=[report.pk]), 302)]
    assert "Check its rules before its first upload" in text_of(response)
    rules_link = f'href="{reverse("rules", args=[report.pk])}"'
    uploads = client.get(reverse("uploads", args=[report.pk]))
    assert "Before the first upload, check the report's rules" in text_of(uploads)
    assert uploads.content.decode().count(rules_link) == 2
    overview = client.get(reverse("overview", args=[report.pk])).content.decode()
    assert rules_link in overview
    sidebar = client.get(reverse("rules", args=[report.pk])).content.decode()
    assert f'{rules_link} aria-current="page"' in sidebar
