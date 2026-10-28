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


def test_a_table_s_cells_are_dense_and_its_total_row_lined(
    page: Page, live_server
) -> None:
    """Verify every cell of a report's tables and of an upload's preview, the
    headings and the first and last of a row too, is padded 4px by 8px, and
    each total row, in a table's foot or its body, has a 2px ink line above
    it, where Tabler would pad a heading 0.5rem above and below, and a card
    table's outer cells 1.25rem, and drop the line above its foot (ENG-03 4,
    Denser tables).
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.set_viewport_size({"width": 1440, "height": 900})
    drawn = """() => [...document.querySelectorAll('.card-table')].map(table => [
        [...table.querySelectorAll('td, th')]
            .filter(cell => cell.offsetParent && !cell.matches('.items-row td:empty'))
            .map(cell => getComputedStyle(cell).padding),
        [...table.querySelectorAll('tr.total td')].map(cell => {
            const style = getComputedStyle(cell);
            return [cell.closest('tfoot, tbody').tagName, style.borderTopStyle,
                    style.borderTopWidth, style.borderTopColor];
        }),
    ])"""
    for address, part in (("overview", "TFOOT"), ("uploads/1", "TBODY")):
        page.goto(f"{live_server.url}/reports/{report.pk}/{address}")
        tables = page.evaluate(drawn)
        assert tables, address
        totals = [line for _, lines in tables for line in lines]
        assert part in [line[0] for line in totals], (address, totals)
        for paddings, lines in tables:
            assert paddings and set(paddings) == {"4px 8px"}, (address, set(paddings))
            for line in lines:
                assert line[1:] == ["solid", "2px", "rgb(0, 0, 0)"], (address, line)


def test_the_filter_card_lines_up_its_controls_and_says_what_is_shown(
    page: Page, live_server
) -> None:
    """Verify a report screen's filter card holds its lists, Show and Export in
    one row, the bottoms of the lists, Show and Export lined up, Export at
    the row's end, and its footer holds the filter tags and the as-of line
    under the row (ENG-03 4, The filter card).
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server.url}/reports/{report.pk}/overview?year=2026")
    card = page.locator(".filter-card")
    row = [
        card.locator(selector).bounding_box()
        for selector in (
            "select[name=year]",
            "select[name=exclude_outside]",
            "button",
            ".export",
        )
    ]
    bottoms = {round(box["y"] + box["height"]) for box in row}
    assert len(bottoms) == 1, row
    body = card.locator(".card-body").bounding_box()
    assert row[-1]["x"] + row[-1]["width"] > body["x"] + body["width"] - 40, (row, body)
    footer = card.locator(".card-footer")
    assert footer.locator(".tag").all_inner_texts() == [
        "Year: 2026",
        "Outside IDS: included",
    ]
    expect(footer.locator(".as-of")).to_contain_text("Figures as of upload 1")
    assert footer.bounding_box()["y"] >= max(bottoms), footer.bounding_box()


def test_a_note_has_its_own_bar_and_icon(page: Page, live_server) -> None:
    """Verify each note on the exceptions screen, such as what a filter
    doesn't apply to, has a 4px ink-subtle bar at its start and an info icon,
    so it isn't taken for a problem's alert or a notice (ENG-03 4).
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.goto(f"{live_server.url}/reports/{report.pk}/exceptions")
    notes = page.locator(".note").evaluate_all(
        """notes => notes.map(note => {
            const style = getComputedStyle(note);
            return [style.borderLeftStyle, style.borderLeftWidth,
                    style.borderLeftColor,
                    note.querySelector('use').getAttribute('href').split('#')[1]];
        })"""
    )
    assert len(notes) == 2, notes
    assert all(note == ["solid", "4px", "rgb(112, 111, 111)", "info"] for note in notes)


def test_each_chart_is_a_card_with_its_title_in_the_header(
    page: Page, live_server
) -> None:
    """Verify each chart on the overview and on a GL account's screen is drawn
    inside a card, with a line around it, its title in the card's header
    above the chart (ENG-03 4, Charts in cards).
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.set_viewport_size({"width": 1440, "height": 900})
    for address, titles in (
        ("overview", ["Spend by GL account", "Running total"]),
        ("gl/610100", ["Running total", "Spend by month"]),
    ):
        page.goto(f"{live_server.url}/reports/{report.pk}/{address}")
        page.wait_for_selector("[data-chart] canvas")
        found = page.evaluate(
            """() => [...document.querySelectorAll('[data-chart]')].map(chart => {
                const card = chart.closest('.card');
                if (!card) return null;
                const header = card.querySelector(':scope > .card-header');
                const style = getComputedStyle(card);
                return [header && header.innerText.trim(),
                        header && header.getBoundingClientRect().bottom
                            <= chart.getBoundingClientRect().top,
                        style.borderTopStyle, style.borderTopWidth];
            })"""
        )
        assert found == [[title, True, "solid", "1px"] for title in titles], found


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


def test_the_sidebar_s_links_and_logo_fill_its_width(page: Page, live_server) -> None:
    """Verify on a wide screen the sidebar's logo, with its line, and each of
    its links span the sidebar, so the band of the screen open now reaches
    its edge, as Tabler's own vertical navbar would otherwise stop them
    at their words.
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server.url}/reports/{report.pk}/overview")
    bar = page.locator("nav.sidebar").bounding_box()
    logo = page.locator("nav.sidebar .logo").bounding_box()
    assert logo["width"] >= bar["width"] - 2, (logo, bar)
    for link in page.locator("nav.sidebar .nav-link").all():
        box = link.bounding_box()
        assert box["width"] >= bar["width"] - 20, (link.inner_text(), box, bar)


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
        f"{base}/rules",
        f"{base}/rules/headers/amount",
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
    """Verify a printed screen leaves out the sidebar, the filter card's form
    and every button, keeps the crumbs, the filter tags, the as-of line, the
    notes and the figures, and prints its tables whole, out of their boxes.
    """
    admin = make_admin()
    report = sample(admin)
    sign_in(page, live_server, admin.username)
    page.goto(f"{live_server.url}/reports/{report.pk}/rows")
    page.emulate_media(media="print")
    for hidden in ("nav.sidebar", ".filter-card form", ".export", "button"):
        expect(page.locator(hidden).first).to_be_hidden()
    for shown in (".crumbs", ".filter-tags", ".as-of", ".note", "table"):
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
