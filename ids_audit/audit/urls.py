"""The audit log screens' addresses (ENG-03 4): a report's log, and the whole
log under the administration screens.
"""

from django.urls import path

from ids_audit.audit import views

urlpatterns = [
    path("reports/<int:report_id>/audit", views.report_log, name="audit"),
    path("admin/audit", views.full_log, name="admin_audit"),
]
