from django.urls import path

from . import views

urlpatterns = [
    path("", views.legal_index, name="legal_index"),
    path("personal-data/", views.legal_document, {"slug": "personal-data"}, name="legal_personal_data"),
    path("privacy/", views.legal_document, {"slug": "privacy"}, name="legal_privacy"),
    path("offer/", views.legal_document, {"slug": "offer"}, name="legal_offer"),
    path("terms/", views.legal_document, {"slug": "terms"}, name="legal_terms"),
]
