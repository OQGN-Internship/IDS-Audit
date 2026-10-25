"""The six permissions, each on one report (ENG-01 6.2).

They're kept here, apart from access.py, because the Role model reads them
too, and access.py imports the models.
"""

VIEW = "view"
EXPORT = "export"
UPLOAD = "upload"
APPROVE = "approve"
ROLL_BACK = "roll_back"
VIEW_AUDIT_LOG = "view_audit_log"

# Each permission's name on screen, in the plan's order.
LABELS = {
    VIEW: "View",
    EXPORT: "Export",
    UPLOAD: "Upload",
    APPROVE: "Approve",
    ROLL_BACK: "Roll back",
    VIEW_AUDIT_LOG: "View audit log",
}
PERMISSIONS = frozenset(LABELS)


def in_order(permissions) -> list[str]:
    """Return the permissions once each, in the plan's order. A name that
    isn't a permission is kept, last, so the check that refuses it sees it.
    """
    order = list(LABELS)
    return sorted(
        dict.fromkeys(permissions),
        key=lambda name: order.index(name) if name in order else len(order),
    )
