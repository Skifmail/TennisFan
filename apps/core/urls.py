"""
Core app URLs - main pages.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("rating/", views.rating, name="rating"),
    path("results/", views.results, name="results"),
    path("legends/", views.legends, name="legends"),
    path("rules/", views.rules, name="rules"),
    path("feedback/", views.feedback, name="feedback"),
    path("support/", views.support_feedback, name="support_feedback"),
    path("api/feedback/submit/", views.feedback_submit, name="feedback_submit"),
    path("api/feedback/threads/", views.feedback_threads, name="feedback_threads"),
    path("telegram/support-webhook/", views.telegram_support_webhook, name="telegram_support_webhook"),
]
