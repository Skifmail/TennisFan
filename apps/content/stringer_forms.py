"""Публичная форма заявки стрингера и фотографии для модерации."""

from __future__ import annotations

import re
from typing import Any, cast

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from apps.core.contact_utils import normalize_russian_phone

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
            field.error_messages["required"] = f"Заполните поле «{field.label}»."
            field.error_messages["max_length"] = (
                "Слишком длинное значение: максимум %(limit_value)s символов."
            )
            field.widget.attrs["data-label"] = str(field.label)
            if not field.required:
                field.help_text = "Необязательно."
        self.fields["email"].error_messages[
            "invalid"
        ] = "Введите email в формате name@example.ru."
        phone_hint = "Номер РФ: +7 999 123-45-67 или 8 999 123-45-67."
        phone_error = "Введите полный номер РФ, например +7 999 123-45-67. Один знак «+» не является номером."
        for name in ("phone", "whatsapp"):
            self.fields[name].help_text = (
                "Необязательно. " if name == "whatsapp" else ""
            ) + phone_hint
            self.fields[name].widget.attrs.update(
                {
                    "inputmode": "tel",
                    "placeholder": "+7 999 123-45-67",
                    "pattern": r"(?=(?:[^0-9]*[0-9]){10,11}[^0-9]*$)\+?[0-9 \(\)\-]+",
                    "data-format-error": phone_error,
                }
            )
        self.fields["email"].help_text = "Для связи с администрацией; не публикуется."
        self.fields["telegram"].help_text = (
            "Необязательно. @username или https://t.me/username; 5–32 латинские буквы, цифры и знак _."
        )
        self.fields["telegram"].widget.attrs.update(
            {
                "pattern": r"(?:https?://t\.me/|@)?[A-Za-z][A-Za-z0-9_]{4,31}",
                "data-format-error": "Укажите @username или https://t.me/username: 5–32 латинские буквы, цифры и знак _, начало — буква.",
            }
        )
        self.fields["max_contact"].help_text = (
            "Необязательно. Скопируйте ссылку на профиль из MAX, например https://max.ru/username."
        )
        self.fields["max_contact"].widget.attrs.update(
            {
                "pattern": r"https?://(?:[A-Za-z0-9]+\.)?max\.ru/[^\s]+",
                "data-format-error": "Укажите полную ссылку на профиль MAX: https://max.ru/username.",
            }
        )

    def clean(self) -> dict[str, Any]:
        """Проверяет, что публичные контакты образуют безопасные рабочие ссылки."""
        data = cast(dict[str, Any], super().clean())
        for name in ("phone", "whatsapp"):
            value = data.get(name)
            if value:
                if not re.fullmatch(
                    r"\+?[0-9 \(\)\-]+", value
                ) or not normalize_russian_phone(value):
                    self.add_error(
                        name, self.fields[name].widget.attrs["data-format-error"]
                    )
                else:
                    data[name] = normalize_russian_phone(value)
        telegram = data.get("telegram")
        if telegram:
            if not re.fullmatch(
                r"(?:https?://t\.me/|@)?[A-Za-z][A-Za-z0-9_]{4,31}", telegram
            ):
                self.add_error(
                    "telegram",
                    self.fields["telegram"].widget.attrs["data-format-error"],
                )
            else:
                data["telegram"] = telegram.rsplit("/", 1)[-1].lstrip("@")
        max_contact = data.get("max_contact")
        if max_contact:
            if not re.fullmatch(
                r"https?://(?:[A-Za-z0-9]+\.)?max\.ru/[^\s]+", max_contact
            ):
                self.add_error(
                    "max_contact",
                    self.fields["max_contact"].widget.attrs["data-format-error"],
                )
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
