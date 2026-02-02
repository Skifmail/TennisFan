"""
Tournaments app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path('', views.tournament_list, name='tournament_list'),
    path('tables/', views.tournament_tables_list, name='tournament_tables_list'),
    path('tables/<slug:slug>/', views.tournament_tables_detail, name='tournament_tables_detail'),
    path('champions-league/', views.champions_league, name='champions_league'),
    path('my/', views.my_matches, name='my_matches'),
    path('match/<int:pk>/propose/', views.propose_result, name='propose_result'),
    path('proposal/<int:pk>/confirm/', views.confirm_proposal, name='confirm_proposal'),
    path('<slug:slug>/register/', views.tournament_register, name='tournament_register'),
    path('<slug:slug>/register/doubles/', views.tournament_register_doubles, name='tournament_register_doubles'),
    path('<slug:slug>/join-team/<int:team_id>/', views.tournament_join_team, name='tournament_join_team'),
    path('<slug:slug>/', views.tournament_detail, name='tournament_detail'),
    path('match/<int:pk>/', views.match_detail, name='match_detail'),
]
