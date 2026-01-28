"""
Training app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.training_list, name="training_list"),
    path("my/", views.my_trainings, name="my_trainings"),
    path("my/add/", views.training_add, name="training_add"),
    path("my/<int:pk>/edit/", views.training_edit, name="training_edit"),
    path("my/enrollments/<int:pk>/contact/<str:method>/", views.enrollment_contact, name="enrollment_contact"),
    path("my/enrollments/<int:pk>/delete/", views.enrollment_delete, name="enrollment_delete"),
    path("coaches/", views.coach_list, name="coach_list"),
    path("coaches/apply/", views.coach_application_create, name="coach_application_create"),
    path("coaches/apply/success/", views.coach_application_success, name="coach_application_success"),
    path("coaches/<str:slug>/", views.coach_detail, name="coach_detail"),
    path("<str:slug>/", views.training_detail, name="training_detail"),
    path("<str:slug>/enroll/", views.training_enroll, name="training_enroll"),
]
