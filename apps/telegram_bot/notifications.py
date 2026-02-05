"""
Уведомления пользователей в Telegram (пользовательский бот).
Отправка по User или chat_id; тексты для регистрации, матча, предложения результата.
"""

import logging

from apps.core.models import UserTelegramLink
from apps.tournaments.utils import get_match_opponent_users, get_match_participant_users

from . import services as bot

logger = logging.getLogger(__name__)


def get_chat_id_for_user(user) -> int | None:
    """chat_id для пользовательского бота (None если пользователь ещё не нажал /start в чате с ботом)."""
    if not user:
        return None
    try:
        link = user.telegram_link
        return link.user_bot_chat_id if link.user_bot_chat_id else None
    except (AttributeError, UserTelegramLink.DoesNotExist):
        return None


def send_to_user_by_user(user, text: str, reply_markup: dict | None = None) -> bool:
    """Отправить сообщение пользователю по User (если привязан Telegram)."""
    chat_id = get_chat_id_for_user(user)
    if chat_id is None:
        return False
    return bot.send_to_user(chat_id, text, reply_markup=reply_markup)


def _get_site_base_url() -> str:
    from django.conf import settings
    base = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", None) or ""
    if base:
        return base.rstrip("/") + "/"
    return "http://localhost:8000/" if settings.DEBUG else "https://tennisfan.ru/"


def notify_tournament_registered(user, tournament) -> None:
    """Уведомление о регистрации на турнир."""
    if not bot.is_configured():
        return
    deadline = tournament.registration_deadline
    deadline_str = f"до {deadline.strftime('%d.%m.%Y')}" if deadline else "в ближайшее время"
    text = (
        f"🎾 <b>Вы зарегистрированы на турнир</b>\n\n"
        f"«{tournament.name}» ({tournament.city})\n\n"
        f"Ожидайте формирования сетки {deadline_str}. "
        f"Мы пришлём уведомление о ваших матчах в этом турнире."
    )
    send_to_user_by_user(user, text)


def _match_info_text(match) -> str:
    """Текст с информацией о матче для уведомления (без ссылки на сайт)."""
    side1 = match.get_player1_display()
    side2 = match.get_player2_display()
    deadline_str = match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else "не указан"
    return (
        f"🎾 <b>Новый матч</b>\n\n"
        f"Турнир: {match.tournament.name}\n"
        f"Этап: {match.round_name or '—'}\n"
        f"{side1} — {side2}\n"
        f"Дедлайн: {deadline_str}\n\n"
        "Внести результат или посмотреть матчи — кнопки ниже."
    )


def notify_match_created(match) -> None:
    """Уведомление участникам о создании матча (кнопки только в боте)."""
    if not bot.is_configured():
        return
    text = _match_info_text(match)
    reply_markup = {
        "inline_keyboard": [
            [{"text": "📝 Внести результат", "callback_data": f"result_enter_{match.pk}"}],
            [{"text": "📅 Мои матчи", "callback_data": "menu_my_matches"}],
        ],
    }
    for user in get_match_participant_users(match):
        send_to_user_by_user(user, text, reply_markup=reply_markup)


def notify_result_proposal(proposal) -> None:
    """Уведомление сопернику о предложенном результате с кнопками Подтвердить/Отклонить."""
    if not bot.is_configured():
        return
    match = proposal.match
    proposer = proposal.proposer
    score = proposal.match.score_display
    try:
        score = " / ".join(
            f"{getattr(proposal, f'player1_set{i}')}:{getattr(proposal, f'player2_set{i}')}"
            for i in (1, 2, 3)
            if getattr(proposal, f"player1_set{i}") is not None
        ) or "—"
    except Exception:
        score = "—"
    text = (
        f"📩 <b>{proposer} предложил результат матча</b>\n\n"
        f"Турнир: {match.tournament.name}\n"
        f"Счёт: {score}\n\n"
        "Подтвердите или отклоните:"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Подтвердить", "callback_data": f"proposal_confirm_{proposal.pk}"},
                {"text": "❌ Отклонить", "callback_data": f"proposal_reject_{proposal.pk}"},
            ],
        ],
    }
    for user in get_match_opponent_users(match, proposer):
        send_to_user_by_user(user, text, reply_markup=reply_markup)


def notify_proposal_confirmed(proposal) -> None:
    """Уведомление инициатору о подтверждении результата."""
    if not bot.is_configured():
        return
    proposer_user = getattr(proposal.proposer, "user", None)
    if not proposer_user:
        return
    match = proposal.match
    text = (
        "✅ <b>Результат подтверждён</b>\n\n"
        f"Матч «{match.tournament.name}» завершён. Счёт учтён."
    )
    send_to_user_by_user(proposer_user, text)


def notify_proposal_rejected(proposal) -> None:
    """Уведомление инициатору об отклонении результата."""
    if not bot.is_configured():
        return
    proposer_user = getattr(proposal.proposer, "user", None)
    if not proposer_user:
        return
    match = proposal.match
    text = (
        "❌ <b>Результат отклонён</b>\n\n"
        f"Соперник отклонил предложенный счёт. Введите результат заново (Мои матчи → Внести результат)."
    )
    send_to_user_by_user(proposer_user, text)


def notify_match_deadline_reminder(match, days_left: int) -> None:
    """
    Напоминание участникам матча о приближающемся дедлайне (за 2 или 1 день).
    days_left: 2 или 1. В сообщении кнопки «Внести результат», «Мои матчи», «Запросить продление».
    """
    if not bot.is_configured():
        return
    if not match.deadline:
        return
    deadline_str = match.deadline.strftime("%d.%m.%Y %H:%M")
    side1 = match.get_player1_display()
    side2 = match.get_player2_display()
    text = (
        f"⏰ <b>Напоминание: до дедлайна матча {days_left} дн.</b>\n\n"
        f"Турнир: {match.tournament.name}\n"
        f"Этап: {match.round_name or '—'}\n"
        f"{side1} — {side2}\n"
        f"Дедлайн: {deadline_str}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📝 Внести результат", "callback_data": f"result_enter_{match.pk}"},
                {"text": "📅 Мои матчи", "callback_data": "menu_my_matches"},
            ],
            [{"text": "🔄 Запросить продление", "callback_data": f"extension_request_{match.pk}"}],
        ],
    }
    for user in get_match_participant_users(match):
        send_to_user_by_user(user, text, reply_markup=reply_markup)


def notify_extension_approved(extension_request) -> None:
    """Уведомление пользователю об одобрении запроса на продление дедлайна."""
    if not bot.is_configured():
        return
    user = getattr(extension_request.requested_by, "user", None)
    if not user:
        return
    match = extension_request.match
    new_deadline = match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else "—"
    text = (
        "✅ <b>Продление дедлайна одобрено</b>\n\n"
        f"Матч «{match.tournament.name}». Новый дедлайн: {new_deadline}"
    )
    send_to_user_by_user(user, text)
