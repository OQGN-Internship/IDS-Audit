"""The addresses of a report's uploads and its rules, under /reports/<id>/
(ENG-03 4).
"""

from django.urls import path

from ids_audit.imports import rule_views, views

urlpatterns = [
    path("uploads", views.uploads, name="uploads"),
    path("uploads/new", views.new_upload, name="new_upload"),
    path("uploads/new-mapping", views.new_mapping, name="new_mapping"),
    path("uploads/<int:number>", views.upload_page, name="upload"),
    path(
        "uploads/<int:number>/exceptions/<slug:kind>",
        views.exception_items,
        name="exception_items",
    ),
    path("uploads/<int:number>/approve", views.approve, name="approve"),
    path("uploads/<int:number>/reject", views.reject, name="reject"),
    path("uploads/<int:number>/withdraw", views.withdraw, name="withdraw"),
    path("uploads/<int:number>/columns", views.columns, name="columns"),
    path("uploads/<int:number>/roll-back", views.roll_back, name="roll_back"),
    path("rules", rule_views.rules_page, name="rules"),
    path("rules/<int:version>", rule_views.rules_version, name="rules_version"),
    path("rules/change/<slug:setting>", rule_views.rules_change, name="rules_change"),
    path("rules/headers/<slug:field>", rule_views.rules_headers, name="rules_headers"),
]
