"""
Sparring app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.sparring_list, name="sparring_list"),
    path("my/", views.sparring_my_requests, name="sparring_my_requests"),
    path("create/", views.sparring_create, name="sparring_create"),
    path("<int:pk>/edit/", views.sparring_edit, name="sparring_edit"),
    path("<int:pk>/delete/", views.sparring_delete, name="sparring_delete"),
    path("<int:pk>/cancel/", views.sparring_cancel, name="sparring_cancel"),
    path("<int:pk>/respond/", views.sparring_respond, name="sparring_respond"),
    path(
        "response/<int:response_id>/confirm/",
        views.sparring_confirm_response,
        name="sparring_confirm_response",
    ),
    # Парный спарринг 2×2
    path("doubles/", views.doubles_list, name="doubles_list"),
    path("doubles/my/", views.doubles_my_requests, name="doubles_my_requests"),
    path("doubles/create/", views.doubles_create, name="doubles_create"),
    path("doubles/<int:pk>/", views.doubles_detail, name="doubles_detail"),
    path("doubles/<int:pk>/join/", views.doubles_join, name="doubles_join"),
    path(
        "doubles/<int:pk>/add-partner/",
        views.doubles_add_partner,
        name="doubles_add_partner",
    ),
    path(
        "doubles/<int:pk>/remove-member/",
        views.doubles_remove_member,
        name="doubles_remove_member",
    ),
    path("doubles/<int:pk>/confirm/", views.doubles_confirm, name="doubles_confirm"),
    path(
        "doubles/<int:pk>/cancel/",
        views.doubles_cancel_request,
        name="doubles_cancel_request",
    ),
    path(
        "doubles/<int:pk>/join/<int:join_request_id>/accept/",
        views.doubles_accept_join,
        name="doubles_accept_join",
    ),
    path(
        "doubles/<int:pk>/join/<int:join_request_id>/reject/",
        views.doubles_reject_join,
        name="doubles_reject_join",
    ),
    path(
        "doubles/join/<int:join_request_id>/cancel/",
        views.doubles_cancel_join,
        name="doubles_cancel_join",
    ),
]
