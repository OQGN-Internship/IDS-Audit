"""The sign-in addresses (ENG-01 6.1)."""

from django.urls import path

from ids_audit.accounts import views

urlpatterns = [
    path("", views.home, name="home"),
    path("sign-in", views.SignIn.as_view(), name="sign_in"),
    path("sign-out", views.SignOut.as_view(), name="sign_out"),
    path("password", views.PasswordChange.as_view(), name="password_change"),
]
