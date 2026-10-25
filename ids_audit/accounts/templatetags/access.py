"""{% may "permission" as name %}: whether the signed-in user holds the
permissions on the page's report, so the navigation links only what they may
open. The address itself checks again (ENG-01 6.2).
"""

from django import template

from ids_audit.accounts.access import allowed

register = template.Library()


@register.simple_tag(takes_context=True)
def may(context, *permissions: str) -> bool:
    return allowed(context["request"].user, context["report"], *permissions)
