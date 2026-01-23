"""
Tournaments app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path('', views.tournament_list, name='tournament_list'),
    path('champions-league/', views.champions_league, name='champions_league'),
    path('my/', views.my_matches, name='my_matches'),
    path('match/<int:pk>/propose/', views.propose_result, name='propose_result'),
    path('proposal/<int:pk>/confirm/', views.confirm_proposal, name='confirm_proposal'),
    path('<slug:slug>/register/', views.tournament_register, name='tournament_register'),
    path('<slug:slug>/', views.tournament_detail, name='tournament_detail'),
    path('match/<int:pk>/', views.match_detail, name='match_detail'),
]
