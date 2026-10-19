"""Почтовый адаптер приглашений с журналированием штатным backend платформы."""

from typing import Final

from django.urls import reverse

from apps.core.email_service import _get_site_base_url, _send_html_email

from .models import ChatInvitation

SMTP_TIMEOUT_SECONDS: Final = 10.0


def send_invitation_email(invitation: ChatInvitation) -> bool:
    """Отправляет одному игроку нейтральное приглашение и ссылку отключения писем.

    Args:
        invitation: Приглашение, уже зарезервированное фоновой очередью.

    Returns:
        Принял ли почтовый backend сообщение, без обещания доставки во входящие.
    """
    from .invitations import invitation_email_token

    base = _get_site_base_url()
    return _send_html_email(
        subject=f"TennisFan: приглашение в чат «{invitation.room.display_name}»",
        template_name="emails/chat_invitation.html",
        context={
            "city_label": str(invitation.room),
            "invitation_url": base + reverse("chat:invitation", args=[invitation.pk]),
            "unsubscribe_url": base
            + reverse(
                "chat:invitation_email_settings",
                args=[invitation_email_token(user_id=invitation.recipient_id)],
            ),
        },
        recipient=invitation.recipient.email,
        category="other",
        timeout_seconds=SMTP_TIMEOUT_SECONDS,
        event_key="chat.invited",
    )
