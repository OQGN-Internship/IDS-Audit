"""The layout in a real browser (ENG-03 4): long tables keep their headings
in view, the narrow bar stays short with the page never scrolling sideways,
and a printed page holds the figures without the navigation or controls.

These run after every other test, as tests/conftest.py arranges, since
Playwright keeps an event loop running in the main thread.
"""

import pytest
from playwright.sync_api import Page, expect

from ids_audit.imports.services import create_report, receive_file
from ids_audit.imports.stage import stage_upload
from tests.factories import make_admin
from tests.test_browser import SAMPLE, sign_in
from tests.test_report_browser import sample

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("uploads_dir"),
]

# The narrow bar's most, in CSS pixels: 10rem (ENG-03 4, Narrow screens).
BAR_MOST = 160


@pytest.fixture(autouse=True, scope="module")
def database_beside_the_browser(django_db_setup):
    """Let the tests use the database while Playwright's event loop runs in
    the same thread, as tests/test_browser.py does.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        yield


def test_a_long_table_keeps_its_headings_and_total_in_view(
    page: Page, live_server
) -> None:
    """Verify a table taller than the window scrolls in its box, with its
    headings held at the top of the box and its total row at the bottom.
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.set_viewport_size({"width": 1440, "height": 600})
    page.goto(f"{live_server.url}/reports/{report.pk}/rows")
    box = page.locator(".table-wrap")
    sizes = box.evaluate("e => [e.scrollHeight, e.clientHeight]")
    assert sizes[0] > sizes[1], sizes
    box.evaluate("e => { e.scrollTop = 300; }")
    top = box.bounding_box()["y"]
    heading = page.locator(".table-wrap thead th").first.bounding_box()
    assert abs(heading["y"] - top - 1) < 1.5, (heading, top)
    bottom = top + box.bounding_box()["height"]
    total = page.locator(".table-wrap tfoot td").first.bounding_box()
    assert abs(total["y"] + total["height"] - bottom + 1) < 1.5, (total, bottom)


def test_no_button_is_stretched_past_its_words(page: Page, live_server) -> None:
    """Verify an upload's actions keep each button as tall as one line of
    words and narrower than its form, however tall the form beside it.
    """
    admin = make_admin()
    report = create_report("Waiting", admin)
    stage_upload(receive_file(report, admin, "sample.xlsx", SAMPLE.read_bytes()), admin)
    sign_in(page, live_server, admin.username)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server.url}/reports/{report.pk}/uploads/1")
    for name in ("Approve upload 1", "Reject upload 1", "Withdraw upload 1"):
        button = page.get_by_role("button", name=name)
        box = button.bounding_box()
        form = button.locator("xpath=ancestor::form").bounding_box()
        assert box["height"] <= 40, (name, box)
        assert box["width"] < form["width"] / 2, (name, box, form)


def test_the_preview_s_figures_sit_side_by_side_on_a_wide_screen(
    page: Page, live_server
) -> None:
    """Verify an upload's preview puts its figures two by two at least 75rem
    wide, Rows beside Reconciliation, and one under another narrower.
    """
    admin = make_admin()
    report = create_report("Waiting", admin)
    stage_upload(receive_file(report, admin, "sample.xlsx", SAMPLE.read_bytes()), admin)
    sign_in(page, live_server, admin.username)
    page.goto(f"{live_server.url}/reports/{report.pk}/uploads/1")

    def places() -> tuple[dict, dict]:
        rows, reconciliation = page.locator(".preview-grid section").all()[:2]
        return rows.bounding_box(), reconciliation.bounding_box()

    page.set_viewport_size({"width": 1440, "height": 900})
    rows, reconciliation = places()
    assert rows["y"] == reconciliation["y"] and rows["x"] < reconciliation["x"]
    page.set_viewport_size({"width": 1000, "height": 900})
    rows, reconciliation = places()
    assert rows["x"] == reconciliation["x"] and rows["y"] < reconciliation["y"]


def test_the_narrow_bar_is_short_and_nothing_scrolls_sideways(
    page: Page, live_server
) -> None:
    """Verify below 62rem the sidebar is a bar across the top at most 10rem
    tall, its links in one row that scrolls sideways, and no page scrolls
    sideways itself.
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.set_viewport_size({"width": 390, "height": 800})
    base = f"{live_server.url}/reports/{report.pk}"
    for address in (
        f"{base}/overview",
        f"{base}/gl",
        f"{base}/rows",
        f"{base}/uploads",
        f"{base}/uploads/1",
        f"{base}/audit",
        f"{live_server.url}/admin/users/{admin.pk}",
    ):
        page.goto(address)
        bar = page.locator("nav.sidebar").bounding_box()
        assert bar["y"] == 0 and bar["height"] <= BAR_MOST, (address, bar)
        widths = page.evaluate(
            "[document.documentElement.scrollWidth, window.innerWidth]"
        )
        assert widths[0] <= widths[1], (address, widths)
    page.goto(f"{base}/overview")
    links = page.locator(".nav-links")
    assert links.evaluate("e => e.scrollWidth > e.clientWidth")
    expect(page.locator(".nav-links .group-label").first).to_be_hidden()
    expect(page.get_by_role("link", name="Whole audit log")).to_be_attached()


def test_a_printed_page_holds_the_figures_without_the_controls(
    page: Page, live_server
) -> None:
    """Verify a printed screen leaves out the sidebar, the toolbar and every
    button, keeps the crumbs, the filter and as-of lines and the figures,
    and prints its tables whole, out of their boxes.
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.goto(f"{live_server.url}/reports/{report.pk}/rows")
    page.emulate_media(media="print")
    for hidden in ("nav.sidebar", ".toolbar", "button"):
        expect(page.locator(hidden).first).to_be_hidden()
    for shown in (".crumbs", ".filter-line", ".as-of", "table"):
        expect(page.locator(shown).first).to_be_visible()
    expect(page.locator(".crumbs")).to_contain_text(report.name)
    box = page.locator(".table-wrap").evaluate(
        "e => [getComputedStyle(e).maxHeight, getComputedStyle(e).overflowY]"
    )
    assert box == ["none", "visible"]
    position = page.locator(".table-wrap thead th").first.evaluate(
        "e => getComputedStyle(e).position"
    )
    assert position == "static"
