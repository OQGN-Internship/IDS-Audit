"""{{ upload|tone }}: the badge an upload's state shows as (ENG-03 4, Badges,
notices and messages): "good" once committed, "open" while it's reading,
needs its columns or awaits approval, "bad" once failed, and nothing, the
closed bar, once rejected or rolled back.
"""

from django import template

from ids_audit.imports.models import Upload

register = template.Library()

Status = Upload.Status
TONES = {
    Status.COMMITTED: "good",
    Status.READING: "open",
    Status.NEEDS_COLUMNS: "open",
    Status.AWAITING_APPROVAL: "open",
    Status.FAILED: "bad",
}


@register.filter
def tone(upload: Upload) -> str:
    return TONES.get(upload.status, "")
