"""
Courts app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path('', views.court_list, name='court_list'),
    path('<str:slug>/', views.court_detail, name='court_detail'),
]
