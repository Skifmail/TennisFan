"""
Core app URLs - main pages.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("platform/dashboard/", views.platform_dashboard, name="platform_dashboard"),
    path(
        "platform/dashboard/players/export/",
        views.platform_players_export,
        name="platform_players_export",
    ),
    path("api/cities/", views.api_cities, name="api_cities"),
    path("api/recent-matches/", views.api_recent_matches, name="api_recent_matches"),
    path(
        "api/upcoming-matches/", views.api_upcoming_matches, name="api_upcoming_matches"
    ),
    path("rating/", views.rating, name="rating"),
    path("cities/", views.cities_page, name="cities"),
    path("hall-of-fame/", views.hall_of_fame, name="hall_of_fame"),
    path("results/", views.results, name="results"),
    path("rules/", views.rules, name="rules"),
    path("feedback/", views.feedback, name="feedback"),
    path("support/", views.support_feedback, name="support_feedback"),
    path("api/feedback/submit/", views.feedback_submit, name="feedback_submit"),
    path("api/feedback/threads/", views.feedback_threads, name="feedback_threads"),
    path(
        "api/feedback/message/update/",
        views.feedback_message_update,
        name="feedback_message_update",
    ),
    path(
        "api/feedback/unread-count/",
        views.feedback_unread_count,
        name="feedback_unread_count",
    ),
    path("platform/support/", views.support_admin_list, name="support_admin_list"),
    path(
        "platform/support/<int:thread_id>/",
        views.support_admin_thread,
        name="support_admin_thread",
    ),
    path(
        "api/support/admin/reply/",
        views.support_admin_reply,
        name="support_admin_reply",
    ),
    path(
        "api/support/admin/message/update/",
        views.support_admin_update_message,
        name="support_admin_update_message",
    ),
    path(
        "api/support/admin/message/delete/",
        views.support_admin_delete_message,
        name="support_admin_delete_message",
    ),
    path(
        "api/support/admin/unread-count/",
        views.support_admin_unread_count,
        name="support_admin_unread_count",
    ),
    path("private-chat/", views.private_chat_access, name="private_chat_access"),
]
