"""
Уведомления «Оцените соперника» после подтверждения результата матча.
"""

from typing import cast

from django.urls import reverse

from apps.notifications.services import create_notification
from apps.tournaments.models import Match
from apps.tournaments.utils import get_match_participant_users


def get_rate_match_path(match_id: int) -> str:
    """Путь к странице оценки матча (для Notification.url)."""
    return cast(str, reverse("player_ratings:rate_match", args=[match_id]))


def get_rate_match_absolute_url(match_id: int) -> str:
    """Абсолютная ссылка (для кнопки в Telegram)."""
    from django.conf import settings

    path = get_rate_match_path(match_id)
    base = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", None) or ""
    if base:
        return base.rstrip("/") + path
    return path


def notify_players_to_rate_match(match: Match) -> None:
    """
    После подтверждения результата: уведомление на сайте и в бот ЛК
    со ссылкой на опрос оценки соперника.
    """
    users = get_match_participant_users(match)
    rate_path = get_rate_match_path(match.pk)
    rate_absolute = get_rate_match_absolute_url(match.pk)
    for user in users:
        create_notification(
            user=user,
            message=f"Матч №{match.pk}: оцените соперника",
            url=rate_path,
            event_key="match.rate_players",
        )

    from apps.telegram_bot import services as bot
    from apps.telegram_bot.notifications import send_to_user_by_user

    if not bot.is_configured():
        return
    text = (
        "⭐ <b>Оцените соперника</b>\n\n"
        f"Результат матча №{match.pk} внесён и применён. "
        "Пожалуйста, оцените соперника по 12 параметрам (анонимно, 1–10). "
        "Редактирование возможно в течение 24 часов."
    )
    reply_markup = {
        "inline_keyboard": [[{"text": "➡️ Перейти к опросу", "url": rate_absolute}]]
    }
    for user in users:
        send_to_user_by_user(
            user,
            text,
            reply_markup=reply_markup,
            event_key="match.rate_players",
        )
