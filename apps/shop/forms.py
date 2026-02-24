"""
Shop forms.
"""

from django import forms
from django.core.exceptions import ValidationError

from .models import PurchaseRequest


class PurchaseRequestForm(forms.ModelForm):
    """Форма заявки на покупку."""

    agree_legal = forms.BooleanField(
        required=True,
        label="Согласие на обработку персональных данных",
        error_messages={
            "required": "Необходимо согласиться с обработкой персональных данных."
        },
    )

    class Meta:
        model = PurchaseRequest
        fields = ("first_name", "last_name", "contact_phone", "comment")
        widgets = {
            "first_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Имя"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Фамилия"}
            ),
            "contact_phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Номер для связи"}
            ),
            "comment": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Комментарий (необязательно)",
                }
            ),
        }
        labels = {
            "first_name": "Имя",
            "last_name": "Фамилия",
            "contact_phone": "Номер для связи",
            "comment": "Комментарий",
        }

    def clean_agree_legal(self) -> bool:
        """Проверка обязательного согласия на обработку персональных данных."""
        if not self.cleaned_data.get("agree_legal"):
            raise ValidationError(
                "Необходимо согласиться с обработкой персональных данных."
            )
        return True
