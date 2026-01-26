from django.urls import path
from . import views

urlpatterns = [
    path('donate/', views.donate_view, name='donate'),
    path('preview/', views.payment_preview, name='payment_preview'),
    path('process/', views.payment_process, name='payment_process'),
]
