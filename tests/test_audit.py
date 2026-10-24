"""audit.record(), the one way to write an audit event (ENG-01 4 rule 4, 6.3)."""

import pytest

from ids_audit import audit
from ids_audit.audit.models import AuditEvent
from tests.factories import make_report

pytestmark = pytest.mark.django_db


def test_record_writes_one_event_with_its_links() -> None:
    """Verify record() writes one event with the action, links and details it's
    given, and its time set.
    """
    report = make_report()
    event = audit.record(
        AuditEvent.Action.REPORT_CREATED,
        user=report.created_by,
        report=report,
        details={"name": "IDS 2026"},
    )
    saved = AuditEvent.objects.get()
    assert saved == event
    assert (saved.action, saved.user, saved.report, saved.upload, saved.details) == (
        "report_created",
        report.created_by,
        report,
        None,
        {"name": "IDS 2026"},
    )
    assert saved.at is not None


def test_record_refuses_an_action_not_in_the_list() -> None:
    """Verify an unknown action is refused, and no event is written."""
    with pytest.raises(ValueError, match="isn't an audit action"):
        audit.record("report_deleted")
    assert not AuditEvent.objects.exists()
