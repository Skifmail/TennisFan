"""Публичная форма заявки стрингера и фотографии для модерации."""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from apps.core.contact_utils import (
    build_max_url,
    build_telegram_url,
    build_whatsapp_url,
    normalize_russian_phone,
)

from .models import StringerApplication, StringerApplicationPhoto


class StringerApplicationForm(forms.ModelForm):
    """Разрешает заявителю заполнить только сведения о компании и контактах."""

    class Meta:
        model = StringerApplication
        fields = (
            "name",
            "address",
            "price",
            "description",
            "contact_name",
            "email",
            "phone",
            "telegram",
            "whatsapp",
            "max_contact",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        self.fields["phone"].help_text = "Российский номер телефона с кодом +7."
        self.fields["email"].help_text = "Для связи с администрацией; не публикуется."
        self.fields["telegram"].help_text = "@username."

    def clean(self) -> dict[str, Any]:
        """Проверяет, что публичные контакты образуют безопасные рабочие ссылки."""
        data = cast(dict[str, Any], super().clean())
        validators = {
            "phone": normalize_russian_phone,
            "telegram": build_telegram_url,
            "whatsapp": build_whatsapp_url,
            "max_contact": build_max_url,
        }
        for field, validator in validators.items():
            value = data.get(field)
            if value and not validator(value):
                self.add_error(field, "Укажите корректный номер или профиль.")
        return data


StringerPhotoFormSet: type[
    BaseInlineFormSet[
        StringerApplicationPhoto,
        StringerApplication,
        forms.ModelForm[StringerApplicationPhoto],
    ]
] = inlineformset_factory(
    StringerApplication,
    StringerApplicationPhoto,
    fields=("image", "caption"),
    widgets={
        "image": forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": "image/*"}
        ),
        "caption": forms.TextInput(attrs={"class": "form-control"}),
    },
    extra=3,
    max_num=3,
    validate_max=True,
    absolute_max=3,
    can_delete=False,
)
