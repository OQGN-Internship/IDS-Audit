"""Every address the application answers (ENG-01 4)."""

from django.urls import include, path

urlpatterns = [
    path("", include("ids_audit.accounts.urls")),
    path("reports/", include("ids_audit.reports.urls")),
    path("reports/<int:report_id>/", include("ids_audit.imports.urls")),
]
