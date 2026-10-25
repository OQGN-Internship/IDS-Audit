"""The addresses of a report's uploads, under /reports/<id>/ (ENG-03 4)."""

from django.urls import path

from ids_audit.imports import views

urlpatterns = [
    path("uploads", views.uploads, name="uploads"),
    path("uploads/new", views.new_upload, name="new_upload"),
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
]
