"""
Уведомления пользователей в Telegram (пользовательский бот).
Отправка по User или chat_id; тексты для регистрации, матча, предложения результата.
"""

import html
import logging
import threading

from apps.core.models import UserTelegramLink
from apps.tournaments.models import Match
from apps.tournaments.utils import (
    get_match_opponent_users,
    get_match_participant_users,
    get_tournament_participant_users,
)
from apps.users.models import Notification, SkillLevel

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


def notify_bracket_formed(tournament) -> None:
    """
    Уведомление всем участникам турнира о сформированной сетке (в бот и в ЛК).
    Вызывать после формирования сетки (bracket_generated=True), один раз на турнир.
    """
    users = get_tournament_participant_users(tournament)
    from django.urls import reverse

    url = reverse("tournament_detail", args=[tournament.slug])
    message_lk = f"Сетка турнира «{tournament.name}» сформирована. Проверьте матчи в «Мои матчи»."
    if len(message_lk) > 255:
        message_lk = message_lk[:252] + "..."

    for user in users:
        try:
            Notification.objects.create(user=user, message=message_lk, url=url)
        except Exception as e:
            logger.warning("notify_bracket_formed Notification for user %s: %s", user.pk, e)

    if not bot.is_configured():
        return
    text = (
        f"📋 <b>Сетка турнира сформирована</b>\n\n"
        f"«{tournament.name}»\n\n"
        "Ваши матчи уже в разделе «Мои матчи». Внесите результат до дедлайна."
    )
    reply_markup = {
        "inline_keyboard": [
            [{"text": "📅 Мои матчи", "callback_data": "menu_my_matches"}],
        ],
    }
    for user in users:
        send_to_user_by_user(user, text, reply_markup=reply_markup)


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
    """
    Уведомление сопернику о предложенном результате с кнопками Подтвердить/Отклонить.

    Логика с точки зрения ПОЛУЧАТЕЛЯ (соперника):
    - WALKOVER_WIN (proposer заявляет победу) → получатель проигрывает → -40 очков, 6:0 6:0
    - WALKOVER_LOSS (proposer признаёт поражение) → получатель выигрывает → без штрафа
    """
    if not bot.is_configured():
        return
    match = proposal.match
    proposer = proposal.proposer

    result_val = str(proposal.result) if proposal.result else ""
    is_walkover_loss = result_val == "walkover_loss"
    is_walkover_win = result_val == "walkover_win"

    if is_walkover_win:
        # Proposer заявляет тех. победу → получатель (соперник) проигрывает
        result_text = "Тех. победа (соперник заявляет, что вы не вышли)"
        score = "6:0 6:0"
        warning_text = (
            "\n\n⚠️ <b>Внимание!</b> Если вы подтвердите:\n"
            "• Из вашего рейтинга будет вычтено <b>40 очков</b>\n"
            "• Счёт будет записан как <b>6:0 6:0</b> в пользу соперника"
        )
    elif is_walkover_loss:
        # Proposer признаёт своё тех. поражение → получатель выигрывает
        result_text = "Тех. поражение (соперник признаёт, что не вышел)"
        score = "6:0 6:0 в вашу пользу"
        warning_text = (
            "\n\n✅ Соперник признаёт тех. поражение.\n"
            "• Из рейтинга <b>соперника</b> будет вычтено <b>40 очков</b>\n"
            "• Счёт: <b>6:0 6:0</b> в вашу пользу"
        )
    else:
        result_text = proposal.get_result_display()
        try:
            score = " / ".join(
                f"{getattr(proposal, f'player1_set{i}')}:{getattr(proposal, f'player2_set{i}')}"
                for i in (1, 2, 3)
                if getattr(proposal, f"player1_set{i}") is not None
            ) or "—"
        except Exception:
            score = "—"
        warning_text = ""

    text = (
        f"📩 <b>{proposer} предложил результат матча</b>\n\n"
        f"Турнир: {match.tournament.name}\n"
        f"Результат: {result_text}\n"
        f"Счёт: {score}{warning_text}\n\n"
        f"⏰ У вас есть <b>3 часа</b> на подтверждение или отклонение.\n"
        f"Если не ответите в течение 3 часов, результат будет подтверждён автоматически.\n\n"
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


def _proposal_score_text(proposal) -> str:
    """Формирует строку счёта из proposal."""
    sets = []
    for i in (1, 2, 3):
        s1 = getattr(proposal, f"player1_set{i}", None)
        s2 = getattr(proposal, f"player2_set{i}", None)
        if s1 is not None and s2 is not None:
            sets.append(f"{s1}:{s2}")
    return " / ".join(sets) if sets else "—"


def _proposal_opponent_name(proposal) -> str:
    """Имя соперника (того, кто подтверждает/отклоняет)."""
    match = proposal.match
    proposer = proposal.proposer
    is_doubles = match.team1_id and match.team2_id
    if is_doubles:
        if not match.team1 or not match.team2:
            return "Соперник"
        if proposer in (match.team1.player1, match.team1.player2):
            proposer_team = match.team1
            opponent_team = match.team2
        elif proposer in (match.team2.player1, match.team2.player2):
            proposer_team = match.team2
            opponent_team = match.team1
        else:
            return "Соперник"
        names = []
        for p in (opponent_team.player1, opponent_team.player2):
            if p and not getattr(p, "is_bye", False):
                names.append(str(p))
        return " / ".join(names) if names else "Соперник"
    opponent = match.player2 if proposer == match.player1 else match.player1
    return str(opponent) if opponent else "Соперник"


def _proposal_result_text(proposal) -> str:
    """Текстовое описание результата из proposal."""
    result_val = str(proposal.result) if proposal.result else ""
    if result_val == "walkover_loss":
        return "Тех. поражение"
    if result_val == "walkover_win":
        return "Тех. победа"
    if result_val == "win":
        return "Победа"
    if result_val == "loss":
        return "Поражение"
    try:
        return proposal.get_result_display()
    except Exception:
        return result_val or "—"


def _get_penalty_text_for_player(match, player) -> str:
    """
    Получить текст о штрафе для конкретного игрока.
    
    Args:
        match: Объект Match (уже должен иметь установленные winner и winner_team)
        player: Player объект, для которого нужно определить штраф
    
    Returns:
        Строка с информацией о штрафе или пустая строка, если штраф не применяется
    """
    if not match.is_walkover_loss():
        return ""
    
    is_doubles = match.team1_id and match.team2_id
    
    if is_doubles:
        # Проверяем наличие команд
        if not match.team1 or not match.team2:
            return ""
        
        # Определяем команду игрока
        if player in (match.team1.player1, match.team1.player2):
            player_team = match.team1
        elif player in (match.team2.player1, match.team2.player2):
            player_team = match.team2
        else:
            # Игрок не найден в командах (не должен происходить в нормальной ситуации)
            return ""
        
        winner_team = match.winner_team
        if not winner_team:
            return ""
        loser_team = match.team2 if winner_team == match.team1 else match.team1
        
        # Если команда игрока проиграла - он получил штраф
        if player_team == loser_team:
            return "\n\n⚠️ <b>Штраф:</b> Из вашего рейтинга силы вычтено <b>40 очков</b> за тех. поражение."
        else:
            return "\n\nℹ️ Из рейтинга силы соперника вычтено <b>40 очков</b> за тех. поражение."
    else:
        # Одиночный матч
        if not match.winner:
            return ""
        if player == match.winner:
            # Игрок выиграл - соперник получил штраф
            return "\n\nℹ️ Из рейтинга силы соперника вычтено <b>40 очков</b> за тех. поражение."
        else:
            # Игрок проиграл - он получил штраф
            return "\n\n⚠️ <b>Штраф:</b> Из вашего рейтинга силы вычтено <b>40 очков</b> за тех. поражение."


def notify_proposal_confirmed(proposal) -> None:
    """Уведомление инициатору о подтверждении результата."""
    if not bot.is_configured():
        logger.warning("notify_proposal_confirmed: bot not configured")
        return
    proposer_user = getattr(proposal.proposer, "user", None)
    if not proposer_user:
        logger.warning("notify_proposal_confirmed: proposer has no user")
        return
    match = proposal.match
    opponent = _proposal_opponent_name(proposal)
    result = _proposal_result_text(proposal)
    score = match.score_display() if match.winner else _proposal_score_text(proposal)
    winner_name = str(match.winner) if match.winner else "—"
    
    # Получаем текст о штрафе для инициатора (proposer)
    proposer_player = proposal.proposer
    penalty_text = _get_penalty_text_for_player(match, proposer_player)

    text = (
        "✅ <b>Результат подтверждён</b>\n\n"
        f"Турнир: {match.tournament.name}\n"
        f"Матч: {match.get_player1_display()} vs {match.get_player2_display()}\n"
        f"Результат: {result}\n"
        f"Счёт: {score}\n"
        f"Победитель: {winner_name}{penalty_text}\n\n"
        f"<b>{opponent}</b> подтвердил(а) результат."
    )
    ok = send_to_user_by_user(proposer_user, text)
    logger.info(
        "notify_proposal_confirmed: proposer=%s, user=%s, sent=%s",
        proposal.proposer, proposer_user, ok,
    )


def notify_proposal_rejected(proposal) -> None:
    """Уведомление инициатору об отклонении результата."""
    if not bot.is_configured():
        logger.warning("notify_proposal_rejected: bot not configured")
        return
    proposer_user = getattr(proposal.proposer, "user", None)
    if not proposer_user:
        logger.warning("notify_proposal_rejected: proposer has no user")
        return
    match = proposal.match
    opponent = _proposal_opponent_name(proposal)
    result = _proposal_result_text(proposal)
    score = _proposal_score_text(proposal)

    text = (
        "❌ <b>Результат отклонён</b>\n\n"
        f"Турнир: {match.tournament.name}\n"
        f"Матч: {match.get_player1_display()} vs {match.get_player2_display()}\n"
        f"Ваш результат: {result}\n"
        f"Счёт: {score}\n\n"
        f"<b>{opponent}</b> отклонил(а) предложенный счёт.\n"
        "Введите результат заново (Мои матчи → Внести результат)."
    )
    ok = send_to_user_by_user(proposer_user, text)
    logger.info(
        "notify_proposal_rejected: proposer=%s, user=%s, sent=%s",
        proposal.proposer, proposer_user, ok,
    )


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
    notify_new_tournament_by_pk(pk)


def notify_tournament_start(tournament) -> None:
    """
    Уведомление участникам турнира о начале турнира (в бот и в ЛК).
    Вызывать в день start_date турнира (например из cron утром).
    """
    from django.urls import reverse

    users = get_tournament_participant_users(tournament)
    url = reverse("tournament_detail", args=[tournament.slug])
    message_lk = f"Турнир «{tournament.name}» начинается сегодня. Удачи!"
    if len(message_lk) > 255:
        message_lk = message_lk[:252] + "..."

    for user in users:
        try:
            Notification.objects.create(user=user, message=message_lk, url=url)
        except Exception as e:
            logger.warning("notify_tournament_start Notification for user %s: %s", user.pk, e)

    if not bot.is_configured():
        return
    start_str = tournament.start_date.strftime("%d.%m.%Y") if tournament.start_date else "сегодня"
    text = (
        f"🏟 <b>Турнир начинается</b>\n\n"
        f"«{tournament.name}»\n"
        f"📍 {tournament.city}\n"
        f"📅 {start_str}\n\n"
        "Проверьте свои матчи в разделе «Мои матчи» и внесите результат до дедлайна."
    )
    reply_markup = {
        "inline_keyboard": [
            [{"text": "📅 Мои матчи", "callback_data": "menu_my_matches"}],
            [{"text": "🔗 Страница турнира", "url": _get_site_base_url().rstrip("/") + url}],
        ],
    }
    for user in users:
        send_to_user_by_user(user, text, reply_markup=reply_markup)


def notify_new_tournament_by_pk(tournament_pk: int) -> None:
    """
    Запуск рассылки о новом турнире по pk. Вызывать после transaction.on_commit(),
    чтобы турнир был уже закоммичен и виден в фоновом потоке.
    """
    if not bot.is_configured():
        logger.warning("notify_new_tournament_by_pk: bot not configured, pk=%s", tournament_pk)
        return
    logger.info("New tournament pk=%s, starting background notify", tournament_pk)
    thread = threading.Thread(
        target=_send_new_tournament_to_all,
        args=(tournament_pk,),
        daemon=True,
        name=f"notify_tournament_{tournament_pk}",
    )
    thread.start()


def notify_subscription_expiring(user, subscription, days_left: int) -> None:
    """
    Уведомление пользователю об истечении подписки за N дней.
    
    Args:
        user: User объект
        subscription: UserSubscription объект
        days_left: количество дней до истечения (3 или 1)
    """
    if not bot.is_configured():
        return
    
    tier = subscription.tier
    tier_name = tier.get_name_display()
    end_date_str = subscription.end_date.strftime("%d.%m.%Y")
    
    # Продающий текст о том, чего лишится игрок
    features_lost = []
    if tier.can_write_comments:
        features_lost.append("• Комментирование матчей и турниров")
    if tier.can_rate_opponents:
        features_lost.append("• Оценка соперников после матчей")
    if tier.has_private_chat:
        features_lost.append("• Доступ в закрытый чат сообщества")
    if tier.has_sparring:
        features_lost.append("• Организация спаррингов")
    if tier.max_tournaments > 0 or tier.is_unlimited:
        if tier.is_unlimited:
            features_lost.append("• Регистрация на турниры (безлимит)")
        else:
            features_lost.append(f"• Регистрация на турниры ({tier.max_tournaments} в месяц)")
    if tier.one_day_tournament_discount > 0:
        features_lost.append(f"• Скидка {tier.one_day_tournament_discount}% на однодневные турниры")
    if tier.has_admin_support:
        features_lost.append("• Приоритетная поддержка администратора")
    if tier.has_badge:
        features_lost.append("• Особый статус в профиле")
    
    features_text = "\n".join(features_lost) if features_lost else "• Все функции тарифа"
    
    if days_left == 3:
        urgency_text = "⏰ <b>Ваша подписка истекает через 3 дня!</b>"
    elif days_left == 1:
        urgency_text = "🚨 <b>Ваша подписка истекает завтра!</b>"
    else:
        urgency_text = f"⏰ <b>Ваша подписка истекает через {days_left} дн.</b>"
    
    text = (
        f"{urgency_text}\n\n"
        f"Тариф: <b>{tier_name}</b>\n"
        f"Истекает: {end_date_str}\n\n"
        f"<b>Что вы потеряете:</b>\n"
        f"{features_text}\n\n"
        f"💡 <b>Продлите подписку сейчас</b> и сохраните доступ ко всем функциям!\n"
        f"Не упустите возможность участвовать в турнирах и общаться с сообществом."
    )
    
    site_base_url = _get_site_base_url()
    pricing_url = f"{site_base_url.rstrip('/')}/subscriptions/pricing/"
    
    reply_markup = {
        "inline_keyboard": [
            [{"text": "💳 Продлить подписку", "url": pricing_url}],
            [{"text": "📋 Моя подписка", "callback_data": "menu_my_subscription"}],
        ],
    }
    
    ok = send_to_user_by_user(user, text, reply_markup=reply_markup)
    logger.info(
        "notify_subscription_expiring: user=%s, tier=%s, days_left=%s, sent=%s",
        user, tier_name, days_left, ok,
    )
