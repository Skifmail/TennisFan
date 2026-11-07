"""
Админка раздела «Телеграм»: рассылки, привязки пользователей, запросы на продление дедлайна.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse

from apps.tournaments.deadline_extensions import approve_deadline_extension
from apps.tournaments.models import DeadlineExtensionRequest
from apps.users.models import User

from . import services as bot
from .delivery import request_broadcast
from .models import (
    DeadlineExtensionRequestProxy,
    TelegramBroadcast,
    UserTelegramLinkProxy,
)

if TYPE_CHECKING:
    _BroadcastAdmin = admin.ModelAdmin[TelegramBroadcast]
    _LinkAdmin = admin.ModelAdmin[UserTelegramLinkProxy]
    _ExtensionAdmin = admin.ModelAdmin[DeadlineExtensionRequestProxy]
else:
    _BroadcastAdmin = _LinkAdmin = _ExtensionAdmin = admin.ModelAdmin

# ---------------------------------------------------------------------------
# Рассылки в Telegram
# ---------------------------------------------------------------------------


@admin.register(TelegramBroadcast)
class TelegramBroadcastAdmin(_BroadcastAdmin):
    """Рассылка: текст и опционально фото — отправка всем пользователям бота при сохранении."""

    list_display = (
        "text_short",
        "has_image",
        "status",
        "delivery_summary",
        "sent_at",
        "created_at",
        "created_by",
    )
    list_filter = ("status",)
    search_fields = ("text",)
    readonly_fields = (
        "status",
        "delivery_summary",
        "prepared_at",
        "finished_at",
        "sent_at",
        "created_at",
        "created_by",
    )

    fieldsets = (
        (
            None,
            {
                "fields": ("text", "image"),
                "description": 'Текст поддерживает HTML: <b>, <i>, <a href="...">. '
                "Если указано фото — отправляется пост с подписью. "
                "Отправка только по кнопке «Сохранить» — случайное нажатие Enter не отправит форму.",
            },
        ),
        (
            "Служебные",
            {
                "fields": (
                    "status",
                    "delivery_summary",
                    "prepared_at",
                    "finished_at",
                    "sent_at",
                    "created_at",
                    "created_by",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    class Media:
        js = ("js/admin_broadcast.js",)

    @admin.display(description="Текст")
    def text_short(self, obj: TelegramBroadcast) -> str:
        """Показать короткий текст рассылки."""
        return (
            ((obj.text or "")[:60] + "…")
            if (obj.text and len(obj.text) > 60)
            else (obj.text or "—")
        )

    @admin.display(boolean=True, description="Фото")
    def has_image(self, obj: TelegramBroadcast) -> bool:
        """Показать наличие фото в хранилище."""
        return bool(obj.image)

    @admin.display(description="Доставка")
    def delivery_summary(self, obj: TelegramBroadcast) -> str:
        """Не смешивать принятие Telegram с ошибками, пропусками и неопределённостью."""
        if not obj.pk:
            return "—"
        from django.db.models import Count

        counts = dict(
            obj.deliveries.values("status")
            .annotate(total=Count("pk"))
            .values_list("status", "total")
        )
        return (
            f"Принято: {counts.get('accepted', 0)}/{sum(counts.values())}; "
            f"ошибки: {counts.get('failed', 0)}; неизвестно: {counts.get('uncertain', 0)}; "
            f"пропущено: {counts.get('skipped', 0)}"
        )

    def get_readonly_fields(
        self, request: HttpRequest, obj: TelegramBroadcast | None = None
    ) -> list[str] | tuple[str, ...]:
        """Запретить редактировать содержимое уже поставленной рассылки."""
        if obj and obj.status != TelegramBroadcast.Status.DRAFT:
            return list(super().get_readonly_fields(request, obj)) + ["text", "image"]
        return super().get_readonly_fields(request, obj)

    def save_model(
        self,
        request: HttpRequest,
        obj: TelegramBroadcast,
        form: ModelForm[TelegramBroadcast],
        change: bool,
    ) -> None:
        """Сохранить подготовку рассылки без сетевых вызовов и daemon-потока."""
        if not change:
            obj.created_by = cast(User, request.user)
        super().save_model(request, obj, form, change)
        if request_broadcast(obj.pk):
            obj.refresh_from_db()
            messages.success(
                request,
                "Рассылка сохранена в очереди. Доставку выполняет process_notifications; "
                "статусы показывают приём Telegram, а не прочтение.",
            )
            if not bot.is_configured():
                messages.warning(
                    request,
                    "API бота отключён или токен не настроен. Задания сохранены, но отправка пока приостановлена.",
                )

    def response_post_save_add(
        self, request: HttpRequest, obj: TelegramBroadcast
    ) -> HttpResponseRedirect:
        """
        Редирект сразу после создания — на страницу просмотра рассылки по pk.
        Иначе Django подставляет get_full_path().replace("/add/", "/<pk>/"); если в пути
        нет слэша после add (например .../add), подстановка не срабатывает и редирект
        ведёт на .../add/, что обрабатывается как object_id="add" → «объект не существует».
        """
        pk = getattr(obj, "pk", None)
        if pk is not None and isinstance(pk, int):
            path = reverse(
                "admin:telegram_bot_telegrambroadcast_change",
                args=[pk],
            )
            return HttpResponseRedirect(path)
        return super().response_post_save_add(request, obj)

    def response_add(
        self,
        request: HttpRequest,
        obj: TelegramBroadcast,
        post_url_continue: str | None = None,
    ) -> HttpResponse:
        """Дублируем явный редирект по pk на случай другого пути вызова."""
        pk = getattr(obj, "pk", None)
        if pk is not None and isinstance(pk, int):
            path = reverse(
                "admin:telegram_bot_telegrambroadcast_change",
                args=[pk],
            )
            return HttpResponseRedirect(path)
        return super().response_add(request, obj, post_url_continue)


# ---------------------------------------------------------------------------
# Привязки Telegram (прокси из core)
# ---------------------------------------------------------------------------


@admin.register(UserTelegramLinkProxy)
class UserTelegramLinkProxyAdmin(_LinkAdmin):
    """Привязки пользователей к Telegram (отображаются в разделе «Телеграм»)."""

    list_display = (
        "user",
        "telegram_chat_id",
        "user_bot_chat_id",
        "has_binding_token",
        "created_at",
    )
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at", "token_created_at")

    @admin.display(boolean=True, description="Токен привязки")
    def has_binding_token(self, obj: UserTelegramLinkProxy) -> bool:
        """Показать наличие одноразового токена, не раскрывая его содержимое."""
        return bool(obj.binding_token)


# ---------------------------------------------------------------------------
# Запросы на продление дедлайна (прокси из tournaments)
# ---------------------------------------------------------------------------


@admin.action(description="Одобрить (+24 ч)")
def approve_extension_action(
    modeladmin: admin.ModelAdmin[DeadlineExtensionRequestProxy],
    request: HttpRequest,
    queryset: QuerySet[DeadlineExtensionRequestProxy],
) -> None:
    """Продлить дедлайн матча на 24 часа и отметить запрос как одобренный."""
    count = 0
    for ext in queryset.filter(status=DeadlineExtensionRequest.Status.PENDING):
        count += int(approve_deadline_extension(ext.pk))
    if count:
        messages.success(
            request, f"Одобрено запросов: {count}. Дедлайн продлён на 24 ч."
        )
    else:
        messages.warning(
            request, "Нет запросов для одобрения (или матчи уже завершены)."
        )


@admin.register(DeadlineExtensionRequestProxy)
class DeadlineExtensionRequestProxyAdmin(_ExtensionAdmin):
    """Запросы на продление дедлайна матча (из кнопки в Telegram-боте)."""

    list_display = ("match", "requested_by", "status", "created_at", "processed_at")
    list_filter = ("status",)
    search_fields = ("match__tournament__name", "requested_by__user__email")
    actions = [approve_extension_action]
    raw_id_fields = ("match", "requested_by")
    readonly_fields = ("created_at",)
