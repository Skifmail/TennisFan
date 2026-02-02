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
]
