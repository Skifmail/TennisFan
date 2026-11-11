"""Маршруты единого раздела детской лиги."""

from django.urls import path

from . import (
    match_views,
    organizer_views,
    payment_views,
    sparring_views,
    views,
)

app_name = "juniors"
urlpatterns = [
    path("", views.home, name="home"),
    path("join/", views.parent_join, name="parent_join"),
    path("documents/", views.legal_documents, name="documents"),
    path("documents/<slug:slug>/", views.legal_documents, name="document"),
    path("rules/", views.rules, name="rules"),
    path("ratings/", views.ratings, name="ratings"),
    path("tournaments/", views.tournaments, name="tournaments"),
    path("tournaments/<int:pk>/", views.tournament_detail, name="tournament"),
    path("tournaments/<int:pk>/manage/", organizer_views.manage, name="manage"),
    path("matches/", match_views.my_matches, name="my_matches"),
    path("matches/<int:pk>/", match_views.match_detail, name="match"),
    path("matches/<int:pk>/result/", organizer_views.result, name="result"),
    path("sparrings/", sparring_views.sparrings, name="sparrings"),
    path("sparrings/new/", sparring_views.sparring_new, name="sparring_new"),
    path("sparrings/mine/", sparring_views.my_sparrings, name="my_sparrings"),
    path("sparrings/<int:pk>/", sparring_views.sparring_detail, name="sparring"),
    path(
        "sparrings/<int:pk>/close/",
        sparring_views.sparring_close,
        name="sparring_close",
    ),
    path(
        "sparrings/<int:pk>/respond/",
        sparring_views.sparring_respond,
        name="sparring_respond",
    ),
    path(
        "sparrings/responses/<int:pk>/accept/",
        sparring_views.sparring_accept,
        name="sparring_accept",
    ),
    path(
        "sparrings/responses/<int:pk>/reject/",
        sparring_views.sparring_reject,
        name="sparring_reject",
    ),
    path(
        "sparrings/responses/<int:pk>/cancel/",
        sparring_views.sparring_cancel,
        name="sparring_cancel",
    ),
    path("entries/<int:pk>/withdraw/", views.withdraw_entry, name="withdraw"),
    path("entries/<int:pk>/pay/", payment_views.pay, name="pay"),
    path("payments/", payment_views.payments, name="payments"),
    path("payments/<uuid:pk>/", payment_views.payment_action, name="payment_action"),
    path("payments/webhook/", payment_views.webhook, name="payment_webhook"),
    path("family/", views.family, name="family"),
    path("history/", views.adult_history, name="adult_history"),
    path("family/add/", views.child_form, name="add_child"),
    path("family/<int:pk>/", views.child_detail, name="child"),
    path("family/<int:pk>/photo/", views.child_photo, name="child_photo"),
    path("family/<int:pk>/edit/", views.child_form, name="edit_child"),
    path("family/<int:pk>/consent/", views.consent_action, name="consent"),
    path("family/<int:pk>/matches/<int:match_id>/appeal/", views.appeal, name="appeal"),
]
