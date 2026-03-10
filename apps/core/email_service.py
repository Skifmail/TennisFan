"""
Сервис отправки email-уведомлений.

Модуль содержит функции для отправки писем пользователям: приветственное письмо
при регистрации, уведомления и др.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags

if TYPE_CHECKING:
    from apps.users.models import User

logger = logging.getLogger(__name__)


def _get_site_base_url() -> str:
    """Получить базовый URL сайта для ссылок в письмах.

    Args:
        None: Функция не принимает аргументов.

    Returns:
        str: Базовый URL сайта (например, ``https://tennisfan.ru``).
    """
    base_url = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", "").strip()
    if not base_url:
        domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru")
        base_url = f"https://{domain}"
    return base_url.rstrip("/")


def send_welcome_email(user: User) -> bool:
    """Отправить приветственное письмо новому пользователю после регистрации.

    Args:
        user (User): Объект пользователя, которому отправляется письмо.

    Returns:
        bool: ``True``, если письмо отправлено успешно, ``False`` в противном случае.
    """
    email = (getattr(user, "email", "") or "").strip()
    if not email or "@" not in email:
        logger.warning("send_welcome_email: user %s has no valid email", user.pk)
        return False

    base_url = _get_site_base_url()

    player = getattr(user, "player", None)
    profile_url = ""
    if player:
        try:
            profile_url = base_url + reverse("profile", kwargs={"pk": player.pk})
        except Exception:
            profile_url = base_url + "/profile/"

    context = {
        "user": user,
        "user_name": user.get_full_name() or user.email.split("@")[0],
        "base_url": base_url,
        "profile_url": profile_url,
        "pricing_url": base_url + reverse("pricing"),
        "tournaments_url": base_url + reverse("tournament_list"),
        "telegram_bot_url": getattr(settings, "TELEGRAM_PUBLIC_COMMUNITY_URL", "")
        or "https://t.me/TennisFanu",
        "support_email": getattr(settings, "DEFAULT_FROM_EMAIL", "info@tennisfan.ru"),
    }

    subject = "Добро пожаловать в TennisFan!"

    try:
        html_content = render_to_string("emails/welcome.html", context)
        text_content = strip_tags(html_content)
    except Exception as exc:
        logger.exception("send_welcome_email: template rendering failed: %s", exc)
        return False

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru")

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        logger.info("send_welcome_email: sent to %s (user %s)", email, user.pk)
        return True
    except Exception as exc:
        logger.exception("send_welcome_email: failed to send to %s: %s", email, exc)
        return False
