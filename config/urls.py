"""Every address the application answers (ENG-01 4)."""

from django.urls import include, path

urlpatterns = [
    path("", include("ids_audit.accounts.urls")),
]
