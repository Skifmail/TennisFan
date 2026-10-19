"""Минимальные настройки пользователя и короткая форма администратора."""

from typing import TYPE_CHECKING

from django import forms
from django.conf import settings

from .domain.rules import (
    NotificationValidationError,
    validate_content,
    validate_destination,
)
from .models import Broadcast


class PreferenceForm(forms.Form):
    """Предоставляет два согласия без таблицы событий и переключателя ЛК.

    Attributes:
        push_enabled: Согласие на события платформы в push.
        email_enabled: Согласие на обычные email, кроме доступа и восстановления.
    """

    push_enabled = forms.BooleanField(required=False, label="Push-уведомления")
    email_enabled = forms.BooleanField(required=False, label="Email-уведомления")


if TYPE_CHECKING:
    _BroadcastForm = forms.ModelForm[Broadcast]
else:
    _BroadcastForm = forms.ModelForm


class BroadcastForm(_BroadcastForm):
    """Проверяет лимиты и безопасный переход до сохранения черновика.

    Attributes:
        Meta: Только изменяемые поля черновика.
    """

    class Meta:
        model = Broadcast
        fields = ["title", "body", "url", "send_push", "send_email", "send_inbox"]
        widgets = {
            "title": forms.TextInput(attrs={"maxlength": 40, "data-counter": "40"}),
            "body": forms.Textarea(
                attrs={"maxlength": 120, "data-counter": "120", "rows": 3}
            ),
        }

    def clean(self) -> dict[str, object]:
        """Проверяет содержимое и наличие каналов независимо от ограничений HTML.

        Returns:
            Проверенные данные формы.
        """
        data = super().clean() or {}
        if "title" in data and "body" in data:
            try:
                data["title"], data["body"] = validate_content(
                    data["title"], data["body"]
                )
                data["url"] = validate_destination(
                    data.get("url", ""), settings.ONESIGNAL_SITE_ORIGIN
                )
                if not any(
                    data.get(field)
                    for field in ("send_push", "send_email", "send_inbox")
                ):
                    raise NotificationValidationError("Выберите хотя бы один канал.")
            except NotificationValidationError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return data
