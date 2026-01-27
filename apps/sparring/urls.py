"""
Sparring app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.sparring_list, name="sparring_list"),
    path("my/", views.sparring_my_requests, name="sparring_my_requests"),
    path("create/", views.sparring_create, name="sparring_create"),
    path("<int:pk>/edit/", views.sparring_edit, name="sparring_edit"),
    path("<int:pk>/delete/", views.sparring_delete, name="sparring_delete"),
    path("<int:pk>/cancel/", views.sparring_cancel, name="sparring_cancel"),
    path("<int:pk>/respond/", views.sparring_respond, name="sparring_respond"),
]
