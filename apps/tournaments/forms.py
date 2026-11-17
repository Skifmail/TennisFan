"""Формы приложения tournaments."""

from __future__ import annotations

from typing import Any

from django import forms

from config.validators import validate_image_max_2mb

from .models import Tournament, TournamentDuration


class TournamentRegistrationWindowForm(forms.Form):
    """Проверить ввод календаря при продлении регистрации и возобновлении турнира."""

    start_date = forms.DateField(
        label="Дата начала",
        required=False,
        widget=forms.DateInput(
            attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"
        ),
        error_messages={
            "required": "Укажите дату начала турнира.",
            "invalid": "Укажите корректную дату начала турнира.",
        },
    )
    start_time = forms.TimeField(
        label="Время начала",
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control"}, format="%H:%M"
        ),
        error_messages={
            "required": "Укажите время начала однодневного турнира.",
            "invalid": "Укажите корректное время начала турнира.",
        },
    )
    registration_deadline = forms.DateTimeField(
        label="Дедлайн регистрации",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "form-control"},
            format="%Y-%m-%dT%H:%M",
        ),
        error_messages={
            "required": "Укажите новый дедлайн регистрации.",
            "invalid": "Укажите корректные дату и время дедлайна регистрации.",
        },
    )

    def __init__(
        self,
        *args: Any,
        tournament: Tournament,
        reopening: bool = False,
        **kwargs: Any,
    ) -> None:
        """Сохранить назначенное время и потребовать его для однодневного события."""
        super().__init__(*args, **kwargs)
        self.fields["start_date"].required = reopening or tournament.start_date is None
        self.fields["start_time"].required = (
            tournament.duration == TournamentDuration.SINGLE_DAY
        )
        self.initial.setdefault("start_date", tournament.start_date)
        self.initial.setdefault("start_time", tournament.start_time)


class TournamentPhotoUploadForm(forms.Form):
    """Форма загрузки фото участником турнира."""

    image = forms.ImageField(
        label="Фото",
        validators=[validate_image_max_2mb],
        widget=forms.FileInput(
            attrs={
                "class": "form-control",
                "accept": "image/*",
            }
        ),
    )
    caption = forms.CharField(
        label="Подпись",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Необязательно",
            }
        ),
    )
