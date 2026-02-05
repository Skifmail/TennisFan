from django.urls import path

from . import views

urlpatterns = [
    path("user-bot-webhook/", views.user_bot_webhook, name="telegram_user_bot_webhook"),
    path("connect/", views.connect_redirect, name="telegram_connect"),
]
