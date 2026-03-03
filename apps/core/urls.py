"""
Core app URLs - main pages.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/cities/", views.api_cities, name="api_cities"),
    path("api/recent-matches/", views.api_recent_matches, name="api_recent_matches"),
    path(
        "api/upcoming-matches/", views.api_upcoming_matches, name="api_upcoming_matches"
    ),
    path("rating/", views.rating, name="rating"),
    path("hall-of-fame/", views.hall_of_fame, name="hall_of_fame"),
    path("results/", views.results, name="results"),
    path("rules/", views.rules, name="rules"),
    path("feedback/", views.feedback, name="feedback"),
    path("support/", views.support_feedback, name="support_feedback"),
    path("api/feedback/submit/", views.feedback_submit, name="feedback_submit"),
    path("api/feedback/threads/", views.feedback_threads, name="feedback_threads"),
    path(
        "telegram/support-webhook/",
        views.telegram_support_webhook,
        name="telegram_support_webhook",
    ),
    path("private-chat/", views.private_chat_access, name="private_chat_access"),
]
