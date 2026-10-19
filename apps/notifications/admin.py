"""Матрица каналов, подтверждение рассылок и наблюдаемая очередь Django Admin."""

import hashlib
import json
from typing import TYPE_CHECKING, Any, cast

from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Model, Q
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import URLPattern, path, reverse
from django.utils import timezone

from apps.users.models import User

from .domain.rules import NotificationValidationError
from .forms import BroadcastForm
from .models import Broadcast, Delivery, EventPolicy, NotificationPreference, PushDevice
from .services import center_enabled, event_policy, queue_broadcast


def _fingerprint(campaign: Broadcast) -> str:
    content = [
        campaign.pk,
        campaign.title,
        campaign.body,
        campaign.url,
        campaign.send_push,
        campaign.send_email,
        campaign.send_inbox,
    ]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()


def _counts(campaign: Broadcast) -> dict[str, int]:
    users = User.objects.filter(is_active=True)
    policy = event_policy("manual.broadcast")
    return {
        "ЛК": users.count() if campaign.send_inbox and policy.inbox_enabled else 0,
        "Email": (
            users.filter(
                Q(notificationpreference__isnull=True)
                | Q(notificationpreference__email_enabled=True)
            ).count()
            if campaign.send_email and policy.email_enabled
            else 0
        ),
        "Push": (
            users.filter(
                notificationpreference__push_enabled=True,
                pushdevice__active=True,
                pushdevice__subscription_id__isnull=False,
                pushdevice__expires_at__gt=timezone.now(),
            )
            .distinct()
            .count()
            if campaign.send_push and policy.push_enabled
            else 0
        ),
    }


if TYPE_CHECKING:
    _PolicyAdmin = admin.ModelAdmin[EventPolicy]
    _BroadcastAdmin = admin.ModelAdmin[Broadcast]
    _AuditAdmin = admin.ModelAdmin[Model]
else:
    _PolicyAdmin = _BroadcastAdmin = _AuditAdmin = admin.ModelAdmin


