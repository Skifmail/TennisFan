"""
Courts app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.court_list, name="court_list"),
    path("apply/", views.court_application_create, name="court_application_create"),
    path("apply/success/", views.court_application_success, name="court_application_success"),
    path("<str:slug>/", views.court_detail, name="court_detail"),
]
