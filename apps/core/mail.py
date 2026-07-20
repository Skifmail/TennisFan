"""Обёртка email-backend: логирует все исходящие письма в БД."""

from __future__ import annotations

import logging
from typing import Any, cast

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage
from django.utils import timezone

logger = logging.getLogger(__name__)


class LoggingEmailBackend(BaseEmailBackend):
    """Прокси над реальным EMAIL_BACKEND_INNER с записью в ``OutboundEmail``."""

    def __init__(self, fail_silently: bool = False, **kwargs: Any) -> None:
        """Инициализировать прокси и внутренний backend.

        Args:
            fail_silently (bool): Проглатывать ошибки отправки.
            **kwargs: Параметры для внутреннего backend.
        """
        super().__init__(fail_silently=fail_silently)
        inner_path = getattr(
            settings,
            "EMAIL_BACKEND_INNER",
            "django.core.mail.backends.smtp.EmailBackend",
        )
        self._inner = get_connection(
            backend=inner_path,
            fail_silently=fail_silently,
            **kwargs,
        )

    def open(self) -> bool | None:
        """Открыть соединение внутреннего backend."""
        return cast(bool | None, self._inner.open())

    def close(self) -> None:
        """Закрыть соединение внутреннего backend."""
        self._inner.close()

    def send_messages(self, email_messages: list[EmailMessage]) -> int:
        """Отправить письма и сохранить копии в журнале.

        Args:
            email_messages (list[EmailMessage]): Письма к отправке.

        Returns:
            int: Число успешно отправленных писем.
        """
        if not email_messages:
            return 0
        try:
            sent_count = int(self._inner.send_messages(email_messages) or 0)
        except Exception as exc:
            for message in email_messages:
                self._store_message(message, success=False, error=str(exc))
            if not self.fail_silently:
                raise
            return 0

        for message in email_messages:
            self._store_message(message, success=True, error="")
        return sent_count

    def _store_message(
        self,
        message: EmailMessage,
        *,
        success: bool,
        error: str,
    ) -> None:
        """Сохранить одно письмо в ``OutboundEmail``.

        Args:
            message (EmailMessage): Отправленное или упавшее письмо.
            success (bool): Успех отправки.
            error (str): Текст ошибки при неуспехе.
        """
        try:
            from django.contrib.auth import get_user_model

            from apps.core.models import OutboundEmail

            recipients = (
                list(message.to or [])
                + list(getattr(message, "cc", None) or [])
                + list(getattr(message, "bcc", None) or [])
            )
            if not recipients:
                recipients = [""]

            body_html = ""
            alternatives = getattr(message, "alternatives", None) or []
            for content, mimetype in alternatives:
                if mimetype == "text/html":
                    body_html = str(content)
                    break

            user_model = get_user_model()
            now = timezone.now()
            status = (
                OutboundEmail.Status.SENT if success else OutboundEmail.Status.FAILED
            )
            for to_email in recipients:
                to_norm = (to_email or "").strip().lower()
                user = None
                if to_norm and "@" in to_norm:
                    user = user_model.objects.filter(email__iexact=to_norm).first()
                OutboundEmail.objects.create(
                    user=user,
                    to_email=to_norm or (to_email or ""),
                    from_email=str(getattr(message, "from_email", "") or ""),
                    subject=str(getattr(message, "subject", "") or "")[:998],
                    body_text=str(getattr(message, "body", "") or ""),
                    body_html=body_html,
                    status=status,
                    error_message=(error or "")[:5000],
                    sent_at=now,
                )
        except Exception:
            logger.exception("Failed to store outbound email log")
