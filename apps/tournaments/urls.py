"""
Tournaments app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.tournament_list, name="tournament_list"),
    path("tables/", views.tournament_tables_list, name="tournament_tables_list"),
    path(
        "tables/<slug:slug>/",
        views.tournament_tables_detail,
        name="tournament_tables_detail",
    ),
    path("champions-league/", views.champions_league, name="champions_league"),
    path("my/", views.my_matches, name="my_matches"),
    path("my/sparring/", views.my_sparring_matches, name="my_sparring_matches"),
    path("match/<int:pk>/propose/", views.propose_result, name="propose_result"),
    path("proposal/<int:pk>/confirm/", views.confirm_proposal, name="confirm_proposal"),
    path(
        "<slug:slug>/register/", views.tournament_register, name="tournament_register"
    ),
    path(
        "<slug:slug>/register/required/",
        views.tournament_register_required,
        name="tournament_register_required",
    ),
    path(
        "<slug:slug>/register/doubles/",
        views.tournament_register_doubles,
        name="tournament_register_doubles",
    ),
    path(
        "<slug:slug>/join-team/<int:team_id>/",
        views.tournament_join_team,
        name="tournament_join_team",
    ),
    path(
        "<slug:slug>/manage/",
        views.tournament_manage,
        name="tournament_manage",
    ),
    path(
        "<slug:slug>/manage/compose-pair/",
        views.tournament_manage_compose_pair,
        name="tournament_manage_compose_pair",
    ),
    path(
        "<slug:slug>/manage/generate-groups/",
        views.tournament_manage_generate_groups,
        name="tournament_manage_generate_groups",
    ),
    path(
        "<slug:slug>/manage/generate-playoffs/",
        views.tournament_manage_generate_playoffs,
        name="tournament_manage_generate_playoffs",
    ),
    path(
        "<slug:slug>/manage/intermediate-results/",
        views.tournament_manage_intermediate_results,
        name="tournament_manage_intermediate_results",
    ),
    path(
        "<slug:slug>/manage/intermediate-results/generate-main/",
        views.tournament_manage_intermediate_generate_main,
        name="tournament_manage_intermediate_generate_main",
    ),
    path(
        "<slug:slug>/manage/intermediate-results/generate-consolation/",
        views.tournament_manage_intermediate_generate_consolation,
        name="tournament_manage_intermediate_generate_consolation",
    ),
    path(
        "<slug:slug>/manage/finalize/",
        views.tournament_manage_finalize,
        name="tournament_manage_finalize",
    ),
    path(
        "<slug:slug>/manage/match/<int:pk>/result/",
        views.tournament_manage_match_result,
        name="tournament_manage_match_result",
    ),
    path(
        "<slug:slug>/manage/search-participants/",
        views.tournament_manage_search_participants,
        name="tournament_manage_search_participants",
    ),
    path(
        "<slug:slug>/manage/add-participant/",
        views.tournament_manage_add_participant,
        name="tournament_manage_add_participant",
    ),
    path(
        "<slug:slug>/manage/add-participant/<int:player_id>/confirm/",
        views.tournament_manage_add_participant_confirm,
        name="tournament_manage_add_participant_confirm",
    ),
    path(
        "<slug:slug>/manage/add-participant/force/",
        views.tournament_manage_add_participant_force,
        name="tournament_manage_add_participant_force",
    ),
    path(
        "<slug:slug>/manage/add-participant/send-payment-notification/",
        views.tournament_manage_send_payment_notification,
        name="tournament_manage_send_payment_notification",
    ),
    path(
        "<slug:slug>/manage/remove-participant/",
        views.tournament_manage_remove_participant,
        name="tournament_manage_remove_participant",
    ),
    path("<slug:slug>/", views.tournament_detail, name="tournament_detail"),
    path("match/<int:pk>/", views.match_detail, name="match_detail"),
]
