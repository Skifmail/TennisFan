from django.urls import path

from . import views

urlpatterns = [
    path("donate/", views.donate_view, name="donate"),
    path("preview/", views.payment_preview, name="payment_preview"),
    path("process/", views.payment_process, name="payment_process"),
    path("return/", views.payment_return, name="payment_return"),
    path("success/", views.payment_success, name="payment_success"),
    path("webhook/", views.yookassa_webhook, name="payment_webhook"),
    path(
        "autopay/disable/",
        views.disable_subscription_autopay,
        name="disable_subscription_autopay",
    ),
]
