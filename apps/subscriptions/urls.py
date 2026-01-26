from django.urls import path
from . import views

urlpatterns = [
    path('pricing/', views.pricing_page, name='pricing'),
    path('buy/<int:tier_id>/', views.buy_subscription, name='buy_subscription'),
]
