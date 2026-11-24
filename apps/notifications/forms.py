"""Минимальные настройки пользователя и короткая форма администратора."""

from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.conf import settings

from apps.users.display import format_user_admin_label
from apps.users.models import Gender, Player, User

from .domain.rules import (
    BODY_LIMIT,
    TITLE_LIMIT,
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


class AudienceSelect(forms.SelectMultiple):
    """Подключает локальные стили и поиск Select2 из комплекта админки."""

    i18n_name = "ru"

    @property
    def media(self) -> forms.Media:
        """Подключить локальный Select2 без AJAX-атрибутов AutocompleteMixin."""
        suffix = "" if settings.DEBUG else ".min"
        return forms.Media(
            js=[
                f"admin/js/vendor/jquery/jquery{suffix}.js",
                f"admin/js/vendor/select2/select2.full{suffix}.js",
                f"admin/js/vendor/select2/i18n/{self.i18n_name}.js",
                "admin/js/jquery.init.js",
                "admin/js/autocomplete.js",
            ],
            css={
                "screen": [
                    f"admin/css/vendor/select2/select2{suffix}.css",
                    "admin/css/autocomplete.css",
                ]
            },
        )


class BroadcastForm(_BroadcastForm):
    """Проверяет лимиты и безопасный переход до сохранения черновика.

    Attributes:
        Meta: Только изменяемые поля черновика.
    """

    cities = forms.MultipleChoiceField(
        widget=AudienceSelect,
        label="Города",
        required=False,
        help_text="Можно выбрать несколько городов.",
    )
    recipient_ids = forms.TypedMultipleChoiceField(
        widget=AudienceSelect, label="Пользователи", coerce=int, required=False
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if "cities" in self.fields:
            cities = (
                Player.objects.filter(user__is_active=True)
                .exclude(city="")
                .order_by("city")
                .values_list("city", flat=True)
                .distinct()
            )
            cast(forms.MultipleChoiceField, self.fields["cities"]).choices = [
                (city, city) for city in cities
            ]
        if "recipient_ids" in self.fields:
            users = (
                User.objects.filter(is_active=True)
                .only("pk", "first_name", "last_name", "email")
                .order_by("last_name", "first_name", "pk")
            )
            cast(
                forms.TypedMultipleChoiceField, self.fields["recipient_ids"]
            ).choices = [(user.pk, format_user_admin_label(user)) for user in users]
        if "audience_mode" in self.fields:
            self.fields["audience_mode"].required = False
            cast(forms.ChoiceField, self.fields["gender"]).choices = [
                ("", "Любой пол"),
                *Gender.choices,
            ]

    class Meta:
        model = Broadcast
        fields = [
            "title",
            "body",
            "url",
            "send_push",
            "send_email",
            "send_inbox",
            "audience_mode",
            "cities",
            "gender",
            "recipient_ids",
        ]
        widgets = {
            "title": forms.TextInput(
                attrs={"maxlength": TITLE_LIMIT, "data-counter": str(TITLE_LIMIT)}
            ),
            "body": forms.Textarea(
                attrs={
                    "maxlength": BODY_LIMIT,
                    "data-counter": str(BODY_LIMIT),
                    "rows": 3,
                }
            ),
        }

    def clean(self) -> dict[str, object]:
        """Проверяет содержимое и наличие каналов независимо от ограничений HTML.

        Returns:
            Проверенные данные формы.
        """
        data = super().clean() or {}
        mode = data.get("audience_mode") or self.instance.audience_mode
        data["audience_mode"] = mode
        if mode == "filters" and not (data.get("cities") or data.get("gender")):
            self.add_error("cities", "Выберите хотя бы один город или укажите пол.")
        if mode == "selected" and not data.get("recipient_ids"):
            self.add_error("recipient_ids", "Выберите хотя бы одного пользователя.")
        if mode != "filters":
            data.update(cities=[], gender="")
        if mode != "selected":
            data["recipient_ids"] = []
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
