"""Формы заявки и работы координатора."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, cast

from django import forms
from django.forms import BaseFormSet, formset_factory

from .models import CampApplication, CampPackage, CampParticipant, CampSession


class PackageChoiceField(forms.ModelChoiceField):
    """Подписывает пакет названием и ценой с валютой."""

    def label_from_instance(self, obj: Any) -> str:
        """Возвращает понятную подпись варианта участия."""
        return f"{obj.name} — {obj.price_label}"


class CampApplicationForm(forms.Form):
    """Контакты и пакет без доступа к внутренним полям заявки."""

    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    package = PackageChoiceField(
        queryset=CampPackage.objects.none(),
        label="Пакет участия",
        empty_label="Выберите пакет",
    )
    contact_name = forms.CharField(
        label="Как к вам обращаться",
        max_length=160,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"})
    )
    phone = forms.CharField(
        label="Телефон",
        max_length=30,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )
    comment = forms.CharField(
        label="Пожелания и вопросы",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    consent = forms.BooleanField(
        label="Разрешаю обработать данные заявки и передать их команде кемпа TennisFan для организации участия."
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args: Any, session: CampSession, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        package_field = cast(forms.ModelChoiceField, self.fields["package"])
        package_field.queryset = session.packages.filter(is_active=True)

    def clean_phone(self) -> str:
        """Нормализует телефон с понятной ошибкой вместо молчаливого отказа."""
        phone = self.cleaned_data["phone"].strip()
        if (
            not re.fullmatch(r"\+?[\d\s()\-]+", phone)
            or not 7 <= len(re.sub(r"\D", "", phone)) <= 15
        ):
            raise forms.ValidationError(
                "Укажите телефон: от 7 до 15 цифр. Допустимы +, пробелы, скобки и дефисы."
            )
        return ("+" if phone.startswith("+") else "") + re.sub(r"\D", "", phone)

    def clean_website(self) -> str:
        """Отсекает автоматическое заполнение скрытого поля."""
        if self.cleaned_data.get("website"):
            raise forms.ValidationError(
                "Не удалось отправить заявку. Обновите страницу."
            )
        return ""


class ParticipantForm(forms.Form):
    """Один игрок либо сопровождающий; профили не выбираются из публичного списка."""

    name = forms.CharField(label="Имя и фамилия", max_length=160)
    role = forms.ChoiceField(label="Кто едет", choices=CampParticipant.Role.choices)
    age = forms.IntegerField(
        label="Возраст на начало заезда", min_value=1, max_value=110, required=False
    )
    level = forms.ChoiceField(
        label="Уровень игры",
        required=False,
        choices=[
            ("", "Не знаю / не играю"),
            ("beginner", "Начинающий"),
            ("amateur", "Любитель"),
            ("experienced", "Опытный игрок"),
        ],
    )


class ParticipantsBaseFormSet(BaseFormSet):
    """Требует хотя бы одного игрока и проверяет возраст на выбранный заезд."""

    def __init__(self, *args: Any, session: CampSession, **kwargs: Any) -> None:
        self.camp_session = session
        super().__init__(*args, **kwargs)

    def clean(self) -> None:
        """Проверяет состав поездки без сохранения частичной заявки."""
        super().clean()
        if any(self.errors):
            return
        players = 0
        for form in self.forms:
            row = form.cleaned_data
            if not row or row.get("DELETE"):
                continue
            if row["role"] == CampParticipant.Role.COMPANION:
                continue
            players += 1
            age = row.get("age")
            if row["role"] == CampParticipant.Role.CHILD and (age is None or age >= 18):
                form.add_error("age", "Для ребёнка укажите возраст младше 18 лет.")
            if (
                row["role"] == CampParticipant.Role.ADULT
                and age is not None
                and age < 18
            ):
                form.add_error("age", "Для участника младше 18 лет выберите «Ребёнок».")
            if age is None and self.camp_session.min_age < 18:
                form.add_error(
                    "age", "Укажите возраст, чтобы подобрать подходящую группу."
                )
            if (
                row["role"] == CampParticipant.Role.CHILD
                and self.camp_session.min_age >= 18
            ):
                form.add_error("role", "Этот заезд предназначен для взрослых игроков.")
            if age is not None and (
                age < self.camp_session.min_age
                or (
                    self.camp_session.max_age is not None
                    and age > self.camp_session.max_age
                )
            ):
                form.add_error("age", "Возраст не соответствует условиям этого заезда.")
        if not players:
            raise forms.ValidationError(
                "Добавьте хотя бы одного игрока. Сопровождающие едут вместе с участником программы."
            )


ParticipantFormSet = cast(
    type[ParticipantsBaseFormSet],
    formset_factory(
        ParticipantForm,
        formset=ParticipantsBaseFormSet,
        extra=0,
        min_num=1,
        validate_min=True,
        max_num=12,
        validate_max=True,
        absolute_max=12,
        can_delete=True,
    ),
)


class ApplicationManagementForm(forms.Form):
    """Разрешённые координатору изменения заявки."""

    status = forms.ChoiceField(label="Статус", choices=CampApplication.Status.choices)
    quoted_total = forms.DecimalField(
        label="Согласованная сумма за всю заявку",
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=2,
        required=False,
    )
    payment_due = forms.DateField(
        label="Срок доплаты",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    accommodation_notes = forms.CharField(
        label="Размещение и трансфер — только для команды",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    staff_notes = forms.CharField(
        label="Заметки — только для команды",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class PaymentForm(forms.Form):
    """Ручной учёт без списания денег с карты."""

    amount = forms.DecimalField(
        label="Сумма (возврат — со знаком минус)", max_digits=12, decimal_places=2
    )
    reference = forms.CharField(label="Основание / номер операции", max_length=180)
    operation_key = forms.UUIDField(widget=forms.HiddenInput)


class GuestRecoveryForm(forms.Form):
    """Запрашивает доступ только на адрес из исходной заявки."""

    email = forms.EmailField(
        label="Email из заявки",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )


class CancellationRequestForm(forms.Form):
    """Отмена всей заявки после явного подтверждения участника."""

    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    reason = forms.CharField(
        label="Почему хотите отменить поездку",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    confirm = forms.BooleanField(
        label="Прошу отменить участие всех людей в этой заявке. Понимаю, что решение и условия возврата подтвердит координатор."
    )


class CancellationDecisionForm(forms.Form):
    """Решение координатора без автоматического возврата денег."""

    request_id = forms.IntegerField(widget=forms.HiddenInput)
    decision = forms.ChoiceField(
        label="Решение по запросу",
        choices=[("approved", "Подтвердить отмену"), ("rejected", "Отклонить запрос")],
    )
    resolution = forms.CharField(
        label="Ответ участнику и дальнейшие действия",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ReservationForm(forms.Form):
    """Явный абсолютный срок предотвращает повторное продление от двойного POST."""

    expires_at = forms.DateTimeField(
        label="Временная бронь до",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
        help_text="Время платформы. Места резервируются для всех игроков заявки без оплаты.",
    )
