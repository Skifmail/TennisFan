"""
Content app URLs - Gallery.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.gallery_list, name="gallery_list"),
    path(
        "tournament/<slug:slug>/",
        views.tournament_gallery_detail,
        name="tournament_gallery_detail",
    ),
    path("<slug:slug>/", views.gallery_detail, name="gallery_detail"),
]
