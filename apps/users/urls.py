"""
Users app URLs.
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('auth/', views.auth, name='auth'),
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('profile/<int:pk>/', views.profile, name='profile'),
    path('profile/edit/', views.profile_edit, name='profile_edit'),
    path('notifications/', views.notifications, name='notifications'),
    path('ntrp-test/', views.ntrp_test, name='ntrp_test'),
    path('ntrp/save/', views.save_ntrp, name='save_ntrp'),
]
