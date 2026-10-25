"""OQGN's theme (ENG-03 4): the colours as custom properties, readable
contrast, square corners and start and end spacing, the official logo on
every page, the sidebar and the overview's tiles.
"""

import hashlib
import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from ids_audit.accounts.models import Role
from tests.factories import make_report, make_user

STATIC = Path(__file__).parent.parent / "ids_audit" / "static"
CSS = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
LOGO = STATIC / "brand" / "oqgn-logo.svg"
# The official file from OQGN's brand guide, received on 2026-10-25.
LOGO_SHA256 = "d29e8b31b0be38f23abfdcd590947cba615de529b8c2827e1cce879adcb0ce1a"
LOGO_TAG = '<img src="/static/brand/oqgn-logo.svg" alt="OQ Gas Networks"'

# The theme's table in ENG-03 4: each token and its colour.
THEME = {
    "surface": "#FFFFFF",
    "ink": "#000000",
    "ink-subtle": "#706F6F",
    "green-text": "#587D39",
    "green-alt": "#73A846",
    "surface-alt": "#EDEDED",
    "line": "#D1D3D4",
    "green-dark": "#344E26",
    "danger": "#E63B48",
    "orange-text": "#AB5B16",
}
# Selectors whose text sits on a band of surface-alt: table headings, the
# "No approved uploads yet" box, and the sidebar's links, which are on one
# under the pointer and while open.
ON_A_BAND = re.compile(r"^(th|\.empty|\.sidebar li a)\b")
TEMPLATES = Path(__file__).parent.parent / "ids_audit" / "templates"
ICONS = STATIC / "icons" / "icons.svg"


def rules() -> list[tuple[str, dict[str, str]]]:
    """Return the stylesheet's rules outside :root, each selector with its
    declarations, at-rules unwrapped.
    """
    body = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    body = re.sub(r":root\s*\{[^}]*\}", "", body)
    body = re.sub(r"@media[^{]*\{", "", body)
    found = []
    for selectors, block in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
        declarations = {}
        for part in block.split(";"):
            if ":" in part:
                name, value = part.split(":", 1)
                declarations[name.strip()] = value.strip()
        for selector in selectors.split(","):
            if selector.strip():
                found.append((selector.strip(), declarations))
    return found


def token(value: str) -> str | None:
    """Return the theme token a value names, as var(--oqgn-token)."""
    named = re.search(r"var\(--oqgn-([a-z-]+)\)", value)
    return named[1] if named else None


