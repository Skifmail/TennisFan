"""
Shop app URLs.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.shop_list, name="shop_list"),
    path("<int:pk>/", views.product_detail, name="shop_product_detail"),
    path("<int:product_id>/purchase/", views.purchase_request_create, name="shop_purchase_request"),
]
