"""The report list, creating a report, and a report's screens (ENG-03 4)."""

from django.urls import path

from ids_audit.reports import views

urlpatterns = [
    path("", views.report_list, name="reports"),
    path("new", views.new_report, name="new_report"),
    path("<int:report_id>/", views.report_home, name="report"),
    path("<int:report_id>/overview", views.overview, name="overview"),
    path("<int:report_id>/gl", views.gl_accounts, name="gl_accounts"),
    path("<int:report_id>/gl/<str:code>", views.gl_account, name="gl_account"),
    path("<int:report_id>/departments", views.departments, name="departments"),
    path(
        "<int:report_id>/departments/<str:code>",
        views.department,
        name="department",
    ),
    path("<int:report_id>/budget-lines", views.budget_lines, name="budget_lines"),
    path(
        "<int:report_id>/budget-lines/<str:code>",
        views.budget_line,
        name="budget_line",
    ),
    path("<int:report_id>/rows", views.source_rows, name="rows"),
    path("<int:report_id>/exceptions", views.exceptions, name="exceptions"),
]
