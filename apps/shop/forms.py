"""
Shop forms.
"""

from django import forms

from .models import PurchaseRequest


class PurchaseRequestForm(forms.ModelForm):
    """Форма заявки на покупку."""

    class Meta:
        model = PurchaseRequest
        fields = ("first_name", "last_name", "contact_phone", "comment")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Имя"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Фамилия"}),
            "contact_phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Номер для связи"}),
            "comment": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Комментарий (необязательно)"}),
        }
        labels = {
            "first_name": "Имя",
            "last_name": "Фамилия",
            "contact_phone": "Номер для связи",
            "comment": "Комментарий",
        }
