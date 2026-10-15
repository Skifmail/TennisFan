"""Модерация городских чатов в существующей панели администратора."""

from django.contrib import admin
from django.http import HttpRequest

from .models import ChatMembership, ChatMessage, ChatReport, ChatRoom


@admin.register(ChatRoom)
class ChatRoomAdmin(admin.ModelAdmin):
    """Управляет доступностью комнат и закреплёнными правилами."""

    list_display = ("city", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("city__name", "city__region")
    autocomplete_fields = ("city",)


@admin.register(ChatMembership)
class ChatMembershipAdmin(admin.ModelAdmin):
    """Назначает модераторов и рассматривает ограничения участников."""

    list_display = ("user", "room", "is_active", "is_moderator", "banned_until")
    list_filter = ("is_active", "is_moderator")
    search_fields = ("user__email", "room__city__name")
    autocomplete_fields = ("user", "room")
    readonly_fields = ("joined_at", "last_read_message_id")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    """Показывает историю без обхода сервисных правил редактирования."""

    list_display = ("id", "room", "author", "created_at", "deleted_at")
    search_fields = ("text", "room__city__name")
    list_filter = ("room",)
    readonly_fields = (
        "room",
        "author",
        "text",
        "client_id",
        "reply_to",
        "created_at",
        "edited_at",
        "deleted_at",
    )
    actions = None

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрещает создавать сообщения в обход сервисного слоя.

        Args:
            request: Запрос администратора.

        Returns:
            Всегда False: отправка выполняется через сервис.
        """
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: ChatMessage | None = None
    ) -> bool:
        """Сохраняет журнал и мягкое удаление вместо удаления строк.

        Args:
            request: Запрос администратора.
            obj: Проверяемое сообщение или весь список.

        Returns:
            Всегда False: удаление текста выполняется через сервис.
        """
        return False


@admin.register(ChatReport)
class ChatReportAdmin(admin.ModelAdmin):
    """Обрабатывает жалобы с сохранением исходных данных заявителя."""

    list_display = ("id", "message", "reporter", "is_resolved", "created_at")
    list_filter = ("is_resolved",)
    readonly_fields = ("message", "reporter", "reason", "created_at")
