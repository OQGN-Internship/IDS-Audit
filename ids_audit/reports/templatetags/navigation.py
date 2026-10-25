"""{% open_now "overview" %}: marks the sidebar's link to the screen open now,
a detail screen counting as its list (ENG-03 4, OQGN's theme).
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def open_now(context, *names: str) -> str:
    """Return aria-current="page" when the page's address has one of these
    names, and nothing otherwise.
    """
    match = getattr(context.get("request"), "resolver_match", None)
    if match is not None and match.url_name in names:
        return mark_safe('aria-current="page"')
    return ""
