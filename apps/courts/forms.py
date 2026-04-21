"""
Courts forms.
"""

from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError

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

    agree_legal = forms.BooleanField(
        required=True,
        label="Согласие на обработку персональных данных",
        error_messages={
            "required": "Необходимо согласиться с обработкой персональных данных."
        },
    )

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
            "indoor_surface",
            "outdoor_surface",
            "courts_count",
            "has_lighting",
            "is_indoor",
            "is_outdoor",
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
                attrs={
                    "class": "form-control",
                    "placeholder": "ФИО или название организации",
                }
            ),
            "applicant_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "email@example.com"}
            ),
            "applicant_phone": forms.TextInput(
                attrs={
                    "class": "form-control js-phone-input",
                    "placeholder": "+7",
                    "autocomplete": "tel",
                }
            ),
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Название корта или клуба",
                }
            ),
            "city": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Город"}
            ),
            "address": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Улица, дом"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Краткое описание",
                }
            ),
            "surface": forms.TextInput(
                attrs={"class": "form-control", "type": "hidden"}
            ),
            "indoor_surface": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Например: хард, грунт, трава",
                    "maxlength": "100",
                }
            ),
            "outdoor_surface": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Например: хард, грунт, трава",
                    "maxlength": "100",
                }
            ),
            "courts_count": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 99}
            ),
            "has_lighting": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "is_indoor": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "is_outdoor": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "phone": forms.TextInput(
                attrs={
                    "class": "form-control js-phone-input",
                    "placeholder": "+7",
                    "autocomplete": "tel",
                }
            ),
            "whatsapp": forms.TextInput(
                attrs={
                    "class": "form-control js-phone-input",
                    "placeholder": "+7",
                    "autocomplete": "tel",
                }
            ),
            "website": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://..."}
            ),
            "image": forms.FileInput(
                attrs={"class": "form-control", "accept": "image/*"}
            ),
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
        self.fields["surface"].required = False
        self.fields["indoor_surface"].required = False
        self.fields["outdoor_surface"].required = False
        self.fields["phone"].required = False
        self.fields["whatsapp"].required = False
        self.fields["website"].required = False
        self.fields["image"].required = False
        self.fields["latitude"].required = False
        self.fields["longitude"].required = False
        self.fields["price_per_hour"].required = False

    def clean_agree_legal(self) -> bool:
        """Проверка обязательного согласия на обработку персональных данных."""
        if not self.cleaned_data.get("agree_legal"):
            raise ValidationError(
                "Необходимо согласиться с обработкой персональных данных."
            )
        return True

    def clean(self) -> dict[str, Any]:
        """Проверить согласованность выбранных характеристик корта.

        Args:
            None: Используются данные формы из ``self.cleaned_data``.

        Returns:
            dict: Очищенные данные формы.

        Raises:
            ValidationError: Если не выбран ни один формат площадки.
        """
        cleaned_data = cast(dict[str, Any], super().clean())
        is_indoor = bool(cleaned_data.get("is_indoor"))
        is_outdoor = bool(cleaned_data.get("is_outdoor"))
        if not is_indoor and not is_outdoor:
            raise ValidationError(
                "Выберите минимум один формат площадки: «Крытый» и/или «Открытый»."
            )
        indoor_surface = (cleaned_data.get("indoor_surface") or "").strip()
        outdoor_surface = (cleaned_data.get("outdoor_surface") or "").strip()
        if is_indoor and not indoor_surface:
            self.add_error(
                "indoor_surface",
                "Укажите покрытие для крытых кортов.",
            )
        if is_outdoor and not outdoor_surface:
            self.add_error(
                "outdoor_surface",
                "Укажите покрытие для открытых кортов.",
            )

        parts: list[str] = []
        if indoor_surface:
            parts.append(f"Крытые: {indoor_surface}")
        if outdoor_surface:
            parts.append(f"Открытые: {outdoor_surface}")
        cleaned_data["surface"] = "; ".join(parts) if parts else ""
        return cleaned_data
