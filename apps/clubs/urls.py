"""
URL маршруты клубного раздела.
"""

from django.urls import path
from django.views.generic import RedirectView

from .views.dashboard import (
    club_edit,
    club_tournament_apply,
    club_tournaments_list,
    dashboard,
    interclub_application_respond,
    interclub_applications,
    manager_set_role,
    managers_view,
    my_dashboard,
    my_tournaments,
    plan_assign_member,
    plan_create,
    plan_edit,
    plans_manage,
    tournament_create,
    tournament_edit,
    tournament_plan_access,
)
from .views.invites import (
    invitation_accept,
    invitation_decline,
    invitations_list,
    invite_by_email,
    invite_create,
    invite_deactivate,
    invite_import_csv,
    invites_list,
    join_request_approve,
    join_request_reject,
)
from .views.members import (
    api_search_user,
    member_detail,
    member_remove,
    members_export,
    members_list,
)
from .views.payments import (
    club_fee_webhook,
    club_payment_webhook,
    fees_mark_paid,
    fees_payments,
    fees_settings,
    my_fee_disable_autopay,
    my_fee_payment_preview,
    my_fees,
    my_fees_pay,
    my_fees_return,
    my_payments,
    payment_settings,
)
from .views.public import (
    club_discover,
    club_join,
    club_public_detail,
    join_request_create,
    register_choice,
    register_step1,
    register_step2,
    register_step3,
    set_current_club,
)
from .views.rating import (
    club_rating,
    dashboard_rating,
    dashboard_rating_export,
    player_profile,
)
from .views.subscription import (
    club_notification_config,
    my_notification_settings,
    my_plan,
    my_plan_cancel,
    my_plan_change,
    my_plan_disable_autopay,
    my_plan_payment_preview,
    my_plan_payment_process,
    my_plan_payment_return,
    subscription_pay,
    subscription_return,
    subscription_view,
)

app_name = "clubs"

