"""Размеченное письмо восстановления доступа без секретов в push и ЛК."""

from typing import Any, ClassVar

from django.contrib.auth.forms import PasswordResetForm
from django.template.loader import render_to_string
from loguru import logger

from .infrastructure.email import EventEmail


class NotificationPasswordResetForm(PasswordResetForm):
    """Подключает восстановление пароля к реестру каналов.

    Attributes:
        event_key: Ключ обязательного сервисного письма.
    """

    event_key: ClassVar[str] = "account.password_reset"

    def send_mail(
        self,
        subject_template_name: str,
        email_template_name: str,
        context: dict[str, Any],
        from_email: str | None,
        to_email: str,
        html_email_template_name: str | None = None,
    ) -> None:
        """Отправляет сервисное письмо, не копируя токен в другие каналы.

        Args:
            subject_template_name: Шаблон темы Django.
            email_template_name: Текстовый шаблон с секретной ссылкой.
            context: Контекст PasswordResetForm.
            from_email: Прежний отправитель.
            to_email: Подтверждённый формой адрес.
            html_email_template_name: Необязательный HTML-шаблон.
        """
        subject = "".join(render_to_string(subject_template_name, context).splitlines())
        message = EventEmail(
            event_key=self.event_key,
            subject=subject,
            body=render_to_string(email_template_name, context),
            from_email=from_email,
            to=[to_email],
        )
        if html_email_template_name:
            message.attach_alternative(
                render_to_string(html_email_template_name, context), "text/html"
            )
        try:
            message.send()
        except Exception:
            # Как и штатная форма Django, не раскрываем наличие аккаунта через сбой SMTP.
            logger.error(
                "Не удалось обработать сервисное письмо восстановления",
                event_key=self.event_key,
            )
