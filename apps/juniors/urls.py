"""Маршруты единого раздела детской лиги."""

from django.urls import path

from . import organizer_views, payment_views, views

app_name = "juniors"
urlpatterns = [
    path("", views.home, name="home"),
    path("join/", views.parent_join, name="parent_join"),
    path("rules/", views.rules, name="rules"),
    path("ratings/", views.ratings, name="ratings"),
    path("tournaments/", views.tournaments, name="tournaments"),
    path("tournaments/<int:pk>/", views.tournament_detail, name="tournament"),
    path("tournaments/<int:pk>/manage/", organizer_views.manage, name="manage"),
    path("matches/<int:pk>/result/", organizer_views.result, name="result"),
    path("entries/<int:pk>/withdraw/", views.withdraw_entry, name="withdraw"),
    path("entries/<int:pk>/pay/", payment_views.pay, name="pay"),
    path("payments/", payment_views.payments, name="payments"),
    path("payments/<uuid:pk>/", payment_views.payment_action, name="payment_action"),
    path("payments/webhook/", payment_views.webhook, name="payment_webhook"),
    path("family/", views.family, name="family"),
    path("history/", views.adult_history, name="adult_history"),
    path("family/add/", views.child_form, name="add_child"),
    path("family/<int:pk>/", views.child_detail, name="child"),
    path("family/<int:pk>/edit/", views.child_form, name="edit_child"),
    path("family/<int:pk>/consent/", views.consent_action, name="consent"),
    path("family/<int:pk>/matches/<int:match_id>/appeal/", views.appeal, name="appeal"),
]
