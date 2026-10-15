"""Маршруты страниц и HTTP-интерфейса городских чатов."""

from django.urls import path

from . import views

app_name = "chat"
urlpatterns = [
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
    path("api/cities/", views.cities, name="cities"),
    path("api/rooms/<int:room_id>/history/", views.history, name="history"),
    path("api/rooms/<int:room_id>/sync/", views.sync, name="sync"),
    path("api/rooms/<int:room_id>/action/", views.action, name="action"),
]
