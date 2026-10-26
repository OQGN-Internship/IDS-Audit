"""The report list, creating a report, and a report's screens, each with
its export at its own address plus /export (ENG-03 4 and 9.1).
"""

from django.urls import path

from ids_audit.reports import views


def screen(address: str, view, name: str) -> list:
    """A screen's address, and its export's."""
    return [
        path(address, view, name=name),
        path(f"{address}/export", view, {"to_excel": True}, name=f"{name}_export"),
    ]


urlpatterns = [
    path("", views.report_list, name="reports"),
    path("new", views.new_report, name="new_report"),
    path("<int:report_id>/", views.report_home, name="report"),
    *screen("<int:report_id>/overview", views.overview, "overview"),
    *screen("<int:report_id>/gl", views.gl_accounts, "gl_accounts"),
    *screen("<int:report_id>/gl/<str:code>", views.gl_account, "gl_account"),
    *screen("<int:report_id>/departments", views.departments, "departments"),
    *screen("<int:report_id>/departments/<str:code>", views.department, "department"),
    *screen("<int:report_id>/budget-lines", views.budget_lines, "budget_lines"),
    *screen(
        "<int:report_id>/budget-lines/<str:code>", views.budget_line, "budget_line"
    ),
    *screen("<int:report_id>/rows", views.source_rows, "rows"),
    *screen("<int:report_id>/exceptions", views.exceptions, "exceptions"),
    path("<int:report_id>/faq", views.faq_index, name="faq"),
    *screen("<int:report_id>/faq/<slug:slug>", views.faq_page, "faq_page"),
]
