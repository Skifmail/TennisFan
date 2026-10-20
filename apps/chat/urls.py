"""Маршруты страниц и HTTP-интерфейса городских чатов."""

from django.urls import path

from . import custom_rooms, direct, invitation_views, views

app_name = "chat"
urlpatterns = [
    path("direct/<int:player_id>/start/", direct.start, name="direct_start"),
    path("create/", custom_rooms.create, name="create"),
    path(
        "invitations/<uuid:invitation_id>/",
        invitation_views.invitation_detail,
        name="invitation",
    ),
    path(
        "invitation-email/<str:token>/",
        invitation_views.invitation_email_settings,
        name="invitation_email_settings",
    ),
    path(
        "<int:room_id>/invitations/",
        invitation_views.manage_invitations,
        name="invitations",
    ),
    path("", views.index, name="index"),
    path("join/", views.join, name="join"),
    path(
        "<int:room_id>/data-conditions/", views.data_conditions, name="data_conditions"
    ),
    path(
        "<int:room_id>/consent/withdraw/",
        views.withdraw_consent,
        name="withdraw_consent",
    ),
    path("<int:room_id>/", views.room, name="room"),
    path("<int:room_id>/management/", views.management, name="management"),
    path("api/cities/", views.cities, name="cities"),
    path("api/rooms/<int:room_id>/history/", views.history, name="history"),
    path("api/rooms/<int:room_id>/sync/", views.sync, name="sync"),
    path("api/rooms/<int:room_id>/action/", views.action, name="action"),
]
