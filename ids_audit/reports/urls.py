"""The report list, creating a report, and a report's own address (ENG-03 4)."""

from django.urls import path

from ids_audit.reports import views

urlpatterns = [
    path("", views.report_list, name="reports"),
    path("new", views.new_report, name="new_report"),
    path("<int:report_id>/", views.report_home, name="report"),
]
