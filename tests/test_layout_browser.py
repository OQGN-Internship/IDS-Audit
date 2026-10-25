"""The layout in a real browser (ENG-03 4): long tables keep their headings
in view, the narrow bar stays short with the page never scrolling sideways,
and a printed page holds the figures without the navigation or controls.

These run after every other test, as tests/conftest.py arranges, since
Playwright keeps an event loop running in the main thread.
"""

import pytest
from playwright.sync_api import Page, expect

from tests.factories import make_admin
from tests.test_browser import sign_in
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


def test_the_narrow_bar_is_short_and_nothing_scrolls_sideways(
    page: Page, live_server
) -> None:
    """Verify below 60rem the sidebar is a bar across the top at most 10rem
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
