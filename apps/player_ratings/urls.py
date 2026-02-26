from django.urls import path

from . import views

app_name = "player_ratings"

urlpatterns = [
    path("matches/<int:match_id>/rate/", views.rate_match, name="rate_match"),
    path("players/<int:player_id>/skills/", views.player_skills, name="player_skills"),
]
