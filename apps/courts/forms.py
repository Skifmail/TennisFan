"""
Courts forms.
"""

from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .models import Court, CourtApplication, CourtRating
from .surfaces import CourtSurface, normalize_surface_codes


def _unique_court_slug(name: str, *, exclude_pk: int | None = None) -> str:
    """Собрать уникальный slug корта из названия (с поддержкой кириллицы)."""
    base = slugify(name, allow_unicode=True) or "court"
    candidate = base
    suffix = 0
    while True:
        qs = Court.objects.filter(slug=candidate)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        if not qs.exists():
            return candidate
        suffix += 1
        candidate = f"{base}-{suffix}"


def _normalize_website(value: str) -> str:
    """Добавить https:// к сайту без схемы, чтобы URLField не валил всю форму."""
    url = (value or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = f"https://{url}"
    return url


class SurfaceCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    """Чекбоксы канонических покрытий для заявки и админки."""

    option_inherits_attrs = False

    def __init__(self, attrs: dict[str, str] | None = None) -> None:
        classes = "surface-checkboxes"
        merged = dict(attrs or {})
        extra_class = merged.pop("class", "")
        merged["class"] = f"{classes} {extra_class}".strip()
        super().__init__(attrs=merged)

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        """Пометить каждый чекбокс классом формы, не копируя класс обёртки."""
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        option["attrs"]["class"] = "form-checkbox"
        return option


def _surface_multiple_choice(label: str) -> forms.MultipleChoiceField:
    """Поле выбора одного или нескольких покрытий."""
    return forms.MultipleChoiceField(
        label=label,
        choices=CourtSurface.choices,
        widget=SurfaceCheckboxSelectMultiple(),
        required=False,
    )


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

    indoor_surfaces = _surface_multiple_choice("Покрытие крытых кортов")
    outdoor_surfaces = _surface_multiple_choice("Покрытие открытых кортов")

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
            "indoor_surfaces",
            "outdoor_surfaces",
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
                attrs={
                    "class": "form-control",
                    "placeholder": "Город, село, деревня, пгт…",
                }
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
        self.fields["phone"].required = False
        self.fields["whatsapp"].required = False
        self.fields["website"].required = False
        self.fields["image"].required = False
        self.fields["latitude"].required = False
        self.fields["longitude"].required = False
        self.fields["price_per_hour"].required = False
        if self.instance and self.instance.pk:
            self.initial["indoor_surfaces"] = self.instance.indoor_surfaces or []
            self.initial["outdoor_surfaces"] = self.instance.outdoor_surfaces or []

    def clean_website(self) -> str:
        """Нормализовать сайт без схемы."""
        return _normalize_website(str(self.cleaned_data.get("website") or ""))

    def clean_agree_legal(self) -> bool:
        """Проверка обязательного согласия на обработку персональных данных."""
        if not self.cleaned_data.get("agree_legal"):
            raise ValidationError(
                "Необходимо согласиться с обработкой персональных данных."
            )
        return True

    def clean_indoor_surfaces(self) -> list[str]:
        """Оставить только канонические коды крытых покрытий."""
        return normalize_surface_codes(self.cleaned_data.get("indoor_surfaces"))

    def clean_outdoor_surfaces(self) -> list[str]:
        """Оставить только канонические коды открытых покрытий."""
        return normalize_surface_codes(self.cleaned_data.get("outdoor_surfaces"))

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
        indoor_surfaces = list(cleaned_data.get("indoor_surfaces") or [])
        outdoor_surfaces = list(cleaned_data.get("outdoor_surfaces") or [])
        if is_indoor and not indoor_surfaces:
            self.add_error(
                "indoor_surfaces",
                "Выберите покрытие для крытых кортов.",
            )
        if is_outdoor and not outdoor_surfaces:
            self.add_error(
                "outdoor_surfaces",
                "Выберите покрытие для открытых кортов.",
            )
        if not is_indoor:
            cleaned_data["indoor_surfaces"] = []
        if not is_outdoor:
            cleaned_data["outdoor_surfaces"] = []
        return cleaned_data


class CourtAdminForm(forms.ModelForm):
    """Форма корта в админке: покрытия выбираются чекбоксами."""

    indoor_surfaces = _surface_multiple_choice("Покрытие крытых кортов")
    outdoor_surfaces = _surface_multiple_choice("Покрытие открытых кортов")

    class Meta:
        model = Court
        exclude = ("surface",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # JS prepopulated_fields для кириллицы даёт пустой slug → ошибка и потеря фото.
        self.fields["slug"].required = False
        self.fields["slug"].help_text = (
            "Если пусто — заполнится автоматически из названия (кириллица поддерживается)."
        )
        if self.instance and self.instance.pk:
            self.initial["indoor_surfaces"] = self.instance.indoor_surfaces or []
            self.initial["outdoor_surfaces"] = self.instance.outdoor_surfaces or []

    def clean_indoor_surfaces(self) -> list[str]:
        """Оставить только канонические коды крытых покрытий."""
        return normalize_surface_codes(self.cleaned_data.get("indoor_surfaces"))

    def clean_outdoor_surfaces(self) -> list[str]:
        """Оставить только канонические коды открытых покрытий."""
        return normalize_surface_codes(self.cleaned_data.get("outdoor_surfaces"))

    def clean_website(self) -> str:
        """Нормализовать сайт без схемы (example.com → https://example.com)."""
        return _normalize_website(str(self.cleaned_data.get("website") or ""))

    def clean(self) -> dict[str, Any]:
        """Подставить slug из названия, если админ оставил поле пустым."""
        cleaned_data = cast(dict[str, Any], super().clean())
        name = str(cleaned_data.get("name") or "").strip()
        slug = str(cleaned_data.get("slug") or "").strip()
        if not slug and name:
            exclude_pk = (
                self.instance.pk if self.instance and self.instance.pk else None
            )
            cleaned_data["slug"] = _unique_court_slug(name, exclude_pk=exclude_pk)
        elif slug:
            cleaned_data["slug"] = slugify(slug, allow_unicode=True) or slug
        return cleaned_data


class CourtApplicationAdminForm(forms.ModelForm):
    """Форма заявки в админке: покрытия выбираются чекбоксами."""

    indoor_surfaces = _surface_multiple_choice("Покрытие крытых кортов")
    outdoor_surfaces = _surface_multiple_choice("Покрытие открытых кортов")

    class Meta:
        model = CourtApplication
        exclude = ("surface",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial["indoor_surfaces"] = self.instance.indoor_surfaces or []
            self.initial["outdoor_surfaces"] = self.instance.outdoor_surfaces or []

    def clean_indoor_surfaces(self) -> list[str]:
        """Оставить только канонические коды крытых покрытий."""
        return normalize_surface_codes(self.cleaned_data.get("indoor_surfaces"))

    def clean_outdoor_surfaces(self) -> list[str]:
        """Оставить только канонические коды открытых покрытий."""
        return normalize_surface_codes(self.cleaned_data.get("outdoor_surfaces"))

    def clean_website(self) -> str:
        """Нормализовать сайт без схемы."""
        return _normalize_website(str(self.cleaned_data.get("website") or ""))
