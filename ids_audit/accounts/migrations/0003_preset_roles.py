"""The three preset roles, with the plan's permissions (ENG-01 6.2).
Administrators can edit them afterwards like any other role.
"""

from django.db import migrations

# Written out here, not imported, so this migration does the same thing
# whatever the code says later.
PRESETS = {
    "Viewer": ["view", "export"],
    "Uploader": ["view", "export", "upload"],
    "Approver": ["view", "export", "approve", "roll_back", "view_audit_log"],
}


def create_presets(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for name, permissions in PRESETS.items():
        Role.objects.create(name=name, permissions=permissions)


def remove_presets(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(name__in=PRESETS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_roles_and_report_access"),
    ]

    operations = [
        migrations.RunPython(create_presets, remove_presets),
    ]
