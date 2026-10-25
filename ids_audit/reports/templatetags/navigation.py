"""The sidebar and the page header (ENG-03 4, OQGN's theme).

{% open_now "overview" %} marks the sidebar's link to the screen open now, a
detail screen counting as its list. {% crumbs %} links the pages above the
one open now. {% icon "name" %} draws an icon from static/icons/icons.svg.
{% waiting report as name %} gives the report's upload waiting for someone.
"""

from django import template
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from ids_audit.reports import queries

register = template.Library()

# The pages above each page, by the page's address name. A form's address
# that shows its page again, such as a refused one, has its page's crumbs.
REPORT = ("reports", "report")
UPLOAD = (*REPORT, "uploads", "upload")
ABOVE = {
    "new_report": ("reports",),
    **dict.fromkeys(
        (
            "overview",
            "gl_accounts",
            "departments",
            "budget_lines",
            "rows",
            "exceptions",
            "faq",
            "uploads",
            "audit",
        ),
        REPORT,
    ),
    "gl_account": (*REPORT, "gl_accounts"),
    "department": (*REPORT, "departments"),
    "budget_line": (*REPORT, "budget_lines"),
    "faq_page": (*REPORT, "faq"),
    **dict.fromkeys(
        ("new_upload", "upload", "approve", "reject", "withdraw"),
        (*REPORT, "uploads"),
    ),
    **dict.fromkeys(("exception_items", "columns", "roll_back"), UPLOAD),
    **dict.fromkeys(("admin_users", "admin_roles", "admin_audit"), ("admin_home",)),
    **dict.fromkeys(
        (
            "admin_new_user",
            "admin_user",
            "admin_user_details",
            "admin_user_password",
            "admin_user_disable",
            "admin_user_enable",
            "admin_user_unlock",
            "admin_user_access",
        ),
        ("admin_home", "admin_users"),
    ),
    **dict.fromkeys(
        ("admin_new_role", "admin_role", "admin_role_remove"),
        ("admin_home", "admin_roles"),
    ),
}

# The words of each page above that has fixed words.
NAMES = {
    "reports": "Reports",
    "gl_accounts": "GL accounts",
    "departments": "Departments",
    "budget_lines": "Budget lines",
    "faq": "FAQ",
    "uploads": "Uploads",
    "admin_home": "Administration",
    "admin_users": "Users",
    "admin_roles": "Roles",
}


@register.simple_tag(takes_context=True)
def open_now(context, *names: str) -> str:
    """Return aria-current="page" when the page's address has one of these
    names, and nothing otherwise.
    """
    match = getattr(context.get("request"), "resolver_match", None)
    if match is not None and match.url_name in names:
        return mark_safe('aria-current="page"')
    return ""


def above(name: str, context) -> tuple[str, str] | None:
    """Return the words and address of one page above, or None when the page
    doesn't have what it needs, such as an error page with no report.
    """
    report = context.get("report")
    upload = context.get("upload")
    if name in ("reports", "admin_home", "admin_users", "admin_roles"):
        return NAMES[name], reverse(name)
    if not getattr(report, "pk", None):
        return None
    if name == "report":
        return report.name, reverse("overview", args=[report.pk])
    if name == "upload":
        if upload is None:
            return None
        return f"Upload {upload.number}", reverse(
            "upload", args=[report.pk, upload.number]
        )
    return NAMES[name], reverse(name, args=[report.pk])


@register.simple_tag(takes_context=True)
def crumbs(context) -> str:
    """Link the pages above the one open now, or nothing for a page at the
    top or a page that doesn't have what its crumbs need.
    """
    match = getattr(context.get("request"), "resolver_match", None)
    if match is None:
        return ""
    names = ABOVE.get(match.url_name.removesuffix("_export"), ())
    links = [above(name, context) for name in names]
    if not links or None in links:
        return ""
    return format_html(
        '<nav class="crumbs" aria-label="Breadcrumb"><ol>{}</ol></nav>',
        format_html_join(
            "", '<li><a href="{}">{}</a></li>', [(u, w) for w, u in links]
        ),
    )


@register.simple_tag
def icon(name: str) -> str:
    """Draw an icon in the colour of its words, hidden from screen readers,
    since the words beside it say the same.
    """
    return format_html(
        '<svg class="icon" aria-hidden="true" focusable="false">'
        '<use href="{}#{}"></use></svg>',
        static("icons/icons.svg"),
        name,
    )


@register.simple_tag
def waiting(report):
    """Return the report's upload waiting for someone: awaiting approval, or
    needing its columns picked (ENG-03 4, Navigation).
    """
    return queries.waiting(report)