urlpatterns = [
    path("", register_choice, name="register_choice"),
    path("discover/", club_discover, name="club_discover"),
    path("register/step-1/", register_step1, name="register_step1"),
    path("register/step-2/", register_step2, name="register_step2"),
    path("register/step-3/", register_step3, name="register_step3"),
    path("invitations/", invitations_list, name="invitations_list"),
    path(
        "invitations/<int:pk>/accept/",
        invitation_accept,
        name="invitation_accept",
    ),
    path(
        "invitations/<int:pk>/decline/",
        invitation_decline,
        name="invitation_decline",
    ),
    path("set-current/<slug:slug>/", set_current_club, name="set_current_club"),
    path(
        "my/",
        RedirectView.as_view(pattern_name="clubs:my_dashboard", permanent=False),
        name="club_my_home",
    ),
    path("my/dashboard/", my_dashboard, name="my_dashboard"),
    path("my/tournaments/", my_tournaments, name="my_tournaments"),
    path("my/rating/", club_rating, name="club_rating"),
    path("my/payments/", my_payments, name="my_payments"),
    path("my/fees/", my_fees, name="my_fees"),
    path(
        "my/fees/payment/",
        my_fee_payment_preview,
        name="my_fee_payment_preview",
    ),
    path("my/fees/pay/", my_fees_pay, name="my_fees_pay"),
    path("my/fees/return/", my_fees_return, name="my_fees_return"),
    path(
        "my/fees/autopay/disable/",
        my_fee_disable_autopay,
        name="my_fee_disable_autopay",
    ),
    path("my/plan/", my_plan, name="my_plan"),
    path("my/plan/change/", my_plan_change, name="my_plan_change"),
    path(
        "my/plan/payment/<int:plan_id>/",
        my_plan_payment_preview,
        name="my_plan_payment_preview",
    ),
    path(
        "my/plan/payment/process/",
        my_plan_payment_process,
        name="my_plan_payment_process",
    ),
    path(
        "my/plan/payment/return/",
        my_plan_payment_return,
        name="my_plan_payment_return",
    ),
    path("my/plan/cancel/", my_plan_cancel, name="my_plan_cancel"),
    path(
        "my/plan/autopay/disable/",
        my_plan_disable_autopay,
        name="my_plan_disable_autopay",
    ),
    path(
        "my/notifications/",
        my_notification_settings,
        name="my_notification_settings",
    ),
    path("api/<slug:slug>/search-user/", api_search_user, name="api_search_user"),
    path("<slug:slug>/", club_public_detail, name="club_public_detail"),
    path("<slug:slug>/edit/", club_edit, name="club_edit"),
    path("<slug:slug>/dashboard/", dashboard, name="dashboard"),
    path("<slug:slug>/dashboard/plans/", plans_manage, name="plans_manage"),
    path("<slug:slug>/dashboard/plans/create/", plan_create, name="plan_create"),
    path(
        "<slug:slug>/dashboard/plans/<int:plan_id>/edit/",
        plan_edit,
        name="plan_edit",
    ),
    path(
        "<slug:slug>/dashboard/plans/assign/",
        plan_assign_member,
        name="plan_assign_member",
    ),
    path("<slug:slug>/join/", club_join, name="join"),
    path(
        "<slug:slug>/join/request/",
        join_request_create,
        name="join_request_create",
    ),
    path("<slug:slug>/invite/create/", invite_create, name="invite_create"),
    path("<slug:slug>/invite/email/", invite_by_email, name="invite_by_email"),
    path(
        "<slug:slug>/invite/import-csv/",
        invite_import_csv,
        name="invite_import_csv",
    ),
    path("<slug:slug>/members/", members_list, name="members_list"),
    path(
        "<slug:slug>/members/<int:member_id>/",
        member_detail,
        name="member_detail",
    ),
    path(
        "<slug:slug>/players/<int:player_id>/",
        player_profile,
        name="player_profile",
    ),
    path("<slug:slug>/members/export/", members_export, name="members_export"),
    path(
        "<slug:slug>/members/<int:member_id>/remove/",
        member_remove,
        name="member_remove",
    ),
    path(
        "<slug:slug>/tournaments/",
        club_tournaments_list,
        name="club_tournaments_list",
    ),
    path(
        "<slug:slug>/tournaments/<int:tournament_id>/plan-access/",
        tournament_plan_access,
        name="tournament_plan_access",
    ),
    path(
        "<slug:slug>/tournaments/create/",
        tournament_create,
        name="tournament_create",
    ),
    path(
        "<slug:slug>/tournaments/<int:tournament_id>/edit/",
        tournament_edit,
        name="tournament_edit",
    ),
    path("<slug:slug>/rating/", dashboard_rating, name="dashboard_rating"),
    path(
        "<slug:slug>/rating/export/",
        dashboard_rating_export,
        name="dashboard_rating_export",
    ),
    path("<slug:slug>/fees/settings/", fees_settings, name="fees_settings"),
    path(
        "<slug:slug>/payments/settings/",
        payment_settings,
        name="payment_settings",
    ),
    path("<slug:slug>/fees/payments/", fees_payments, name="fees_payments"),
    path(
        "<slug:slug>/fees/payments/mark-paid/",
        fees_mark_paid,
        name="fees_mark_paid",
    ),
    path("<slug:slug>/invites/", invites_list, name="invites_list"),
    path(
        "<slug:slug>/join-requests/<int:pk>/approve/",
        join_request_approve,
        name="join_request_approve",
    ),
    path(
        "<slug:slug>/join-requests/<int:pk>/reject/",
        join_request_reject,
        name="join_request_reject",
    ),
    path(
        "<slug:slug>/invites/<int:pk>/deactivate/",
        invite_deactivate,
        name="invite_deactivate",
    ),
    path("<slug:slug>/managers/", managers_view, name="managers_list"),
    path(
        "<slug:slug>/managers/set-role/",
        manager_set_role,
        name="manager_set_role",
    ),
    path("<slug:slug>/subscription/", subscription_view, name="subscription"),
    path("<slug:slug>/subscription/pay/", subscription_pay, name="subscription_pay"),
    path(
        "<slug:slug>/subscription/return/",
        subscription_return,
        name="subscription_return",
    ),
    path(
        "<slug:slug>/notifications/",
        club_notification_config,
        name="club_notification_config",
    ),
    path(
        "<slug:slug>/interclub-applications/",
        interclub_applications,
        name="interclub_applications",
    ),
    path(
        "<slug:slug>/interclub-applications/<int:pk>/respond/",
        interclub_application_respond,
        name="interclub_application_respond",
    ),
    path(
        "<slug:slug>/tournaments/<int:tournament_id>/apply/",
        club_tournament_apply,
        name="club_tournament_apply",
    ),
    path("webhook/payment/", club_payment_webhook, name="club_payment_webhook"),
    path("webhook/club-fee/", club_fee_webhook, name="club_fee_webhook"),
]
