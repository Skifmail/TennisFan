"""
URLs for stringers page.
"""

from django.urls import path

from . import views
from .stringer_views import stringer_application

urlpatterns = [
    path("", views.stringers, name="stringers"),
    path("apply/", stringer_application, name="stringer_application"),
    path("<int:pk>/", views.stringer_detail, name="stringer_detail"),
    path("rate/", views.stringer_rate, name="stringer_rate"),
]