def luminance(colour: str) -> float:
    channels = []
    for i in (1, 3, 5):
        c = int(colour[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(one: str, two: str) -> float:
    light, dark = sorted((luminance(THEME[one]), luminance(THEME[two])), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_every_colour_is_a_custom_property_from_the_theme() -> None:
    """Verify :root defines each token of the theme's table with its colour,
    and no other rule writes a colour of its own.
    """
    root = re.search(r":root\s*\{([^}]*)\}", CSS)[1]
    defined = dict(re.findall(r"--oqgn-([a-z-]+):\s*(#[0-9A-Fa-f]{6});", root))
    assert {name: value.upper() for name, value in defined.items()} == THEME
    outside = CSS.replace(root, "")
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(", outside)
    for selector, declarations in rules():
        for name, value in declarations.items():
            if "color" in name or name.startswith(("background", "border", "outline")):
                assert not re.search(r"\b(white|black|transparent)\b", value), selector


def test_text_and_meaningful_colours_are_readable() -> None:
    """Verify each text colour has 4.5:1 against the background it's set
    on, and the controls', problems', successes' and charts' colours 3:1
    against white (WCAG 2.1 AA).
    """
    checked = 0
    for selector, declarations in rules():
        text = token(declarations.get("color", ""))
        if text is None:
            continue
        background = token(declarations.get("background", ""))
        if background is None:
            background = "surface-alt" if ON_A_BAND.match(selector) else "surface"
        assert contrast(text, background) >= 4.5, (selector, text, background)
        checked += 1
    assert checked >= 10
    for meaning in ("ink-subtle", "danger", "green-text", "green-dark", "orange-text"):
        assert contrast(meaning, "surface") >= 3, meaning


def test_the_charts_read_the_theme_s_colours() -> None:
    """Verify charts.js draws bars in green-text, the running total in
    green-dark and Outside IDS in orange-text, read from the stylesheet, and
    the legend's swatch matches (ENG-03 4 and 5).
    """
    script = (STATIC / "js" / "charts.js").read_text(encoding="utf-8")
    read = re.findall(r'colour\("--oqgn-([a-z-]+)"\)', script)
    assert sorted(set(read)) == ["green-dark", "green-text", "orange-text"]
    assert (
        'point.outside ? colour("--oqgn-orange-text") : colour("--oqgn-green-text")'
        in script
    )
    assert 'lineStyle: { color: colour("--oqgn-green-dark") }' in script
    swatch = dict(rules())[".swatch.outside"]
    assert token(swatch["background"]) == "orange-text"


def test_no_rule_rounds_a_corner_or_spaces_by_left_or_right() -> None:
    """Verify nothing has rounded corners, casts a shadow, draws a gradient or
    moves, and margins, padding and alignment go by start and end, so Arabic
    screens wouldn't need a new layout.
    """
    assert "radius" not in CSS
    assert not re.search(r"shadow|gradient|animation|transition", CSS)
    assert not re.search(
        r"(margin|padding)-(left|right)|text-align:\s*(left|right)", CSS
    )
    for selector, declarations in rules():
        for name in ("margin", "padding"):
            sides = declarations.get(name, "").split()
            if len(sides) == 4:
                assert sides[1] == sides[3], selector


def buttons() -> dict[str, list[str]]:
    """Return the words of every button and every link drawn as one in the
    templates, by kind: main, secondary or quiet, with links marked "link".
    """
    kinds: dict[str, list[str]] = {}
    for template in TEMPLATES.rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        found = [
            ("", attributes, words)
            for attributes, words in re.findall(r"<button([^>]*)>(.*?)</button>", text)
        ] + [
            ("link ", attributes, words)
            for attributes, words in re.findall(
                r'<a (class="button[^"]*"[^>]*)>(.*?)</a>', text
            )
        ]
        for link, attributes, words in found:
            classes = re.search(r'class="([^"]*)"', attributes)
            names = set(classes[1].split()) - {"button"} if classes else set()
            kind = (names & {"secondary", "quiet"} or {"main"}).pop()
            words = re.sub(r"\{% icon [^%]*%\}", "", words)
            kinds.setdefault(link + kind, []).append(words)
    return kinds


def test_each_button_is_of_its_kind() -> None:
    """Verify only Show and Sign out are quiet buttons, only Reject,
    Withdraw, Disable and Remove are secondary ones, Export to Excel and
    Roll back are the secondary links, and every kind is styled, for a
    button and for a link drawn as one (ENG-03 4, Buttons).
    """
    kinds = buttons()
    assert sorted(kinds["quiet"]) == ["Show", "Sign out"]
    assert sorted(kinds["secondary"]) == [
        "Disable {{ account.username }}",
        "Reject upload {{ upload.number }}",
        "Remove {{ role.name }}",
        "Withdraw upload {{ upload.number }}",
    ]
    assert sorted(kinds["link secondary"]) == [
        "Export to Excel",
        "Roll back upload {{ upload.number }}",
    ]
    assert sorted(kinds["link main"]) == [
        "New report",
        "New role",
        "New user",
        "Pick the columns",
        "Upload a file",
    ]
    styled: dict[str, dict[str, str]] = {}
    for selector, declarations in rules():
        styled.setdefault(selector, {}).update(declarations)
    assert token(styled["button"]["background"]) == "green-dark"
    assert token(styled[".button"]["background"]) == "green-dark"
    for kind in ("secondary", "quiet"):
        assert token(styled[f"button.{kind}"]["background"]) == "surface", kind
    assert token(styled[".button.secondary"]["background"]) == "surface"
    assert styled["button"]["justify-self"] == "start"
    assert styled["button"]["align-self"] == "start"


def test_icons_come_from_the_application_s_own_file() -> None:
    """Verify every icon the templates use is drawn in icons.svg, which
    draws only in the colour of the words, and every icon is hidden from
    screen readers (ENG-03 4, Icons).
    """
    drawing = ICONS.read_text(encoding="utf-8")
    symbols = set(re.findall(r'<symbol id="([a-z-]+)"', drawing))
    used = set()
    for template in TEMPLATES.rglob("*.html"):
        used |= set(
            re.findall(
                r'\{% icon "([a-z-]+)" %\}', template.read_text(encoding="utf-8")
            )
        )
    assert used and used <= symbols, used - symbols
    colours = re.findall(r'(?:fill|stroke|color)="([^"]*)"', drawing)
    assert set(colours) == {"none", "currentColor"}
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|<style|<image|href=", drawing)


@pytest.mark.django_db
def test_every_icon_on_a_page_is_hidden_from_screen_readers() -> None:
    """Verify a page's icons each name a symbol of the application's file and
    are hidden from screen readers, since their words say the same.
    """
    client = Client()
    client.force_login(make_user())
    page = client.get(reverse("overview", args=[make_report().pk])).content.decode()
    icons = re.findall(r"<svg ([^>]*)><use href=\"([^\"]+)\"></use></svg>", page)
    assert len(icons) >= 10
    for attributes, address in icons:
        assert attributes == 'class="icon" aria-hidden="true" focusable="false"'
        assert address.startswith("/static/icons/icons.svg#"), address


def test_the_logo_is_the_official_file() -> None:
    """Verify the logo is OQGN's file, byte for byte."""
    assert hashlib.sha256(LOGO.read_bytes()).hexdigest() == LOGO_SHA256


@pytest.mark.django_db
def test_every_page_shows_the_logo_linking_home() -> None:
    """Verify the sign-in page, a report's screen, the report list and the
    administration screens each show the logo, with its text alternative,
    linking to the home page.
    """
    tag = f'<a class="logo" href="{reverse("home")}">{LOGO_TAG}'
    assert tag in Client().get(reverse("sign_in")).content.decode()
    client = Client()
    client.force_login(make_user())
    report = make_report()
    for url in (
        reverse("reports"),
        reverse("overview", args=[report.pk]),
        reverse("admin_users"),
    ):
        assert tag in client.get(url).content.decode(), url


@pytest.mark.django_db
def test_the_sidebar_marks_the_screen_open_now() -> None:
    """Verify one link is marked as the screen open now, a detail screen
    marking its list, nothing on the administration home, which the crumbs
    reach, and the sign-in page has no sidebar, only its panel with the logo.
    """
    client = Client()
    user = make_user()
    client.force_login(user)
    report = make_report()
    marked = re.compile(r'<a href="([^"]+)" aria-current="page">')
    for address, link in (
        (reverse("gl_account", args=[report.pk, "610100"]), "gl_accounts"),
        (reverse("faq", args=[report.pk]), "faq"),
        (reverse("uploads", args=[report.pk]), "uploads"),
        (reverse("audit", args=[report.pk]), "audit"),
    ):
        page = client.get(address).content.decode()
        assert marked.findall(page) == [reverse(link, args=[report.pk])], address
    for address, link in (
        (reverse("admin_role", args=[Role.objects.first().pk]), "admin_roles"),
        (reverse("admin_user", args=[user.pk]), "admin_users"),
        (reverse("admin_audit"), "admin_audit"),
    ):
        page = client.get(address).content.decode()
        assert marked.findall(page) == [reverse(link)], address
    assert marked.findall(client.get(reverse("admin_home")).content.decode()) == []
    sign_in = Client().get(reverse("sign_in")).content.decode()
    assert 'class="sidebar"' not in sign_in
    panel = re.search(r'<main class="panel">(.*?)</main>', sign_in, re.S)[1]
    assert re.findall(r"<a [^>]*>", panel) == [
        f'<a class="logo" href="{reverse("home")}">'
    ]
