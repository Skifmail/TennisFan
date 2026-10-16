"""Закрытый постраничный журнал отправок без повторного запуска SMTP из админки."""

from typing import TYPE_CHECKING, Any, Final

from django.contrib import admin
from django.core.paginator import Paginator
from django.db.models import Model
from django.http import HttpRequest, HttpResponse

from .models import ChatInvitationCampaign

JOURNAL_PAGE_SIZE: Final = 25

if TYPE_CHECKING:
    _ModelAdmin = admin.ModelAdmin[Any]
else:
    _ModelAdmin = admin.ModelAdmin


@admin.register(ChatInvitationCampaign)
class ChatInvitationCampaignAdmin(_ModelAdmin):
    """Показывает авторство, аудиторию и постраничные результаты доставки.

    Attributes:
        change_form_template: Закрытая страница отдельной отправки.
        readonly_fields: Неизменяемые через административный интерфейс поля.
    """

    change_form_template = "admin/chat/invitation_campaign.html"
    list_display = (
        "room",
        "sender",
        "audience",
        "created_count",
        "status",
        "created_at",
    )
    readonly_fields = (
        "room",
        "sender",
        "audience",
        "target_id",
        "upper_user_id",
        "cursor_user_id",
        "estimated_count",
        "created_count",
        "status",
        "created_at",
        "finished_at",
    )

    def get_model_perms(self, request: HttpRequest) -> dict[str, bool]:
        """Оставляет журнал в карточке чата, не добавляя глобальный раздел.

        Args:
            request: Запрос сотрудника.

        Returns:
            Пустой набор пунктов меню.
        """
        return {}

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрещает создавать рассылку в обход подписанного предпросмотра.

        Args:
            request: Запрос сотрудника.

        Returns:
            Всегда False.
        """
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Запрещает менять аудиторию и результаты доставки задним числом.

        Args:
            request: Запрос сотрудника.
            obj: Выбранная отправка.

        Returns:
            Всегда False.
        """
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Не позволяет очищать журнал и терять защиту от повторного приглашения.

        Args:
            request: Запрос сотрудника.
            obj: Отправка приглашений.

        Returns:
            Всегда False; каскад при удалении комнаты контролируется её админкой.
        """
        return False

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Model | None = None,
    ) -> HttpResponse:
        """Загружает одну страницу приглашений, не раскрывая контакты в общем списке.

        Args:
            request: Запрос уполномоченного сотрудника.
            context: Контекст административной страницы.
            add: Служебный флаг создания.
            change: Служебный флаг редактирования.
            form_url: Служебный URL.
            obj: Выбранная отправка.

        Returns:
            Страница журнала с состояниями обоих каналов.
        """
        if isinstance(obj, ChatInvitationCampaign):
            context["invitation_page"] = Paginator(
                obj.invitations.select_related("recipient").order_by(
                    "created_at", "pk"
                ),
                JOURNAL_PAGE_SIZE,
            ).get_page(request.GET.get("page"))
            context["private_campaign"] = obj
        return super().render_change_form(request, context, add, change, form_url, obj)
