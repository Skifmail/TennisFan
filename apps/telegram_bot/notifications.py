"""
Уведомления пользователей в Telegram (пользовательский бот).
Отправка по User или chat_id; тексты для регистрации, матча, предложения результата.
"""

import html
import logging
import threading

from apps.core.models import UserTelegramLink
from apps.tournaments.utils import get_match_opponent_users, get_match_participant_users
from apps.users.models import SkillLevel

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


def _format_new_tournament_message(tournament) -> str:
    """Формирует подробный текст уведомления о новом турнире (HTML для Telegram)."""
    parts = [
        "🆕 <b>Новый турнир</b>",
        "",
        f"<b>{html.escape(tournament.name)}</b>",
        f"📍 {html.escape(tournament.city)}",
        "",
        f"Формат: {tournament.get_format_display()}",
        f"Вариант: {tournament.get_variant_display()}",
        f"Категория: {tournament.get_gender_display()}",
        f"Продолжительность: {tournament.get_duration_display()}",
        f"Тип: {tournament.get_tournament_type_display()}",
        f"Статус: {tournament.get_status_display()}",
        "",
        f"📅 Начало: {tournament.start_date.strftime('%d.%m.%Y')}",
    ]
    if tournament.end_date:
        parts.append(f"📅 Окончание: {tournament.end_date.strftime('%d.%m.%Y')}")
    if tournament.registration_deadline:
        parts.append(
            f"⏰ Дедлайн регистрации: {tournament.registration_deadline.strftime('%d.%m.%Y %H:%M')}"
        )
    parts.append("")
    if tournament.entry_fee and tournament.entry_fee > 0:
        parts.append(f"💰 Взнос: {tournament.entry_fee} ₽")
    if tournament.is_singles():
        if tournament.min_participants is not None or tournament.max_participants is not None:
            min_m = tournament.min_participants or "—"
            max_m = tournament.max_participants or "—"
            parts.append(f"Участники: от {min_m} до {max_m}")
    else:
        if tournament.min_teams is not None or tournament.max_teams is not None:
            min_t = tournament.min_teams or "—"
            max_t = tournament.max_teams or "—"
            parts.append(f"Команд: от {min_t} до {max_t}")
    try:
        categories = list(
            tournament.allowed_categories.values_list("category", flat=True)
        )
        if categories:
            labels = [SkillLevel(c).label for c in categories]
            parts.append(f"Категории участников: {', '.join(labels)}")
    except Exception:
        pass
    if tournament.description:
        desc = html.escape(tournament.description.strip())
        if len(desc) > 400:
            desc = desc[:397] + "..."
        parts.extend(["", desc])
    return "\n".join(parts)


def _send_new_tournament_to_all(tournament_pk: int) -> None:
    """В фоне отправить уведомление о новом турнире всем пользователям с привязанным ботом."""
    from django.db import connection

    connection.close()
    try:
        from apps.tournaments.models import Tournament

        tournament = (
            Tournament.objects.filter(pk=tournament_pk)
            .prefetch_related("allowed_categories")
            .first()
        )
        if not tournament:
            logger.warning("New tournament notify: tournament pk=%s not found", tournament_pk)
            return
        if not bot.is_configured():
            logger.warning("New tournament notify: bot not configured (TELEGRAM_USER_BOT_TOKEN), pk=%s", tournament_pk)
            return
        links = UserTelegramLink.objects.filter(
            user_bot_chat_id__isnull=False
        ).exclude(user_bot_chat_id=0)
        total = links.count()
        if total == 0:
            logger.info("New tournament pk=%s: no users with bot linked, skip send", tournament_pk)
            return
        text = _format_new_tournament_message(tournament)
        sent = 0
        for link in links:
            try:
                if bot.send_to_user(link.user_bot_chat_id, text):
                    sent += 1
            except Exception as e:
                logger.warning("New tournament notify to %s failed: %s", link.user_bot_chat_id, e)
        logger.info("New tournament pk=%s notified to %s/%s users", tournament_pk, sent, total)
    except Exception as e:
        logger.exception("_send_new_tournament_to_all pk=%s failed: %s", tournament_pk, e)


def notify_new_tournament(tournament) -> None:
    """
    Уведомление всем пользователям с привязанным ботом о новом турнире.
    Вызывается при создании турнира (post_save, created=True). Отправка в фоне.
    """
    pk = getattr(tournament, "pk", None)
    if not tournament or pk is None:
        logger.debug("notify_new_tournament: no tournament or no pk, skip")
        return
    if not bot.is_configured():
        logger.warning("notify_new_tournament: bot not configured, tournament pk=%s", pk)
        return
    logger.info("New tournament created pk=%s, starting background notify", pk)
    thread = threading.Thread(
        target=_send_new_tournament_to_all,
        args=(tournament.pk,),
        daemon=True,
        name=f"notify_tournament_{tournament.pk}",
    )
    thread.start()
