"""A change to a report's rules made in a real browser (ENG-02 3, ENG-03 4):
from the sidebar's Rules to the form, its confirmation and the saved
version, with the confirmation drawn in the theme, nothing loose on it and
nothing scrolling sideways at 390px. The lists of value types travel through
the confirmation's hidden fields one per line.

These run after every other test, as tests/conftest.py arranges, since
Playwright keeps an event loop running in the main thread.
"""

import pytest
from playwright.sync_api import Page, expect

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.services import create_report
from tests.factories import make_admin
from tests.test_browser import sign_in
from tests.test_theme import THEME, contrast_of
from tests.test_theme_browser import DRAWN, LOOSE

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True, scope="module")
def database_beside_the_browser(django_db_setup):
    """Let the tests use the database while Playwright's event loop runs in
    the same thread, as tests/test_browser.py does.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        yield


def check_drawn(page: Page) -> None:
    """Check the page open now, wide and narrow, draws only the theme's
    colours with readable text, has nothing loose, and doesn't scroll
    sideways at 390px.
    """
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 900})
        found = page.evaluate(DRAWN)
        for colour, where in found["colours"]:
            assert colour in THEME.values(), (width, colour, where)
        for text, background, where in found["texts"]:
            assert contrast_of(text, background) >= 4.5, (width, where)
        loose, _ = page.evaluate(LOOSE)
        assert loose == [], (width, loose)
        widths = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
        assert widths[0] <= widths[1], (width, widths)
    page.set_viewport_size({"width": 1440, "height": 900})


def test_a_change_made_in_the_browser_is_confirmed_then_saved(
    page: Page, live_server
) -> None:
    """Verify a fixed administrator opens the rules from the sidebar, changes
    what counts as actual spend with its form, sees the change before and
    after on its confirmation, and saves it as version 2, with both lists of
    value types as typed, one per line.
    """
    admin = make_admin()
    report = create_report("Spend 2026", admin)
    sign_in(page, live_server, admin.username)
    page.goto(f"{live_server.url}/reports/{report.pk}/uploads")
    page.get_by_role("navigation", name="Main").get_by_role(
        "link", name="Rules"
    ).click()
    expect(
        page.get_by_role("heading", name="How files are read and counted")
    ).to_be_visible()
    page.get_by_role("link", name="Change Actual spend").click()
    page.get_by_label("Only rows whose value type is listed as actual spend").check()
    page.get_by_label("Value types that are actual spend, one per line").fill("KR\nKG")
    page.get_by_label("Value types that are kept but aren't actual spend").fill("SA")
    page.get_by_role("button", name="Check the change").click()
    expect(page.get_by_role("heading", name="Check the change")).to_be_visible()
    expect(
        page.get_by_role(
            "cell", name="By value type. Actual spend: KR, KG. Not actual spend: SA."
        )
    ).to_be_visible()
    assert report.rulesets.count() == 1
    check_drawn(page)
    page.get_by_role("button", name="Save as version 2").click()
    expect(page.get_by_text("Rules version 2 is saved.")).to_be_visible()
    expect(page.get_by_role("link", name="Version 2")).to_be_visible()
    latest = report.rulesets.get(version=2)
    assert latest.settings["actual_spend_rule"] == {
        "actual": ["KR", "KG"],
        "not_actual": ["SA"],
    }
    event = AuditEvent.objects.get(
        report=report, action=AuditEvent.Action.RULESET_CHANGED
    )
    assert event.details["screen"] == "rules"


def test_a_header_name_removed_in_the_browser(page: Page, live_server) -> None:
    """Verify Remove beside a header name opens its confirmation, and saving
    it leaves the field without that name.
    """
    admin = make_admin()
    report = create_report("Headers", admin)
    sign_in(page, live_server, admin.username)
    page.goto(f"{live_server.url}/reports/{report.pk}/rules")
    page.get_by_role("link", name="Change the header names for Line item").click()
    page.get_by_role("button", name="Remove Item", exact=True).click()
    expect(page.get_by_role("cell", name="Line Item, Item")).to_be_visible()
    check_drawn(page)
    page.get_by_role("button", name="Save as version 2").click()
    expect(page.get_by_text("Rules version 2 is saved.")).to_be_visible()
    names = report.rulesets.get(version=2).settings["header_names"]["line_item"]
    assert names == ["Line Item"]
