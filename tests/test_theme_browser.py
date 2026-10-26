"""OQGN's theme as the browser draws it, over Tabler (ENG-03 4): every colour
on each kind of page is one from the theme's table, all text is readable
against what it's drawn on, nothing has a rounded corner, a shadow, a
gradient or motion, and a focused button or field shows the ring.

Tabler's stylesheet holds hundreds of colours of its own, so reading
app.css alone can't show what a page draws: these ask the browser.

These run after every other test, as tests/conftest.py arranges, since
Playwright keeps an event loop running in the main thread.
"""

from pathlib import Path

import pytest
from playwright.sync_api import Page

from ids_audit.imports.services import create_report, receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.reports.faq import ENTRIES
from tests.factories import PASSWORD, make_admin, make_user
from tests.test_browser import SAMPLE, sign_in
from tests.test_report_browser import sample
from tests.test_theme import THEME, contrast_of

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("uploads_dir"),
]

# What a page draws, read from every element and its ::before and ::after:
# each colour as #RRGGBB, or "translucent" for a colour seen through, with
# where it was found; each text's colour and the colour behind it; and
# whatever rounds a corner, casts a shadow, draws a gradient or moves.
DRAWN = (Path(__file__).parent / "theme_drawn.js").read_text(encoding="utf-8")


@pytest.fixture(autouse=True, scope="module")
def database_beside_the_browser(django_db_setup):
    """Let the tests use the database while Playwright's event loop runs in
    the same thread, as tests/test_browser.py does.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        yield


def pages(page: Page, live_server) -> list[str]:
    """Sign in as a fixed administrator over a report with the sample file
    approved and a second report whose first upload awaits approval, and
    return the address of each kind of page: every screen's layout, the
    upload's summary, preview and actions, the duplicate mode's choice, the
    forms, the administration, the audit log, a message and an error.
    """
    admin = make_admin()
    report = sample(admin)
    waiting = create_report("Waiting", admin)
    stage_upload(
        receive_file(waiting, admin, "sample.xlsx", SAMPLE.read_bytes()), admin
    )
    viewer = make_user(fixed_admin=False)
    sign_in(page, live_server, admin.username)
    base = live_server.url
    r = f"{base}/reports/{report.pk}"
    return [
        f"{base}/reports/",
        f"{r}/overview",
        f"{r}/gl",
        f"{r}/gl/610100",
        f"{r}/rows",
        f"{r}/exceptions",
        f"{r}/faq",
        f"{r}/faq/{ENTRIES[3].slug}",
        f"{r}/uploads",
        f"{r}/uploads/1",
        f"{r}/uploads/1/roll-back",
        f"{r}/uploads/new",
        f"{r}/audit",
        f"{base}/reports/{waiting.pk}/overview",
        f"{base}/reports/{waiting.pk}/uploads/1",
        f"{base}/admin/",
        f"{base}/admin/users",
        f"{base}/admin/users/{viewer.pk}",
        f"{base}/admin/users/{admin.pk}",
        f"{base}/admin/users/new",
        f"{base}/admin/roles",
        f"{base}/admin/roles/new",
        f"{base}/password",
        f"{base}/reports/{report.pk}/nothing-here",
    ]


def drawn(page: Page, address: str, width: int = 1440) -> dict:
    """What the page at the address draws, at the given width."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(address)
    page.wait_for_load_state("networkidle")
    return page.evaluate(DRAWN)


def test_each_kind_of_page_draws_only_the_theme(page: Page, live_server) -> None:
    """Verify each kind of page, wide and narrow, draws every colour, as
    text, a background, a border or an outline, from the theme's table, and
    nothing see-through; all its text has a contrast of at least 4.5:1 with
    the colour behind it (WCAG 2.1 AA); and nothing has a rounded corner but
    a radio button, a shadow, a gradient, or a transition or animation,
    Tabler's components included. One pass, since the pages take time.
    """
    theme = set(THEME.values())
    seen, checked = set(), 0
    for address in pages(page, live_server):
        for width in (1440, 390):
            found = drawn(page, address, width)
            for colour, where in found["colours"]:
                assert colour in theme, (address, width, colour, where)
                seen.add(colour)
            for text, background, where in found["texts"]:
                assert contrast_of(text, background) >= 4.5, (
                    address,
                    width,
                    where,
                    text,
                    background,
                )
                checked += 1
            for kind in ("corners", "shadows", "gradients", "motion"):
                assert found[kind] == [], (address, width, kind, found[kind][:5])
    assert len(seen) >= 8, seen
    assert checked > 1000, checked


def test_a_message_and_a_refusal_are_drawn_in_the_theme(
    page: Page, live_server
) -> None:
    """Verify a success message and a sign-in refused draw only the theme's
    colours, with readable text: Tabler's alert keeps no tint of its own.
    """
    admin = make_admin()
    sign_in(page, live_server, admin.username)
    page.goto(f"{live_server.url}/admin/roles/new")
    page.get_by_label("Name").fill("Checker")
    page.get_by_role("button", name="Create role").click()
    page.wait_for_load_state("networkidle")
    message = page.evaluate(DRAWN)
    page.get_by_role("button", name="Sign out").click()
    page.get_by_label("Username").fill(admin.username)
    page.get_by_label("Password").fill(PASSWORD + "-wrong")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_load_state("networkidle")
    refusal = page.evaluate(DRAWN)
    for found, words in ((message, "is created"), (refusal, "")):
        assert any(
            "alert" in where or "errorlist" in where for _, where in found["colours"]
        ), words
        for colour, where in found["colours"]:
            assert colour in set(THEME.values()), (colour, where)
        for text, background, where in found["texts"]:
            assert contrast_of(text, background) >= 4.5, (where, text, background)


def test_a_focused_button_and_field_show_the_ring(page: Page, live_server) -> None:
    """Verify a button and a field reached with the keyboard show the 2px
    green-text ring 2px outside them, in place of Tabler's own (ENG-03 4,
    Focus).
    """
    page.goto(f"{live_server.url}/sign-in")
    ring = """() => {
        const style = getComputedStyle(document.activeElement);
        return [document.activeElement.tagName, style.outlineStyle,
                style.outlineWidth, style.outlineColor, style.outlineOffset];
    }"""
    page.get_by_label("Username").focus()
    page.keyboard.press("Tab")
    assert page.evaluate(ring) == ["INPUT", "solid", "2px", "rgb(88, 125, 57)", "2px"]
    page.keyboard.press("Tab")
    assert page.evaluate(ring) == ["BUTTON", "solid", "2px", "rgb(88, 125, 57)", "2px"]


def test_a_link_is_underlined_only_under_the_pointer(page: Page, live_server) -> None:
    """Verify a link in the page shows in green-text without a line, and
    gains its underline under the pointer, as Tabler draws links (ENG-03 4,
    Meaning never rests on colour alone).
    """
    sign_in(page, live_server, make_admin().username)
    page.goto(f"{live_server.url}/admin/users")
    crumb = page.locator(".crumbs a").first
    # Its line, and its colour as the screen shows it, whichever way Tabler
    # writes it: hovered, it's a mix in oklab of the same green.
    line = """e => {
        const pen = document.createElement("canvas").getContext("2d");
        pen.fillStyle = getComputedStyle(e).color;
        pen.fillRect(0, 0, 1, 1);
        const [r, g, b] = pen.getImageData(0, 0, 1, 1).data;
        return [getComputedStyle(e).textDecorationLine, [r, g, b]];
    }"""
    assert crumb.evaluate(line) == ["none", [88, 125, 57]]
    crumb.hover()
    assert crumb.evaluate(line) == ["underline", [88, 125, 57]]
