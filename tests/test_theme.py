"""OQGN's theme over Tabler (ENG-03 4): the colours as custom properties,
Tabler's variables set to them, Tabler's own file, square corners and start
and end spacing, the button kinds, the icons, the official logo on every
page and the sidebar. What the browser draws is in test_theme_browser.py.
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
TABLER = STATIC / "css" / "tabler-1.6.1.min.css"
# Tabler 1.6.1's dist/css/tabler.min.css, from the npm package checked
# against the registry's hash, less its last line, a source-map comment.
TABLER_SHA256 = "09c0ad77d135f90fd8cbc583824b2fdbd247adbc001234d27a36f284bfecab5e"
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
# Tabler's variables app.css sets that aren't colours: the font, the
# corners and the weights.
NOT_COLOURS = re.compile(
    r"--tblr-(font-sans-serif|body-font-family|border-radius(-scale)?|font-weight-[a-z]+)"
)
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


def contrast_of(one: str, two: str) -> float:
    """Return the contrast between two colours written #RRGGBB."""
    light, dark = sorted((luminance(one), luminance(two)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def contrast(one: str, two: str) -> float:
    """Return the contrast between two of the theme's tokens."""
    return contrast_of(THEME[one], THEME[two])


def test_every_colour_is_a_custom_property_from_the_theme() -> None:
    """Verify :root defines each token of the theme's table with its colour,
    every Tabler variable app.css sets that is a colour names one of them,
    and no other rule writes a colour of its own.
    """
    root = re.search(r":root\s*\{([^}]*)\}", CSS)[1]
    defined = dict(re.findall(r"--oqgn-([a-z-]+):\s*(#[0-9A-Fa-f]{6});", root))
    assert {name: value.upper() for name, value in defined.items()} == THEME
    outside = CSS.replace(root, "")
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(|oklch\(", outside)
    tabler = re.findall(r"(--tblr-[a-z0-9-]+):\s*([^;]+);", CSS)
    assert len(tabler) >= 40
    for name, value in tabler:
        if not NOT_COLOURS.fullmatch(name):
            assert re.fullmatch(r"var\(--oqgn-[a-z-]+\)", value), (name, value)
            assert token(value) in THEME, (name, value)
    for selector, declarations in rules():
        for name, value in declarations.items():
            if "color" in name or name.startswith(("background", "border", "outline")):
                assert not re.search(r"\b(white|black|transparent)\b", value), selector


def test_the_colours_that_carry_meaning_stand_out_against_white() -> None:
    """Verify the controls', problems', successes' and charts' colours have
    at least 3:1 against white, the secondary text and links 4.5:1, and a
    link, underlined only under the pointer, 3:1 against the ink around it
    (WCAG 2.1 AA). The browser checks every text on its background.
    """
    for meaning in ("ink-subtle", "danger", "green-text", "green-dark", "orange-text"):
        assert contrast(meaning, "surface") >= 3, meaning
    for text in ("ink-subtle", "green-text", "green-dark"):
        assert contrast(text, "surface") >= 4.5, text
    assert contrast("green-text", "ink") >= 3


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


def test_tabler_is_its_own_file_and_loads_nothing_from_outside() -> None:
    """Verify the stylesheet is Tabler 1.6.1's, byte for byte as kept, with
    no import and no address but the images written into it (ENG-03 4).
    """
    tabler = TABLER.read_bytes()
    assert hashlib.sha256(tabler).hexdigest() == TABLER_SHA256
    text = tabler.decode("utf-8")
    assert "Tabler v1.6.1 (https://tabler.io)" in text[:200]
    assert "@import" not in text and "sourceMappingURL" not in text
    for address in re.findall(r"url\(\s*([^)]*)\)", text):
        assert address.strip("'\"").startswith("data:"), address


def test_app_css_squares_every_corner_and_spaces_by_start_and_end() -> None:
    """Verify app.css rounds nothing, sets every Tabler radius to 0, turns
    shadows, transitions and animation off rather than on, draws no
    gradient, and sets margins, padding and alignment by start and end, so
    Arabic screens wouldn't need a new layout.
    """
    for name, value in re.findall(r"(--tblr-border-radius[a-z-]*):\s*([^;]+);", CSS):
        assert value == "0", name
    for selector, declarations in rules():
        for name, value in declarations.items():
            if "radius" in name:
                assert value == "0", selector
            if name in ("box-shadow", "transition", "animation"):
                assert value == "none !important", (selector, name)
    assert "gradient" not in CSS
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
    templates, by kind: main (btn-primary), secondary (btn-outline-primary)
    or quiet (a plain btn), with links marked "link".
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
                r'<a (class="btn\b[^"]*"[^>]*)>(.*?)</a>', text
            )
        ]
        for link, attributes, words in found:
            classes = re.search(r'class="([^"]*)"', attributes)
            names = set(classes[1].split()) if classes else set()
            assert "btn" in names, (template.name, words)
            if "btn-primary" in names:
                kind = "main"
            elif "btn-outline-primary" in names:
                kind = "secondary"
            else:
                kind = "quiet"
            words = re.sub(r"\{% icon [^%]*%\}", "", words)
            kinds.setdefault(link + kind, []).append(words)
    return kinds


def test_each_button_is_of_its_kind() -> None:
    """Verify only Show and Sign out are quiet buttons, only Reject,
    Withdraw, Disable and Remove are secondary ones, Export to Excel and
    Roll back are the secondary links, every button and link drawn as one is
    one of Tabler's, and the theme gives each kind its colours (ENG-03 4,
    Buttons).
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
    for kind, background, words, border in (
        (".btn", "surface", "ink", "ink-subtle"),
        (".btn-primary", "green-dark", "surface", "green-dark"),
        (".btn-outline-primary", "surface", "green-dark", "green-dark"),
    ):
        assert token(styled[kind]["--tblr-btn-bg"]) == background, kind
        assert token(styled[kind]["--tblr-btn-color"]) == words, kind
        assert token(styled[kind]["--tblr-btn-border-color"]) == border, kind
        for state in ("hover", "active"):
            assert token(styled[kind][f"--tblr-btn-{state}-bg"]) == background, kind
    assert styled[".btn"]["justify-self"] == "start"
    assert styled[".btn"]["align-self"] == "start"


def test_icons_are_tabler_s_kept_in_the_application_s_own_file() -> None:
    """Verify every icon the templates use is drawn in icons.svg, which
    holds Tabler Icons' licence notice, draws only in the colour of the
    words, and every icon is hidden from screen readers (ENG-03 4, Icons).
    """
    drawing = ICONS.read_text(encoding="utf-8")
    assert "Tabler Icons 3.49.0" in drawing
    assert "MIT License" in drawing and "Permission is hereby granted" in drawing
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
    home = reverse("home")
    assert f'<a class="logo" href="{home}">{LOGO_TAG}' in (
        Client().get(reverse("sign_in")).content.decode()
    )
    client = Client()
    client.force_login(make_user())
    report = make_report()
    for url in (
        reverse("reports"),
        reverse("overview", args=[report.pk]),
        reverse("admin_users"),
    ):
        page = client.get(url).content.decode()
        assert f'<a class="navbar-brand logo" href="{home}">{LOGO_TAG}' in page, url


@pytest.mark.django_db
def test_the_sidebar_marks_the_screen_open_now() -> None:
    """Verify one link is marked as the screen open now, a detail screen
    marking its list, nothing on the administration home, which the crumbs
    reach, and the sign-in page has no sidebar, only its card with the logo.
    """
    client = Client()
    user = make_user()
    client.force_login(user)
    report = make_report()
    marked = re.compile(r'<a class="nav-link" href="([^"]+)" aria-current="page">')
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
    assert "aria-current" not in client.get(reverse("admin_home")).content.decode()
    sign_in = Client().get(reverse("sign_in")).content.decode()
    assert "navbar-vertical" not in sign_in and "sidebar" not in sign_in
    panel = re.search(r'<div class="card panel">(.*?)</main>', sign_in, re.S)[1]
    assert re.findall(r"<a [^>]*>", panel) == [
        f'<a class="logo" href="{reverse("home")}">'
    ]
