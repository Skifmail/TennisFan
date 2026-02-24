"""
URLs for stringers page.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.stringers, name="stringers"),
    path("<int:pk>/", views.stringer_detail, name="stringer_detail"),
    path("rate/", views.stringer_rate, name="stringer_rate"),
]
