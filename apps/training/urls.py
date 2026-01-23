"""
Training app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path('', views.training_list, name='training_list'),
    path('coaches/', views.coach_list, name='coach_list'),
    path('coaches/<slug:slug>/', views.coach_detail, name='coach_detail'),
    path('<slug:slug>/', views.training_detail, name='training_detail'),
    path('<slug:slug>/enroll/', views.training_enroll, name='training_enroll'),
]
