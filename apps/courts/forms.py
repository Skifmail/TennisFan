"""
Courts forms.
"""

from django import forms

from .models import CourtApplication, CourtRating


class CourtRatingForm(forms.ModelForm):
    """Форма оценки корта (1–5 звёзд). Только для авторизованных пользователей."""

    class Meta:
        model = CourtRating
        fields = ("score",)
        widgets = {
            "score": forms.RadioSelect(choices=[(i, str(i)) for i in range(1, 6)]),
        }


class CourtApplicationForm(forms.ModelForm):
    """Форма заявки на добавление корта. Поля совпадают с админкой ручного добавления."""

    class Meta:
        model = CourtApplication
        fields = (
            "applicant_name",
            "applicant_email",
            "applicant_phone",
            "name",
            "city",
            "address",
            "description",
            "surface",
            "courts_count",
            "has_lighting",
            "is_indoor",
            "phone",
            "whatsapp",
            "website",
            "image",
            "latitude",
            "longitude",
            "price_per_hour",
        )
        widgets = {
            "applicant_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "ФИО или название организации"}
            ),
            "applicant_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "email@example.com"}
            ),
            "applicant_phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "+7XXXXXXXXXX"}
            ),
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Название корта или клуба"}
            ),
            "city": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Город"}
            ),
            "address": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Улица, дом"}
            ),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Краткое описание"}
            ),
            "surface": forms.Select(attrs={"class": "form-control"}),
            "courts_count": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 99}
            ),
            "has_lighting": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "is_indoor": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Телефон корта"}
            ),
            "whatsapp": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "+7XXXXXXXXXX"}
            ),
            "website": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://..."}
            ),
            "image": forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "latitude": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "any",
                    "placeholder": "55.7558",
                }
            ),
            "longitude": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "any",
                    "placeholder": "37.6173",
                }
            ),
            "price_per_hour": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01",
                    "min": 0,
                    "placeholder": "1500",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["applicant_phone"].required = False
        self.fields["description"].required = False
        self.fields["phone"].required = False
        self.fields["whatsapp"].required = False
        self.fields["website"].required = False
        self.fields["image"].required = False
        self.fields["latitude"].required = False
        self.fields["longitude"].required = False
        self.fields["price_per_hour"].required = False
