"""Маршруты собственных кемпов TennisFan."""

from django.urls import path

from . import views

app_name = "camps"
urlpatterns = [
    path("", views.camp_list, name="list"),
    path("my/", views.my_camps, name="my"),
    path(
        "confirm-trip/<str:token>/", views.confirm_guest_trip, name="attendance_guest"
    ),
    path("applications/<uuid:pk>/confirm-trip/", views.confirm_trip, name="attendance"),
    path("recover/", views.recover_guest, name="recover"),
    path("access/<str:token>/", views.restore_guest, name="restore"),
    path("applications/<uuid:pk>/cancel/", views.cancel_application, name="cancel"),
    path("team/", views.dashboard, name="dashboard"),
    path("team/sessions/<int:pk>/", views.manage_session, name="manage_session"),
    path("team/sessions/<int:pk>/export/", views.export_session, name="export"),
    path(
        "team/applications/<uuid:pk>/",
        views.manage_application,
        name="manage_application",
    ),
    path("applications/<uuid:pk>/", views.application_detail, name="application"),
    path("sessions/<int:session_id>/apply/", views.apply, name="apply"),
    path("sessions/<int:pk>/games/", views.session_games, name="games"),
    path("<slug:slug>/", views.camp_detail, name="detail"),
]
