"""The sign-in addresses, and the administration screens under /admin/
(ENG-01 6.1).
"""

from django.urls import path

from ids_audit.accounts import admin_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("sign-in", views.SignIn.as_view(), name="sign_in"),
    path("sign-out", views.SignOut.as_view(), name="sign_out"),
    path("password", views.PasswordChange.as_view(), name="password_change"),
    path("admin/", admin_views.home, name="admin_home"),
    path("admin/users", admin_views.users, name="admin_users"),
    path("admin/users/new", admin_views.new_user, name="admin_new_user"),
    path("admin/users/<int:user_id>", admin_views.user, name="admin_user"),
    path(
        "admin/users/<int:user_id>/details",
        admin_views.user_details,
        name="admin_user_details",
    ),
    path(
        "admin/users/<int:user_id>/password",
        admin_views.user_password,
        name="admin_user_password",
    ),
    path(
        "admin/users/<int:user_id>/disable",
        admin_views.user_disable,
        name="admin_user_disable",
    ),
    path(
        "admin/users/<int:user_id>/enable",
        admin_views.user_enable,
        name="admin_user_enable",
    ),
    path(
        "admin/users/<int:user_id>/unlock",
        admin_views.user_unlock,
        name="admin_user_unlock",
    ),
    path(
        "admin/users/<int:user_id>/access",
        admin_views.user_access,
        name="admin_user_access",
    ),
    path("admin/roles", admin_views.roles, name="admin_roles"),
    path("admin/roles/new", admin_views.new_role, name="admin_new_role"),
    path("admin/roles/<int:role_id>", admin_views.role, name="admin_role"),
    path(
        "admin/roles/<int:role_id>/remove",
        admin_views.role_remove,
        name="admin_role_remove",
    ),
]