@admin.register(EventPolicy)
class EventPolicyAdmin(_PolicyAdmin):
    """Показывает события строками, каналы — редактируемыми столбцами.

    Attributes:
        list_display: Название, раздел и три независимых разрешения.
        list_editable: Массовая настройка чекбоксов с журналом Django Admin.
    """

    list_display = ["title", "group", "email_enabled", "push_enabled", "inbox_enabled"]
    list_editable = ["email_enabled", "push_enabled", "inbox_enabled"]
    search_fields = ["key", "title", "description"]
    list_filter = ["group", "email_enabled", "push_enabled", "inbox_enabled"]
    readonly_fields = ["key", "title", "group", "description"]
    actions = None

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрещает события, не подключённые кодом к реальным производителям.

        Args:
            request: Запрос администратора.

        Returns:
            False: реестр пополняется проверенной миграцией.
        """
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: EventPolicy | None = None
    ) -> bool:
        """Сохраняет политики, отключение выполняется чекбоксами.

        Args:
            request: Запрос администратора.
            obj: Выбранное событие.

        Returns:
            False: события нельзя удалить из реестра.
        """
        return False


@admin.register(Broadcast)
class BroadcastAdmin(_BroadcastAdmin):
    """Разделяет редактирование черновика, личный тест и массовое подтверждение.

    Attributes:
        form: Ограниченная и валидируемая форма.
        change_form_template: Ссылки на предпросмотр и персональный тест.
    """

    form = BroadcastForm
    change_form_template = "admin/notifications/broadcast_change.html"
    list_display = [
        "title",
        "status",
        "is_test",
        "created_by",
        "confirmed_by",
        "created_at",
    ]
    list_filter = ["status", "is_test"]
    search_fields = ["title"]
    readonly_fields = [
        "status",
        "created_by",
        "confirmed_by",
        "confirmed_at",
        "is_test",
        "target_user",
        "created_at",
    ]
    fields = [
        "title",
        "body",
        "url",
        "send_push",
        "send_email",
        "send_inbox",
        *readonly_fields,
    ]
    actions = None

    class Media:
        """Подключает счётчики символов без внешних библиотек.

        Attributes:
            js: Локальный скрипт счётчиков.
        """

        js = ["js/notification_admin.js"]

    def get_urls(self) -> list[URLPattern]:
        """Регистрирует подтверждение и личный тест под защитой admin_view.

        Returns:
            URL предпросмотра и теста, затем штатные маршруты модели.
        """
        return [
            path(
                "<int:pk>/send/",
                self.admin_site.admin_view(self.send_view),
                name="notifications_broadcast_send",
            ),
            path(
                "<int:pk>/test/",
                self.admin_site.admin_view(self.test_view),
                name="notifications_broadcast_test",
            ),
            path(
                "<int:pk>/cancel/",
                self.admin_site.admin_view(self.cancel_view),
                name="notifications_broadcast_cancel",
            ),
            *super().get_urls(),
        ]

    def get_readonly_fields(
        self, request: HttpRequest, obj: Broadcast | None = None
    ) -> list[str]:
        """Запрещает изменение подтверждённого текста и каналов.

        Args:
            request: Запрос администратора.
            obj: Редактируемая рассылка.

        Returns:
            Все поля при выходе из состояния черновика.
        """
        return (
            [str(field) for field in self.fields]
            if obj and obj.status != "draft"
            else [str(field) for field in self.readonly_fields]
        )

    def has_delete_permission(
        self, request: HttpRequest, obj: Broadcast | None = None
    ) -> bool:
        """Не позволяет удалять историю уже подтверждённых отправок.

        Args:
            request: Запрос администратора.
            obj: Рассылка, если выбрана.

        Returns:
            False: вместо удаления используются черновики и журнал.
        """
        return False

    def save_model(
        self, request: HttpRequest, obj: Broadcast, form: BroadcastForm, change: bool
    ) -> None:
        """Сохраняет автора и повторно проверяет состояние перед записью.

        Args:
            request: Авторизованный запрос.
            obj: Черновик.
            form: Проверенная форма.
            change: Обновляется существующая запись.

        Raises:
            PermissionDenied: Рассылка уже подтверждена конкурентным запросом.
        """
        with transaction.atomic():
            if (
                change
                and Broadcast.objects.select_for_update().get(pk=obj.pk).status
                != "draft"
            ):
                raise PermissionDenied
            if not change:
                obj.created_by = cast(User, request.user)
            super().save_model(request, obj, form, change)

    def _authorize(self, request: HttpRequest) -> None:
        if not cast(User, request.user).has_perm("notifications.send_broadcast"):
            raise PermissionDenied

    def send_view(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Показывает аудиторию и требует подписанный снимок для массовой отправки.

        Args:
            request: GET предпросмотра либо CSRF-защищённый POST подтверждения.
            pk: ID сохранённого черновика.

        Returns:
            Экран подтверждения либо возврат к рассылке без немедленной сетевой отправки.
        """
        self._authorize(request)
        campaign = get_object_or_404(Broadcast, pk=pk)
        if request.method not in {"GET", "POST"}:
            return HttpResponse(status=405)
        if request.method == "POST":
            try:
                expected = signing.loads(
                    request.POST.get("snapshot", ""),
                    salt="notification-broadcast",
                    max_age=300,
                )
                with transaction.atomic():
                    campaign = Broadcast.objects.select_for_update().get(pk=pk)
                    if request.POST.get("confirm") != "yes" or expected != _fingerprint(
                        campaign
                    ):
                        return HttpResponseBadRequest(
                            "Содержимое изменилось. Повторите предпросмотр."
                        )
                    queue_broadcast(campaign.pk, actor=cast(User, request.user))
            except (signing.BadSignature, NotificationValidationError) as exc:
                return HttpResponseBadRequest(str(exc))
            self.log_change(request, campaign, "Подтверждена массовая рассылка")
            self.message_user(
                request,
                "Рассылка поставлена в очередь. Accepted означает принятие транспортом, а не прочтение.",
                messages.SUCCESS,
            )
            return redirect("admin:notifications_broadcast_change", pk)
        context: dict[str, Any] = {
            **self.admin_site.each_context(request),
            "title": "Подтверждение рассылки",
            "campaign": campaign,
            "counts": _counts(campaign),
            "snapshot": signing.dumps(
                _fingerprint(campaign), salt="notification-broadcast"
            ),
            "enabled": center_enabled(),
            "opts": self.model._meta,
        }
        return render(request, "admin/notifications/broadcast_confirm.html", context)

    def test_view(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Ставит копию сообщения только текущему администратору, не аудитории.

        Args:
            request: Только POST с CSRF-токеном.
            pk: Исходный черновик, который тест не изменяет.

        Returns:
            Возврат к исходному черновику.
        """
        self._authorize(request)
        if request.method != "POST":
            return HttpResponse(status=405)
        source = get_object_or_404(Broadcast, pk=pk, status="draft")
        test = Broadcast.objects.create(
            title=source.title,
            body=source.body,
            url=source.url,
            send_push=source.send_push,
            send_email=source.send_email,
            send_inbox=source.send_inbox,
            is_test=True,
            target_user=request.user,
            created_by=request.user,
        )
        try:
            queue_broadcast(test.pk, actor=cast(User, request.user))
        except NotificationValidationError as exc:
            return HttpResponseBadRequest(str(exc))
        self.log_change(request, source, f"Личный тест: рассылка #{test.pk}")
        self.message_user(
            request,
            "Личный тест поставлен только вам; ваши согласия и разрешения устройств учитываются.",
            messages.SUCCESS,
        )
        return redirect(reverse("admin:notifications_broadcast_change", args=[pk]))

    def cancel_view(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Останавливает только будущие задания после явного подтверждения.

        Args:
            request: GET подтверждения или POST остановки с CSRF.
            pk: Рассылка, которую нужно остановить.

        Returns:
            Подтверждение либо возврат в журнал; принятые провайдером сообщения не отзываются.
        """
        self._authorize(request)
        campaign = get_object_or_404(Broadcast, pk=pk)
        if request.method == "GET":
            return render(
                request,
                "admin/notifications/broadcast_cancel.html",
                {
                    **self.admin_site.each_context(request),
                    "campaign": campaign,
                    "title": "Остановка рассылки",
                },
            )
        if request.method != "POST":
            return HttpResponse(status=405)
        if request.POST.get("confirm") != "yes":
            return HttpResponseBadRequest("Подтвердите остановку.")
        with transaction.atomic():
            Broadcast.objects.select_for_update().filter(pk=pk).get()
            Broadcast.objects.filter(pk=pk).update(status="cancelled")
        self.log_change(request, campaign, "Остановлены будущие задания рассылки")
        self.message_user(
            request,
            "Очередь рассылки остановлена. Уже принятые или выполняемые сообщения могут быть доставлены.",
        )
        return redirect("admin:notifications_broadcast_change", pk)


class NotificationAuditAdmin(_AuditAdmin):
    """Предоставляет наблюдение без ручной смены согласий и статуса очереди.

    Attributes:
        actions: Массовые изменения запрещены.
    """

    actions = None

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрещает создание технических записей вручную.

        Args:
            request: Запрос администратора.

        Returns:
            False: записи создаются сервисами.
        """
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: object | None = None
    ) -> bool:
        """Сохраняет только штатное право просмотра.

        Args:
            request: Запрос администратора.
            obj: Техническая запись.

        Returns:
            False: согласия нельзя подменить из админки.
        """
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: object | None = None
    ) -> bool:
        """Сохраняет историю доставки и пользовательского выбора.

        Args:
            request: Запрос администратора.
            obj: Техническая запись.

        Returns:
            False: запись журнала не удаляется вручную.
        """
        return False


@admin.register(Delivery)
class DeliveryAdmin(NotificationAuditAdmin):
    """Показывает результаты доставки с фильтрами по событию и состоянию.

    Attributes:
        list_display: Безопасные сведения о попытке и результате.
    """

    list_display = [
        "created_at",
        "event_key",
        "user",
        "channel",
        "status",
        "attempts",
        "provider_id",
        "error_code",
    ]
    list_filter = ["channel", "status", "event_key"]
    search_fields = ["user__email", "provider_id", "event_key"]


@admin.register(PushDevice)
class PushDeviceAdmin(NotificationAuditAdmin):
    """Показывает действующие устройства без секретного cookie.

    Attributes:
        list_display: Владелец, активность и срок привязки.
    """

    list_display = ["user", "active", "expires_at", "updated_at"]
    list_filter = ["active"]
    search_fields = ["user__email"]
    exclude = ["device_hash"]


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(NotificationAuditAdmin):
    """Показывает два согласия, не позволяя администратору включить их за пользователя.

    Attributes:
        list_display: Пользователь и состояния согласий.
    """

    list_display = ["user", "push_enabled", "email_enabled", "updated_at"]
    list_filter = ["push_enabled", "email_enabled"]
    search_fields = ["user__email"]
