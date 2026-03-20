"""
URL маршруты клубного раздела.
"""

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "clubs"

urlpatterns = [
    path("", views.register_choice, name="register_choice"),
    path("register/step-1/", views.register_step1, name="register_step1"),
    path("register/step-2/", views.register_step2, name="register_step2"),
    path("register/step-3/", views.register_step3, name="register_step3"),
    path("invitations/", views.invitations_list, name="invitations_list"),
    path(
        "invitations/<int:pk>/accept/",
        views.invitation_accept,
        name="invitation_accept",
    ),
    path(
        "invitations/<int:pk>/decline/",
        views.invitation_decline,
        name="invitation_decline",
    ),
    path("set-current/<slug:slug>/", views.set_current_club, name="set_current_club"),
    path(
        "my/",
        RedirectView.as_view(pattern_name="clubs:my_dashboard", permanent=False),
        name="club_my_home",
    ),
    path("my/dashboard/", views.my_dashboard, name="my_dashboard"),
    path("my/tournaments/", views.my_tournaments, name="my_tournaments"),
    path("my/rating/", views.club_rating, name="club_rating"),
    path("my/fees/", views.my_fees, name="my_fees"),
    path("my/fees/pay/", views.my_fees_pay, name="my_fees_pay"),
    path("my/fees/return/", views.my_fees_return, name="my_fees_return"),
    path("my/plan/", views.my_plan, name="my_plan"),
    path("my/plan/change/", views.my_plan_change, name="my_plan_change"),
    path(
        "my/notifications/",
        views.my_notification_settings,
        name="my_notification_settings",
    ),
    path("api/<slug:slug>/search-user/", views.api_search_user, name="api_search_user"),
    path("<slug:slug>/", views.club_public_detail, name="club_public_detail"),
    path("<slug:slug>/edit/", views.club_edit, name="club_edit"),
    path("<slug:slug>/dashboard/", views.dashboard, name="dashboard"),
    path("<slug:slug>/dashboard/plans/", views.plans_manage, name="plans_manage"),
    path("<slug:slug>/dashboard/plans/create/", views.plan_create, name="plan_create"),
    path(
        "<slug:slug>/dashboard/plans/<int:plan_id>/edit/",
        views.plan_edit,
        name="plan_edit",
    ),
    path(
        "<slug:slug>/dashboard/plans/assign/",
        views.plan_assign_member,
        name="plan_assign_member",
    ),
    path("<slug:slug>/join/", views.club_join, name="join"),
    path(
        "<slug:slug>/join/request/",
        views.join_request_create,
        name="join_request_create",
    ),
    path("<slug:slug>/invite/create/", views.invite_create, name="invite_create"),
    path("<slug:slug>/invite/email/", views.invite_by_email, name="invite_by_email"),
    path(
        "<slug:slug>/invite/import-csv/",
        views.invite_import_csv,
        name="invite_import_csv",
    ),
    path("<slug:slug>/members/", views.members_list, name="members_list"),
    path(
        "<slug:slug>/members/<int:member_id>/",
        views.member_detail,
        name="member_detail",
    ),
    path(
        "<slug:slug>/players/<int:player_id>/",
        views.player_profile,
        name="player_profile",
    ),
    path("<slug:slug>/members/export/", views.members_export, name="members_export"),
    path(
        "<slug:slug>/members/<int:member_id>/remove/",
        views.member_remove,
        name="member_remove",
    ),
    path(
        "<slug:slug>/tournaments/",
        views.club_tournaments_list,
        name="club_tournaments_list",
    ),
    path(
        "<slug:slug>/tournaments/<int:tournament_id>/plan-access/",
        views.tournament_plan_access,
        name="tournament_plan_access",
    ),
    path(
        "<slug:slug>/tournaments/create/",
        views.tournament_create,
        name="tournament_create",
    ),
    path(
        "<slug:slug>/tournaments/<int:tournament_id>/edit/",
        views.tournament_edit,
        name="tournament_edit",
    ),
    path("<slug:slug>/rating/", views.dashboard_rating, name="dashboard_rating"),
    path(
        "<slug:slug>/rating/export/",
        views.dashboard_rating_export,
        name="dashboard_rating_export",
    ),
    path("<slug:slug>/fees/settings/", views.fees_settings, name="fees_settings"),
    path("<slug:slug>/fees/payments/", views.fees_payments, name="fees_payments"),
    path(
        "<slug:slug>/fees/payments/mark-paid/",
        views.fees_mark_paid,
        name="fees_mark_paid",
    ),
    path("<slug:slug>/invites/", views.invites_list, name="invites_list"),
    path(
        "<slug:slug>/join-requests/<int:pk>/approve/",
        views.join_request_approve,
        name="join_request_approve",
    ),
    path(
        "<slug:slug>/join-requests/<int:pk>/reject/",
        views.join_request_reject,
        name="join_request_reject",
    ),
    path(
        "<slug:slug>/invites/<int:pk>/deactivate/",
        views.invite_deactivate,
        name="invite_deactivate",
    ),
    path("<slug:slug>/managers/", views.managers_view, name="managers_list"),
    path(
        "<slug:slug>/managers/set-role/",
        views.manager_set_role,
        name="manager_set_role",
    ),
    path("<slug:slug>/subscription/", views.subscription_view, name="subscription"),
    path(
        "<slug:slug>/subscription/pay/", views.subscription_pay, name="subscription_pay"
    ),
    path(
        "<slug:slug>/subscription/return/",
        views.subscription_return,
        name="subscription_return",
    ),
    path(
        "<slug:slug>/notifications/",
        views.club_notification_config,
        name="club_notification_config",
    ),
    path(
        "<slug:slug>/interclub-applications/",
        views.interclub_applications,
        name="interclub_applications",
    ),
    path(
        "<slug:slug>/interclub-applications/<int:pk>/respond/",
        views.interclub_application_respond,
        name="interclub_application_respond",
    ),
    path(
        "<slug:slug>/tournaments/<int:tournament_id>/apply/",
        views.club_tournament_apply,
        name="club_tournament_apply",
    ),
    path("webhook/club-fee/", views.club_fee_webhook, name="club_fee_webhook"),
]
