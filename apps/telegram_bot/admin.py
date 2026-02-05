"""
Админка раздела «Телеграм»: рассылки, привязки пользователей, запросы на продление дедлайна.
"""

import logging
from datetime import timedelta

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.tournaments.models import DeadlineExtensionRequest, Match

from . import services as bot
from .models import (
    DeadlineExtensionRequestProxy,
    TelegramBroadcast,
    UserTelegramLinkProxy,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Рассылки в Telegram
# ---------------------------------------------------------------------------


@admin.register(TelegramBroadcast)
class TelegramBroadcastAdmin(admin.ModelAdmin):
    """Рассылка: текст и опционально фото — отправка всем пользователям бота при сохранении."""

    list_display = ("text_short", "has_image", "sent_at", "created_at", "created_by")
    list_filter = ("sent_at",)
    search_fields = ("text",)
    readonly_fields = ("sent_at", "created_at", "created_by")

    fieldsets = (
        (
            None,
            {
                "fields": ("text", "image"),
                "description": "Текст поддерживает HTML: <b>, <i>, <a href=\"...\">. "
                "Если указано фото — отправляется пост с подписью.",
            },
        ),
        ("Служебные", {"fields": ("sent_at", "created_at", "created_by"), "classes": ("collapse",)}),
    )

    def text_short(self, obj):
        return ((obj.text or "")[:60] + "…") if (obj.text and len(obj.text) > 60) else (obj.text or "—")

    text_short.short_description = "Текст"

    def has_image(self, obj):
        return bool(obj.image)

    has_image.boolean = True
    has_image.short_description = "Фото"

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.sent_at:
            return list(super().get_readonly_fields(request, obj)) + ["text", "image"]
        return super().get_readonly_fields(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        if not obj.sent_at and (obj.text or "").strip():
            if not bot.is_configured():
                messages.error(
                    request,
                    "Пользовательский бот не настроен (TELEGRAM_USER_BOT_TOKEN). Рассылка не отправлена.",
                )
                super().save_model(request, obj, form, change)
                return
            links = UserTelegramLink.objects.filter(
                user_bot_chat_id__isnull=False
            ).exclude(user_bot_chat_id=0)
            total = links.count()
            sent = 0
            for link in links:
                try:
                    if obj.image:
                        photo_arg = None
                        if hasattr(obj.image, "read"):
                            raw = obj.image.read()
                            photo_arg = raw
                            if hasattr(obj.image, "seek"):
                                obj.image.seek(0)
                        else:
                            photo_arg = getattr(obj.image, "url", None) or getattr(
                                obj.image, "path", None
                            )
                        if photo_arg is not None:
                            _, ok = bot.send_photo(
                                link.user_bot_chat_id,
                                photo_arg,
                                caption=(obj.text or "").strip() or None,
                            )
                        else:
                            ok = False
                    else:
                        ok = bot.send_to_user(link.user_bot_chat_id, (obj.text or "").strip())
                    if ok:
                        sent += 1
                except Exception as e:
                    logger.warning("Broadcast to %s failed: %s", link.user_bot_chat_id, e)
            obj.sent_at = timezone.now()
            messages.success(
                request,
                f"Рассылка отправлена: {sent} из {total} пользователей с привязанным Telegram.",
            )
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        """Редирект на страницу просмотра созданной рассылки (избегаем ID "add" в URL)."""
        return redirect(
            reverse(
                "admin:telegram_bot_telegrambroadcast_change",
                args=[obj.pk],
            )
        )


# ---------------------------------------------------------------------------
# Привязки Telegram (прокси из core)
# ---------------------------------------------------------------------------


@admin.register(UserTelegramLinkProxy)
class UserTelegramLinkProxyAdmin(admin.ModelAdmin):
    """Привязки пользователей к Telegram (отображаются в разделе «Телеграм»)."""

    list_display = ("user", "telegram_chat_id", "user_bot_chat_id", "has_binding_token", "created_at")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at", "token_created_at")

    def has_binding_token(self, obj):
        return bool(obj.binding_token)

    has_binding_token.boolean = True
    has_binding_token.short_description = "Токен привязки"


# ---------------------------------------------------------------------------
# Запросы на продление дедлайна (прокси из tournaments)
# ---------------------------------------------------------------------------


@admin.action(description="Одобрить (+24 ч)")
def approve_extension_action(modeladmin, request, queryset):
    """Продлить дедлайн матча на 24 часа и отметить запрос как одобренный."""
    now = timezone.now()
    count = 0
    for ext in queryset.filter(status=DeadlineExtensionRequest.Status.PENDING):
        match = ext.match
        if match.status != Match.MatchStatus.SCHEDULED:
            continue
        if match.deadline:
            match.deadline = match.deadline + timedelta(hours=24)
        else:
            match.deadline = now + timedelta(hours=24)
        match.save(update_fields=["deadline"])
        ext.status = DeadlineExtensionRequest.Status.APPROVED
        ext.processed_at = now
        ext.save(update_fields=["status", "processed_at"])
        try:
            from apps.telegram_bot import notifications as tg

            tg.notify_extension_approved(ext)
        except Exception:
            pass
        count += 1
    if count:
        messages.success(request, f"Одобрено запросов: {count}. Дедлайн продлён на 24 ч.")
    else:
        messages.warning(request, "Нет запросов для одобрения (или матчи уже завершены).")


@admin.register(DeadlineExtensionRequestProxy)
class DeadlineExtensionRequestProxyAdmin(admin.ModelAdmin):
    """Запросы на продление дедлайна матча (из кнопки в Telegram-боте)."""

    list_display = ("match", "requested_by", "status", "created_at", "processed_at")
    list_filter = ("status",)
    search_fields = ("match__tournament__name", "requested_by__user__email")
    actions = [approve_extension_action]
    raw_id_fields = ("match", "requested_by")
    readonly_fields = ("created_at",)
