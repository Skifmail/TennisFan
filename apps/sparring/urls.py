"""
Sparring app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path('', views.sparring_list, name='sparring_list'),
    path('create/', views.sparring_create, name='sparring_create'),
]
