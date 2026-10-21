"""Почтовый адаптер с общей политикой и независимым источником события."""

from copy import copy
from typing import Any

from django.core.mail import EmailMultiAlternatives

from apps.users.models import User

from ..domain.catalog import EVENTS
from ..domain.rules import NotificationValidationError
from ..services import capture_email_event, center_enabled, event_policy, preferences


class EventEmail(EmailMultiAlternatives):
    """Сохраняет прежний SMTP-транспорт, добавляя разрешения конкретного события.

    Attributes:
        event_key: Явный код события, не тема или текст письма.
        capture_event: Создавать ли ЛК/push для почтового источника.
        notification_url: Относительный адрес раздела с подробностями для ЛК и push.
    """

    outbound_category: str = ""

    def __init__(
        self,
        *args: Any,
        event_key: str,
        capture_event: bool = True,
        delivery_allowed: bool = True,
        notification_url: str = "",
        **kwargs: Any,
    ) -> None:
        """Создаёт письмо с прежними аргументами Django.

        Args:
            *args: Позиционные аргументы письма Django.
            event_key: Ключ реестра.
            capture_event: Выключено у уже сформированной ручной рассылки.
            delivery_allowed: Дополнительное прежнее ограничение почтового канала.
            notification_url: Относительный адрес раздела с подробностями для ЛК и push.
            **kwargs: Прежние именованные параметры письма.

        Raises:
            NotificationValidationError: Неизвестное событие.
        """
        if event_key not in EVENTS:
            raise NotificationValidationError("У письма не зарегистрировано событие.")
        self.event_key = event_key
        self.capture_event = capture_event
        self.delivery_allowed = delivery_allowed
        self.notification_url = notification_url
        super().__init__(*args, **kwargs)

    def send(self, fail_silently: bool = False) -> int:
        """Отправляет каждому адресату отдельно с учётом согласия.

        Args:
            fail_silently: Прежнее поведение SMTP-ошибок.

        Returns:
            Число сообщений, принятых SMTP, а не гарантированно доставленных.
        """
        if not center_enabled():
            return int(super().send(fail_silently=fail_silently))
        policy = event_policy(self.event_key)
        sent = 0
        for recipient in self.recipients():
            user = User.objects.filter(email__iexact=recipient).first()
            if user is not None and not user.is_active:
                continue
            if user and self.capture_event:
                capture_email_event(
                    self.event_key,
                    user,
                    url=self.notification_url,
                )
            consent = preferences(user.pk)[1] if user else True
            if (
                not self.delivery_allowed
                or not policy.email_enabled
                or (not consent and not EVENTS[self.event_key].required_email)
            ):
                continue
            message = copy(self)
            message.to, message.cc, message.bcc = [recipient], [], []
            sent += int(
                EmailMultiAlternatives.send(message, fail_silently=fail_silently)
            )
        return sent


def send_event_mail(
    subject: str,
    message: str,
    from_email: str | None,
    recipient_list: list[str],
    *,
    event_key: str,
    fail_silently: bool = False,
    **kwargs: Any,
) -> int:
    """Совместимый адаптер коротких писем вместо неразмеченного send_mail.

    Args:
        subject: Тема письма.
        message: Обычный текст.
        from_email: Прежний отправитель.
        recipient_list: Адресаты.
        event_key: Код события.
        fail_silently: Прежнее поведение ошибок.
        **kwargs: Дополнительные параметры EmailMessage.

    Returns:
        Число принятых почтовым транспортом сообщений.
    """
    return EventEmail(
        subject=subject,
        body=message,
        from_email=from_email,
        to=recipient_list,
        event_key=event_key,
        **kwargs,
    ).send(fail_silently=fail_silently)
