"""Маршруты настроек и привязки текущего устройства."""

from django.urls import path

from . import views

urlpatterns = [
    path("settings/", views.notification_settings, name="notification_settings"),
    path("identity/", views.notification_identity, name="notification_identity"),
    path("device/", views.notification_device, name="notification_device"),
]
