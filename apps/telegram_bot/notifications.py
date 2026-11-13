"""
Уведомления пользователей в Telegram (пользовательский бот).
Отправка по User или chat_id; тексты для регистрации, матча, предложения результата.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from loguru import logger

from apps.core.models import UserTelegramLink
from apps.notifications.infrastructure.email import send_event_mail as send_mail
from apps.notifications.services import (
    capture_email_event,
    center_enabled,
    create_notification,
)
from apps.tournaments.models import Match, MatchResultProposal
from apps.tournaments.utils import (
    get_match_opponent_users,
    get_match_participant_users,
    get_tournament_participant_users,
)
from apps.users.models import Player, SkillLevel, User

from . import services as bot

if TYPE_CHECKING:
    from apps.sparring.models import (
        DoublesJoinRequest,
        DoublesMatchRequest,
        SparringInvitation,
        SparringResponse,
    )
    from apps.subscriptions.models import UserSubscription
    from apps.tournaments.models import DeadlineExtensionRequest, Tournament


def get_chat_id_for_user(user: User | None) -> int | None:
    """chat_id для пользовательского бота (None если пользователь ещё не нажал /start в чате с ботом)."""
    if not user or not user.is_active:
        return None
    chat_id: int | None = (
        UserTelegramLink.objects.filter(user_id=user.pk, user_bot_chat_id__gt=0)
        .values_list("user_bot_chat_id", flat=True)
        .first()
    )
    return chat_id


def send_to_user_by_user(
    user: User | None,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    *,
    skip_email: bool = False,
    event_key: str = "platform.user_message",
) -> bool:
    """Поставить Telegram в очередь и при необходимости отправить email-дубль.

    Args:
        user: Пользователь-получатель.
        text (str): Текст сообщения (HTML для Telegram).
        reply_markup (dict | None): Клавиатура Telegram.
        skip_email (bool): Не дублировать на почту (если письмо уже уходит отдельно).
        event_key: Явный код события для политики почтового канала.

    Returns:
        bool: ``True``, если Telegram сохранён в очереди либо email принят SMTP.
        Приём Telegram подтверждается отдельно статусом Delivery.accepted.
    """
    from .delivery import enqueue_message

    telegram_queued = (
        enqueue_message(user, text, reply_markup, event_key=event_key) is not None
    )
    email_ok = False
    if not skip_email:
        email_ok = _send_notification_email_to_user(
            user=user, text=text, event_key=event_key
        )
    return telegram_queued or email_ok


def _send_notification_email_to_user(
    user: User | None, text: str, *, event_key: str = "platform.user_message"
) -> bool:
    """Сохранить ЛК/push почтового события и отправить email после коммита.

    Args:
        user: Пользователь-получатель уведомления.
        text (str): Текст Telegram-уведомления (HTML-формат).

    Returns:
        bool: ``True`` только после приёма письма SMTP; отложенная отправка — ``False``.
    """
    if (
        not bool(getattr(settings, "USER_NOTIFICATIONS_EMAIL_ENABLED", True))
        and not center_enabled()
    ):
        return False
    if user is None:
        return False
    if center_enabled():
        user = User.objects.filter(pk=user.pk, is_active=True).first()
        if user is None:
            return False
    recipient = str(getattr(user, "email", "") or "").strip()
    if not recipient or "@" not in recipient:
        return False

    body = html.unescape(strip_tags(text or "")).strip()
    if not body:
        return False
    subject = _build_user_email_subject(body)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru")
    delivery_allowed = bool(getattr(settings, "USER_NOTIFICATIONS_EMAIL_ENABLED", True))
    if center_enabled():
        # Обязательные локальные каналы фиксируются вместе с исходным действием.
        # Повторный захват события в SMTP-callback ниже запрещён.
        capture_email_event(event_key, user)

    def deliver() -> bool:
        """Отправить подготовленное письмо только после успешной фиксации результата."""
        try:
            return bool(
                send_mail(
                    subject=subject,
                    message=body,
                    from_email=from_email,
                    recipient_list=[recipient],
                    fail_silently=False,
                    event_key=event_key,
                    capture_event=False,
                    delivery_allowed=delivery_allowed,
                )
            )
        except Exception as exc:
            logger.warning(
                "Email-дубль уведомления не отправлен: user_id={}, error={}",
                user.pk,
                type(exc).__name__,
            )
            return False

    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(deliver)
        # SMTP пока не подтвердил отправку. При откате callback будет удалён.
        return False
    return deliver()


def _build_user_email_subject(body: str) -> str:
    """Собрать тему письма по первой непустой строке уведомления.

    Args:
        body (str): Текст уведомления без HTML-разметки.

    Returns:
        str: Тема письма в формате ``TennisFan: <тип уведомления>``.
    """
    default_subject = "TennisFan: Уведомление в личный кабинет"
    if not body:
        return default_subject

    first_line = ""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line:
            first_line = line
            break
    if not first_line:
        return default_subject

    normalized = first_line.lstrip(" \t-•:|")
    while normalized and not (normalized[0].isalnum() or normalized[0] in ("№", "#")):
        normalized = normalized[1:].lstrip(" \t-•:|")

    if not normalized:
        return default_subject
    if len(normalized) > 120:
        normalized = normalized[:117].rstrip() + "..."
    return f"TennisFan: {normalized}"


def _get_site_base_url() -> str:
    """Получить базовый адрес сайта для ссылок из уведомлений."""
    from django.conf import settings

    base = str(getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", None) or "")
    if base:
        return base.rstrip("/") + "/"
    return "http://localhost:8000/" if settings.DEBUG else "https://tennisfan.ru/"


def notify_tournament_registered(user: User, tournament: Tournament) -> None:
    """Уведомление о регистрации на турнир."""
    if not bot.is_configured():
        return
    deadline = tournament.registration_deadline
    deadline_str = (
        f"до {deadline.strftime('%d.%m.%Y')}" if deadline else "в ближайшее время"
    )
    is_tvd = getattr(tournament, "format", None) == "weekend_day"
    grid_word = "групп" if is_tvd else "сетки"
    text = (
        f"🎾 <b>Вы зарегистрированы на турнир</b>\n\n"
        f"«{tournament.name}» ({tournament.city})\n\n"
        f"Ожидайте формирования {grid_word} {deadline_str}. "
        f"Мы пришлём уведомление о ваших матчах в этом турнире."
    )
    send_to_user_by_user(user, text, event_key="tournament.registered")


def notify_tournament_removed_refund(
    user: User, tournament: Tournament, feedback_url: str
) -> None:
    """Уведомление о снятии с турнира и возврате взноса — ссылка на форму обратной связи."""
    if not bot.is_configured():
        return
    text = (
        f"⚠️ <b>Вы сняты с турнира</b> «{tournament.name}».\n\n"
        "Взнос за участие был оплачен. Для возврата средств обратитесь к администратору через форму обратной связи по ссылке ниже."
    )
    reply_markup = (
        {
            "inline_keyboard": [
                [{"text": "Обратная связь (возврат средств)", "url": feedback_url}]
            ]
        }
        if feedback_url
        else None
    )
    send_to_user_by_user(
        user, text, reply_markup=reply_markup, event_key="tournament.removed_refund"
    )


def notify_postpayment_opened(
    user: User, tournament: Tournament, amount: str, due_at_text: str, payment_url: str
) -> None:
    """Уведомить игрока о старте окна постоплаты турнира."""
    if not bot.is_configured():
        return
    text = (
        f"💳 <b>Нужно оплатить турнир</b>\n\n"
        f"Турнир: «{tournament.name}»\n"
        f"Сумма: {amount} ₽\n"
        f"Срок оплаты: {due_at_text}\n\n"
        f"{payment_url}"
    )
    send_to_user_by_user(user, text, event_key="tournament.payment_opened")


def notify_postpayment_1h_reminder(
    user: User, tournament: Tournament, due_at_text: str, payment_url: str
) -> None:
    """Уведомить игрока о скором завершении окна постоплаты."""
    if not bot.is_configured():
        return
    text = (
        f"⏰ <b>Напоминание об оплате турнира</b>\n\n"
        f"Турнир: «{tournament.name}»\n"
        f"Срок оплаты: {due_at_text}\n\n"
        f"{payment_url}"
    )
    send_to_user_by_user(user, text, event_key="tournament.payment_reminder")


def notify_postpayment_removed(user: User, tournament: Tournament) -> None:
    """Уведомить игрока об удалении из турнира за неоплату."""
    if not bot.is_configured():
        return
    text = (
        f"⚠️ <b>Регистрация отменена</b>\n\n"
        f"Вы удалены из турнира «{tournament.name}», потому что срок постоплаты истёк."
    )
    send_to_user_by_user(user, text, event_key="tournament.unpaid_removed")


def _player_contact_lines(player: Player) -> list[str]:
    """Строки контактов игрока для уведомления (HTML; читаемы и после strip_tags в email).

    Включает мессенджеры (Telegram, WhatsApp, MAX) и телефон. Показываются только
    заполненные контакты.

    Args:
        player: Объект ``Player``, чьи контакты нужно вывести.

    Returns:
        list[str]: Список строк вида ``"Telegram: @user"`` (с HTML-ссылками,
        где это возможно). Пустой список, если контактов нет.
    """
    lines: list[str] = []
    tg_url = getattr(player, "telegram_url", None)
    if tg_url:
        username = str(player.telegram or "").strip().lstrip("@")
        label = f"@{html.escape(username)}" if username else "профиль"
        lines.append(f'Telegram: <a href="{tg_url}">{label}</a>')
    wa_url = getattr(player, "whatsapp_url", None)
    if wa_url:
        lines.append(
            f'WhatsApp: <a href="{wa_url}">{html.escape(str(player.whatsapp))}</a>'
        )
    max_url = getattr(player, "max_url", None)
    max_display = getattr(player, "max_contact_display", None)
    if max_url:
        label = html.escape(str(max_display)) if max_display else "профиль"
        lines.append(f'MAX: <a href="{max_url}">{label}</a>')
    elif max_display:
        lines.append(f"MAX: {html.escape(str(max_display))}")
    phone = str(getattr(getattr(player, "user", None), "phone", "") or "").strip()
    if phone:
        lines.append(f"Телефон: {html.escape(phone)}")
    return lines


def _opponent_contacts_block(opponents: list[Player]) -> str:
    """Блок контактов соперника(ов) для уведомления о матче (HTML).

    Args:
        opponents (list): Список объектов ``Player`` — соперники получателя.

    Returns:
        str: HTML-блок «Контакты соперника» с мессенджерами и телефоном.
        Если контактов нет — строка с пометкой, что контакты не указаны.
    """
    named = len(opponents) > 1
    parts: list[str] = ["<b>Контакты соперника:</b>"]
    any_contacts = False
    for opp in opponents:
        if not opp or getattr(opp, "is_bye", False):
            continue
        contact_lines = _player_contact_lines(opp)
        if named:
            parts.append(f"<b>{html.escape(str(opp))}</b>")
        if contact_lines:
            any_contacts = True
            parts.extend(contact_lines)
        else:
            parts.append("контакты не указаны")
    if not any_contacts and not named:
        return (
            "<b>Контакты соперника:</b>\n"
            "не указаны — свяжитесь через карточку матча на сайте."
        )
    return "\n".join(parts)


def _match_info_text(match: Match, opponents: list[Player] | None = None) -> str:
    """Текст с информацией о матче для уведомления (без ссылки на сайт).

    Args:
        match: Объект ``Match``.
        opponents (list | None): Соперники получателя для блока контактов.
            Если ``None`` — блок контактов не добавляется (общий текст).

    Returns:
        str: HTML-текст уведомления о новом матче.
    """
    side1 = html.escape(match.get_player1_display())
    side2 = html.escape(match.get_player2_display())
    deadline_str = (
        match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else "не указан"
    )

    # Для спарринговых матчей tournament может быть None
    if match.is_sparring():
        tournament_info = "Спарринг (личная встреча)"
        round_info = "—"
    else:
        tournament_info = (
            html.escape(match.tournament.name) if match.tournament else "—"
        )
        if (
            match.tournament
            and getattr(match.tournament, "format", None) == "weekend_day"
        ):
            tournament_info = f"{tournament_info} (группы + плей-офф)"
        round_info = html.escape(match.round_name or "—")

    contacts_block = ""
    if opponents:
        contacts_block = f"\n{_opponent_contacts_block(opponents)}\n"

    return (
        f"🎾 <b>Новый матч</b>\n\n"
        f"Турнир: {tournament_info}\n"
        f"Этап: {round_info}\n"
        f"{side1} — {side2}\n"
        f"Дедлайн: {deadline_str}\n"
        f"{contacts_block}\n"
        "Внести результат или посмотреть матчи — кнопки ниже."
    )


def notify_bracket_formed(tournament: Tournament, subtitle: str | None = None) -> None:
    """
    Уведомление всем участникам турнира о сформированной сетке (в бот и в ЛК).
    Вызывать после формирования сетки (bracket_generated=True).
    subtitle: для ТВД можно передать "Группы сформированы" или "Плей-офф сформирован".
    """
    users = get_tournament_participant_users(tournament)
    from django.urls import reverse

    url = reverse("tournament_detail", args=[tournament.slug])
    if subtitle:
        message_lk = (
            f"{subtitle} турнира «{tournament.name}». Проверьте матчи в «Мои матчи»."
        )
    else:
        message_lk = f"Сетка турнира «{tournament.name}» сформирована. Проверьте матчи в «Мои матчи»."
    if len(message_lk) > 255:
        message_lk = message_lk[:252] + "..."

    for user in users:
        try:
            create_notification(
                user=user, message=message_lk, url=url, event_key="tournament.bracket"
            )
        except Exception as e:
            logger.warning(
                "Уведомление о сетке не сохранено: user_id={}, error={}",
                user.pk,
                type(e).__name__,
            )

    if not bot.is_configured():
        return
    if subtitle:
        text = (
            f"📋 <b>{subtitle}</b>\n\n"
            f"«{tournament.name}»\n\n"
            "Ваши матчи уже в разделе «Мои матчи». Внесите результат до дедлайна."
        )
    else:
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
        send_to_user_by_user(
            user, text, reply_markup=reply_markup, event_key="tournament.bracket"
        )


def notify_match_created(match: Match) -> None:
    """Уведомление участникам о создании матча: соперник, дедлайн и контакты соперника.

    Каждому участнику отправляется персональный текст с контактами именно его
    соперника (Telegram/WhatsApp/MAX/телефон). Отправка идёт в Telegram (если бот
    настроен) и дублируется на email — письмо приходит даже без Telegram.

    Args:
        match: Объект ``Match``, для которого рассылаются уведомления.
    """
    from apps.tournaments.utils import (
        get_match_opponents_for_player,
        get_match_participants,
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "📝 Внести результат",
                    "callback_data": f"result_enter_{match.pk}",
                }
            ],
            [{"text": "📅 Мои матчи", "callback_data": "menu_my_matches"}],
        ],
    }
    participants = [
        p
        for p in get_match_participants(match)
        if p and not getattr(p, "is_bye", False) and getattr(p, "user_id", None)
    ]
    for player in participants:
        opponents = get_match_opponents_for_player(match, player)
        text = _match_info_text(match, opponents=opponents)
        send_to_user_by_user(
            player.user, text, reply_markup=reply_markup, event_key="match.created"
        )


def result_dispute_url(match: Match, *, score: str | None = None) -> str:
    """Открыть существующую поддержку с номером матча, счётом и ссылкой на контекст."""
    base = _get_site_base_url().rstrip("/")
    match_url = f"{base}{reverse('match_detail', args=[match.pk])}"
    query = urlencode(
        {
            "subject": f"Оспаривание результата матча №{match.pk}",
            "message": (
                f"Оспариваю результат матча №{match.pk}.\n"
                f"Счёт: {score if score is not None else match.score_display}\n"
                f"Матч: {match_url}\n"
                "Причина оспаривания: "
            ),
        }
    )
    return f"{base}{reverse('support_feedback')}?{query}"


def _result_keyboard(match: Match, *, score: str | None = None) -> dict[str, Any]:
    """Разделить просмотр матча и заметное оспаривание без изменения его результата."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Открыть матч",
                    "url": f"{_get_site_base_url().rstrip('/')}{reverse('match_detail', args=[match.pk])}",
                }
            ],
            [
                {
                    "text": "⚠️ Оспорить результат",
                    "url": result_dispute_url(match, score=score),
                }
            ],
        ]
    }


def _match_context_text(match: Match) -> str:
    """Экранировать название турнира и не требовать его наличия у спарринга."""
    tournament = match.tournament
    if tournament is not None:
        return f"Турнир: {html.escape(tournament.name)}"
    return "Спарринг (личная встреча)" if match.is_sparring() else "Матч"


def notify_result_proposal(proposal: MatchResultProposal) -> None:
    """Совместимое уведомление сопернику с его перспективой результата и оспариванием.

    Новые сыгранные результаты уведомляют всех один раз из apply_proposal.
    Этот вход сохранён для прежних производителей уведомлений о заявках.
    """
    if not bot.is_configured():
        return
    match = proposal.match
    proposer = proposal.proposer

    result_val = str(proposal.result) if proposal.result else ""
    is_walkover_loss = result_val == "walkover_loss"
    is_walkover_win = result_val == "walkover_win"

    if is_walkover_win:
        # Proposer заявляет неявку соперника → получатель проигрывает
        result_text = "Вы проиграли: соперник заявил вашу неявку"
        score = "без счёта"
        warning_text = (
            "\n\n⚠️ <b>Внимание!</b> Матч записан как несостоявшийся:\n"
            "• Из вашего рейтинга вычтено <b>40 очков</b>\n"
            "• Счёт не записывается\n"
            "• Если это ошибка — обратитесь к администратору турнира, "
            "он может изменить результат"
        )
    elif is_walkover_loss:
        # Proposer признаёт свою неявку → получатель выигрывает
        result_text = "Вы выиграли: соперник признал свою неявку"
        score = "без счёта"
        warning_text = (
            "\n\n✅ Соперник признаёт неявку.\n"
            "• Из рейтинга <b>соперника</b> вычтено <b>40 очков</b>\n"
            "• Вам засчитана победа без изменения рейтинга"
        )
    else:
        result_text = "Вы проиграли" if result_val == "win" else "Вы выиграли"
        score = _proposal_score_text(proposal)
        warning_text = ""

    applied = proposal.status == Match.ProposalStatus.ACCEPTED
    if not applied:
        warning_text = ""
    text = (
        f"📩 <b>{html.escape(str(proposer))} внёс результат матча</b>\n\n"
        f"{_match_context_text(match)}\n"
        f"Матч №{match.pk}: {html.escape(match.get_player1_display())} — {html.escape(match.get_player2_display())}\n"
        f"Результат: {result_text}\n"
        f"Счёт: {score}{warning_text}\n\n"
        + ("✅ Результат применён сразу.\n" if applied else "")
        + "Не согласны? Нажмите «Оспорить результат» и опишите причину."
    )
    for user in get_match_opponent_users(match, proposer):
        send_to_user_by_user(
            user,
            text,
            reply_markup=_result_keyboard(match, score=score),
            event_key="match.result_proposed",
        )


def _proposal_score_text(proposal: MatchResultProposal) -> str:
    """Формирует строку счёта из proposal."""
    sets = []
    for i in (1, 2, 3):
        s1 = getattr(proposal, f"player1_set{i}", None)
        s2 = getattr(proposal, f"player2_set{i}", None)
        if s1 is not None and s2 is not None:
            sets.append(f"{s1}:{s2}")
    return " / ".join(sets) if sets else "—"


def _proposal_opponent_name(proposal: MatchResultProposal) -> str:
    """Имя соперника (того, кто подтверждает/отклоняет)."""
    match = proposal.match
    proposer = proposal.proposer
    is_doubles = match.team1_id and match.team2_id
    if is_doubles:
        if not match.team1 or not match.team2:
            return "Соперник"
        if proposer in (match.team1.player1, match.team1.player2):
            opponent_team = match.team2
        elif proposer in (match.team2.player1, match.team2.player2):
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


def _proposal_result_text(proposal: MatchResultProposal) -> str:
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
        return str(proposal.get_result_display())
    except Exception:
        return str(result_val or "—")


def _player_side_index(match: Match, player: Player | None) -> int | None:
    """Определить сторону игрока в матче.

    Args:
        match: Объект ``Match``.
        player: Игрок, чью сторону нужно определить.

    Returns:
        1 или 2 для первой и второй стороны, None — если игрок не участвует.
    """
    if match.team1_id and match.team2_id:
        if match.team1 and player in (match.team1.player1, match.team1.player2):
            return 1
        if match.team2 and player in (match.team2.player1, match.team2.player2):
            return 2
        return None
    if player and player.pk in (match.player1_id, match.partner1_id):
        return 1
    if player and player.pk in (match.player2_id, match.partner2_id):
        return 2
    return None


def _no_show_penalty_text(match: Match, player: Player) -> str:
    """Текст о штрафе за неявку для конкретного участника.

    Args:
        match: Матч со статусом Walkover без счёта.
        player: Игрок — получатель уведомления.

    Returns:
        Строка со сведениями о штрафе или пустая строка.
    """
    side = _player_side_index(match, player)
    if side is None:
        return ""
    side1_no_show, side2_no_show = match.get_no_show_sides()
    player_no_show = side1_no_show if side == 1 else side2_no_show
    opponent_no_show = side2_no_show if side == 1 else side1_no_show
    if player_no_show and opponent_no_show:
        return (
            "\n\n⚠️ <b>Неявка обеим сторонам:</b> из рейтинга силы каждого "
            "участника вычтено <b>40 очков</b>."
        )
    if player_no_show:
        return "\n\n⚠️ <b>Неявка:</b> из вашего рейтинга силы вычтено <b>40 очков</b>."
    return (
        "\n\nℹ️ Сопернику засчитана неявка: из его рейтинга силы вычтено "
        "<b>40 очков</b>."
    )


def _get_penalty_text_for_player(match: Match, player: Player) -> str:
    """
    Получить текст о штрафе для конкретного игрока.

    Args:
        match: Объект Match (уже должен иметь установленные winner и winner_team)
        player: Player объект, для которого нужно определить штраф

    Returns:
        Строка с информацией о штрафе или пустая строка, если штраф не применяется
    """
    if match.is_no_show_walkover():
        return _no_show_penalty_text(match, player)

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


def notify_result_confirmed_to_participants(match: Match) -> None:
    """Уведомить всех о применённом результате, не приписывая сопернику подтверждение."""
    if not bot.is_configured():
        return
    from apps.tournaments.proposal_service import result_summary_for_player
    from apps.tournaments.utils import get_match_participants

    participants = [
        p
        for p in get_match_participants(match)
        if p and not getattr(p, "is_bye", False) and getattr(p, "user_id", None)
    ]

    for p in participants:
        user = getattr(p, "user", None)
        if not user:
            continue
        result_line = result_summary_for_player(match, p)
        score = "без счёта" if match.is_no_show_walkover() else match.score_display

        text = (
            "✅ <b>Результат матча применён</b>\n\n"
            f"{_match_context_text(match)}\n"
            f"Матч №{match.pk}: {html.escape(match.get_player1_display())} — {html.escape(match.get_player2_display())}\n"
            f"Счёт: {score}\n\n"
            f"{result_line}\n\n"
            "Не согласны? Нажмите «Оспорить результат» и опишите причину."
        )
        send_to_user_by_user(
            user,
            text,
            reply_markup=_result_keyboard(match, score=score),
            event_key="match.result_confirmed",
        )
    logger.info(
        "Уведомление о результате сохранено: match_id={}, participants={}",
        match.pk,
        len(participants),
    )


def notify_proposal_confirmed(proposal: MatchResultProposal) -> None:
    """Уведомление инициатору о подтверждении результата."""
    if not bot.is_configured():
        logger.warning("notify_proposal_confirmed: bot not configured")
        return
    proposer_user = getattr(proposal.proposer, "user", None)
    if not proposer_user:
        logger.warning("notify_proposal_confirmed: proposer has no user")
        return
    match = proposal.match
    result = _proposal_result_text(proposal)
    score = match.score_display if match.winner else _proposal_score_text(proposal)
    winner_name = str(match.winner) if match.winner else "—"

    # Получаем текст о штрафе для инициатора (proposer)
    proposer_player = proposal.proposer
    penalty_text = _get_penalty_text_for_player(match, proposer_player)

    # Определяем информацию о турнире/спарринге
    if match.tournament:
        tournament_info = f"Турнир: {html.escape(match.tournament.name)}"
    elif match.is_sparring():
        tournament_info = "Спарринг (личная встреча)"
    else:
        tournament_info = "Матч"

    text = (
        "✅ <b>Результат матча применён</b>\n\n"
        f"{tournament_info}\n"
        f"Матч: {html.escape(match.get_player1_display())} vs {html.escape(match.get_player2_display())}\n"
        f"Результат: {result}\n"
        f"Счёт: {score}\n"
        f"Победитель: {html.escape(winner_name)}{penalty_text}\n\n"
        "Не согласны? Нажмите «Оспорить результат»."
    )
    ok = send_to_user_by_user(
        proposer_user,
        text,
        reply_markup=_result_keyboard(match, score=score),
        event_key="match.result_confirmed",
    )
    logger.info(
        "Применённый результат: proposer_id={}, user_id={}, queued_or_email_accepted={}",
        proposal.proposer_id,
        proposer_user.pk,
        ok,
    )


def notify_proposal_rejected(proposal: MatchResultProposal) -> None:
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

    # Определяем информацию о турнире/спарринге
    if match.tournament:
        tournament_info = f"Турнир: {match.tournament.name}"
    elif match.is_sparring():
        tournament_info = "Спарринг (личная встреча)"
    else:
        tournament_info = "Матч"

    text = (
        "❌ <b>Результат отклонён</b>\n\n"
        f"{tournament_info}\n"
        f"Матч: {match.get_player1_display()} vs {match.get_player2_display()}\n"
        f"Ваш результат: {result}\n"
        f"Счёт: {score}\n\n"
        f"<b>{opponent}</b> отклонил(а) предложенный счёт.\n"
        "Введите результат заново (Мои матчи → Внести результат)."
    )
    ok = send_to_user_by_user(proposer_user, text, event_key="match.proposal_updated")
    logger.info(
        "Отклонённый результат: proposer_id={}, user_id={}, queued_or_email_accepted={}",
        proposal.proposer_id,
        proposer_user.pk,
        ok,
    )


def notify_match_deadline_reminder(match: Match, days_left: int) -> None:
    """
    Напоминание участникам матча о приближающемся дедлайне (за 2 или 1 день).
    days_left: 2 или 1. В сообщении кнопки «Внести результат», «Мои матчи», «Запросить продление».
    """
    if not bot.is_configured():
        return
    if not match.deadline:
        return
    deadline_str = timezone.localtime(match.deadline).strftime("%d.%m.%Y %H:%M")
    side1 = match.get_player1_display()
    side2 = match.get_player2_display()

    # Определяем информацию о турнире/спарринге
    if match.tournament:
        tournament_info = f"Турнир: {html.escape(match.tournament.name)}"
    elif match.is_sparring():
        tournament_info = "Спарринг (личная встреча)"
    else:
        tournament_info = "Матч"

    deadline_rule = (
        "Если турнирный матч не сыграют до дедлайна — RT 0:0. "
        "Дальше проходит сторона с более высоким рейтингом; рейтинг не меняется.\n\n"
        if match.tournament_id
        else "Если не успеваете сыграть спарринг, продлите дедлайн в карточке матча или обратитесь к администратору.\n\n"
    )
    text = (
        f"⏰ <b>Напоминание: до дедлайна матча {days_left} дн.</b>\n\n"
        f"{tournament_info}\n"
        f"Этап: {html.escape(match.round_name or '—')}\n"
        f"{html.escape(side1)} — {html.escape(side2)}\n"
        f"Дедлайн: {deadline_str}\n\n"
        f"{deadline_rule}"
        "Матч не может состояться по вине соперника? Нажмите «Внести "
        "результат» и отметьте неявку — счёт вводить не нужно, −40 очков "
        "получит только неявившаяся сторона."
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "📝 Внести результат",
                    "callback_data": f"result_enter_{match.pk}",
                },
                {"text": "📅 Мои матчи", "callback_data": "menu_my_matches"},
            ],
            [
                {
                    "text": "🔄 Запросить продление",
                    "callback_data": f"extension_request_{match.pk}",
                }
            ],
        ],
    }
    from apps.tournaments.sparring_deadlines import can_extend_sparring_deadline

    for user in get_match_participant_users(match):
        personal_text = text
        personal_markup = reply_markup
        if match.is_sparring() and can_extend_sparring_deadline(
            match, getattr(user, "player", None)
        ):
            personal_text += (
                "\n\nНе успеваете сыграть? Каждый участник может один раз продлить дедлайн "
                "на 7 дней. Откройте матч на сайте и нажмите «Продлить дедлайн на неделю»."
            )
            personal_markup = {
                "inline_keyboard": [
                    reply_markup["inline_keyboard"][0],
                    [
                        {
                            "text": "Открыть матч и продлить дедлайн",
                            "url": f"{_get_site_base_url().rstrip('/')}{reverse('match_detail', args=[match.pk])}",
                        }
                    ],
                ],
            }
        send_to_user_by_user(
            user,
            personal_text,
            reply_markup=personal_markup,
            skip_email=True,
            event_key="match.deadline_reminder",
        )


def notify_extension_approved(extension_request: DeadlineExtensionRequest) -> None:
    """Уведомление пользователю об одобрении запроса на продление дедлайна."""
    if not bot.is_configured():
        return
    user = getattr(extension_request.requested_by, "user", None)
    if not user:
        return
    match = extension_request.match
    new_deadline = match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else "—"

    # Определяем информацию о турнире/спарринге
    if match.tournament:
        match_info = f"Матч «{match.tournament.name}»"
    elif match.is_sparring():
        match_info = "Спарринговый матч"
    else:
        match_info = "Матч"

    text = (
        "✅ <b>Продление дедлайна одобрено</b>\n\n"
        f"{match_info}. Новый дедлайн: {new_deadline}"
    )
    send_to_user_by_user(user, text, event_key="match.deadline_extended")


def _format_new_tournament_message(tournament: Tournament) -> str:
    """Формирует подробный текст уведомления о новом турнире (HTML для Telegram)."""
    format_display = (
        "Группы + плей-офф"
        if getattr(tournament, "format", None) == "weekend_day"
        else tournament.get_format_display()
    )
    club = getattr(tournament, "club", None)
    parts = [
        "🆕 <b>Новый клубный турнир</b>" if club else "🆕 <b>Новый турнир</b>",
        "",
        f"<b>{html.escape(tournament.name)}</b>",
    ]
    if club:
        parts.extend(
            [
                f"Клуб: {html.escape(club.name)}",
                "",
            ]
        )
    parts.extend(
        [
            f"📍 {html.escape(tournament.city)}",
            "",
            f"Формат: {format_display}",
            f"Вариант: {tournament.get_variant_display()}",
            f"Категория: {tournament.get_gender_display()}",
            f"Продолжительность: {tournament.get_duration_display()}",
            f"Тип: {tournament.get_tournament_type_display()}",
            f"Статус: {tournament.get_status_display()}",
            "",
            f"📅 Начало: {tournament.start_date.strftime('%d.%m.%Y') if tournament.start_date else 'не указано'}",
        ]
    )
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
        if (
            tournament.min_participants is not None
            or tournament.max_participants is not None
        ):
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


def _get_new_tournament_users(tournament: Tournament) -> QuerySet[User]:
    """Вернуть пользователей для ЛК и email о новом турнире.

    Args:
        tournament: Созданный турнир.

    Returns:
        QuerySet: Активные пользователи. Клубный турнир — активные члены клуба.
        Платформенный — игроки того же населённого пункта, что и турнир.
    """
    from apps.users.models import User

    if tournament.club_id is not None:
        from apps.clubs.models import ClubMember, ClubMemberStatus

        user_ids = ClubMember.objects.filter(
            club_id=tournament.club_id,
            status=ClubMemberStatus.ACTIVE,
        ).values_list("user_id", flat=True)
        return User.objects.filter(pk__in=user_ids, is_active=True).order_by("pk")

    from apps.sparring.city_notify import same_city_player_ids

    player_ids = same_city_player_ids(getattr(tournament, "city", "") or "")
    if not player_ids:
        return User.objects.none()
    return (
        User.objects.filter(player__pk__in=player_ids, is_active=True)
        .distinct()
        .order_by("pk")
    )


def _notify_new_tournament_lk_and_email(tournament: Tournament) -> tuple[int, int]:
    """Создать ЛК-уведомления и отправить email о новом турнире.

    Args:
        tournament: Созданный турнир.

    Returns:
        tuple[int, int]: Число ЛК-уведомлений и успешно отправленных писем.
    """
    from django.urls import reverse

    from apps.core.email_service import send_new_tournament_email

    users = _get_new_tournament_users(tournament)
    url = reverse("tournament_detail", args=[tournament.slug])
    message_lk = f"Новый турнир: «{tournament.name}». Приглашаем принять участие!"
    if len(message_lk) > 255:
        message_lk = message_lk[:252] + "..."

    lk_count = 0
    email_count = 0
    for user in users:
        try:
            create_notification(
                user=user, message=message_lk, url=url, event_key="tournament.created"
            )
            lk_count += 1
        except Exception as exc:
            logger.warning(
                "Уведомление о турнире не сохранено: user_id={}, error={}",
                user.pk,
                type(exc).__name__,
            )
        try:
            if send_new_tournament_email(user, tournament):
                email_count += 1
        except Exception as exc:
            logger.warning(
                "Письмо о турнире не отправлено: user_id={}, error={}",
                user.pk,
                type(exc).__name__,
            )
    return lk_count, email_count


def _get_new_tournament_recipients(tournament: Tournament) -> list[UserTelegramLink]:
    """Возвращает получателей Telegram-уведомления о новом турнире."""
    if getattr(tournament, "club_id", None):
        from apps.clubs.models import (
            ClubMember,
            ClubMemberStatus,
            ClubNotificationConfig,
            ClubNotificationSettings,
        )

        config = ClubNotificationConfig.objects.filter(club=tournament.club).first()
        if not config or not config.notify_by_telegram:
            return []
        if not config.tournament_reminders_enabled:
            return []

        recipient_user_ids: list[int] = []
        active_members = ClubMember.objects.filter(
            club=tournament.club,
            status=ClubMemberStatus.ACTIVE,
        ).select_related("user")

        for member in active_members:
            settings_obj, _ = ClubNotificationSettings.objects.get_or_create(
                user=member.user,
                club=tournament.club,
            )
            if not settings_obj.is_enabled or not settings_obj.telegram_enabled:
                continue
            recipient_user_ids.append(member.user_id)

        if not recipient_user_ids:
            return []

        return list(
            UserTelegramLink.objects.filter(
                user_id__in=recipient_user_ids,
                user_bot_chat_id__isnull=False,
            )
            .exclude(user_bot_chat_id=0)
            .distinct()
        )

    city_user_ids = [user.pk for user in _get_new_tournament_users(tournament)]
    if not city_user_ids:
        return []
    return list(
        UserTelegramLink.objects.filter(
            user_id__in=city_user_ids,
            user_bot_chat_id__isnull=False,
        )
        .exclude(user_bot_chat_id=0)
        .distinct()
    )


def _send_new_tournament_notification(tournament_pk: int) -> None:
    """Совместимый вход: подготовить один сохраняемый пакет, не отправляя в сеть."""
    from .announcements import prepare_announcement, request_announcement

    job = request_announcement(tournament_pk)
    prepare_announcement(job.pk)


def _deliver_new_tournament_notification(tournament_pk: int) -> None:
    """Совместимый псевдоним подготовки; транспорт выполняет worker."""
    _send_new_tournament_notification(tournament_pk)


def notify_new_tournament(tournament: Tournament | None) -> None:
    """
    Уведомление всем пользователям с привязанным ботом о новом турнире.
    Вызывается при создании турнира (post_save, created=True). Подготовка в cron.
    """
    pk = getattr(tournament, "pk", None)
    if not tournament or pk is None:
        logger.debug("notify_new_tournament: no tournament or no pk, skip")
        return
    notify_new_tournament_by_pk(pk)


def notify_tournament_start(tournament: Tournament) -> None:
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
            create_notification(
                user=user, message=message_lk, url=url, event_key="tournament.started"
            )
        except Exception as e:
            logger.warning(
                "Уведомление о старте не сохранено: user_id={}, error={}",
                user.pk,
                type(e).__name__,
            )

    if not bot.is_configured():
        return
    start_str = (
        tournament.start_date.strftime("%d.%m.%Y")
        if tournament.start_date
        else "сегодня"
    )
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
            [
                {
                    "text": "🔗 Страница турнира",
                    "url": _get_site_base_url().rstrip("/") + url,
                }
            ],
        ],
    }
    for user in users:
        send_to_user_by_user(
            user, text, reply_markup=reply_markup, event_key="tournament.started"
        )


def notify_new_tournament_by_pk(tournament_pk: int) -> None:
    """Сохранить задание подготовки ЛК, email и Telegram вместо daemon-потока."""
    from .announcements import request_announcement

    request_announcement(tournament_pk)


def notify_subscription_expiring(
    user: User,
    subscription: UserSubscription,
    days_left: int,
    *,
    has_autopay: bool = False,
    renew_amount: str | None = None,
) -> None:
    """
    Уведомление пользователю об истечении подписки за N дней.

    Для пользователей с автосписанием при days_left=1 добавляется сумма
    и ссылка на отключение (требование ФЗ 376-ФЗ).

    Args:
        user: User объект
        subscription: UserSubscription объект
        days_left: количество дней до истечения (3 или 1)
        has_autopay: включено ли автосписание
        renew_amount: сумма предстоящего списания в рублях (строка)
    """
    if not bot.is_configured():
        return

    tier = subscription.tier
    tier_name = tier.get_name_display()
    end_date_str = (
        subscription.end_date.strftime("%d.%m.%Y")
        if subscription.end_date
        else "бессрочно"
    )

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
    if tier.fancoin_per_purchase > 0 or tier.is_unlimited:
        if tier.is_unlimited:
            features_lost.append("• FAN-token (безлимит)")
        else:
            features_lost.append(
                f"• FAN-token ({tier.fancoin_per_purchase} за покупку)"
            )
    if tier.one_day_tournament_discount > 0:
        features_lost.append(
            f"• Скидка {tier.one_day_tournament_discount}% на однодневные турниры"
        )
    if tier.has_admin_support:
        features_lost.append("• Приоритетная поддержка администратора")
    if tier.has_badge:
        features_lost.append("• Особый статус в профиле")

    features_text = (
        "\n".join(features_lost) if features_lost else "• Все функции тарифа"
    )

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
    )
    if days_left == 1 and has_autopay and renew_amount:
        text += (
            f"💳 <b>Автосписание:</b> завтра будет списано <b>{renew_amount} ₽</b> "
            f"с привязанной карты. Отключить автосписание можно в профиле на сайте.\n\n"
        )
    text += (
        "💡 <b>Продлите подписку сейчас</b> и сохраните доступ ко всем функциям!\n"
        "Не упустите возможность участвовать в турнирах и общаться с сообществом."
    )

    site_base_url = _get_site_base_url()
    pricing_url = f"{site_base_url.rstrip('/')}/subscriptions/pricing/"

    reply_markup = {
        "inline_keyboard": [
            [{"text": "💳 Продлить подписку", "url": pricing_url}],
            [{"text": "📋 Моя подписка", "callback_data": "menu_my_subscription"}],
        ],
    }
    if days_left == 1 and has_autopay:
        from django.urls import reverse

        try:
            player = getattr(user, "player", None)
            if player is not None:
                profile_url = reverse("profile", kwargs={"pk": player.pk})
                profile_full_url = f"{site_base_url.rstrip('/')}{profile_url}"
                reply_markup["inline_keyboard"].append(
                    [{"text": "🚫 Отключить автосписание", "url": profile_full_url}]
                )
        except Exception:
            pass

    ok = send_to_user_by_user(
        user, text, reply_markup=reply_markup, event_key="subscription.expiring"
    )
    logger.info(
        "Истечение подписки: user_id={}, tier_id={}, days_left={}, queued_or_email_accepted={}",
        user.pk,
        tier.pk,
        days_left,
        ok,
    )


def notify_sparring_response(sparring_response: SparringResponse) -> None:
    """Уведомить автора о новом отклике, сохранив Telegram вместе с откликом.

    Письмо с данными игрока и контактами отправляется после успешного коммита,
    независимо от наличия Telegram-бота. Повторный email-дубль не создаётся.
    """
    request = sparring_response.sparring_request
    respondent = sparring_response.respondent
    author = request.player.user

    from apps.core.email_service import send_sparring_response_email

    def deliver_email() -> None:
        """Отправить письмо после сохранения отклика, не отменяя его при сбое почты."""
        try:
            email_accepted = send_sparring_response_email(
                author, request=request, response=sparring_response
            )
        except Exception as exc:
            logger.warning(
                "Письмо об отклике не отправлено: response_id={}, error={}",
                sparring_response.pk,
                type(exc).__name__,
            )
            return
        logger.info(
            "Письмо об отклике: response_id={}, email_accepted={}",
            sparring_response.pk,
            email_accepted,
        )

    transaction.on_commit(deliver_email, robust=True)

    if not bot.is_configured():
        logger.info(
            "Отклик на спарринг: Telegram-бот не настроен"
            " | request_id={}, respondent_id={}",
            request.pk,
            respondent.pk,
        )
        return

    # Формируем текст сообщения
    lines = [
        "🎾 <b>Новый отклик на вашу заявку!</b>",
        "",
        f"<b>Игрок:</b> {html.escape(str(respondent))}",
    ]
    # Добавляем возраст, если есть
    if respondent.age:
        lines.append(f"<b>Возраст:</b> {respondent.age} лет")

    # Добавляем уровень силы
    if respondent.skill_level:
        skill_display = dict(SkillLevel.choices).get(
            respondent.skill_level, respondent.skill_level
        )
        lines.append(f"<b>Уровень:</b> {html.escape(skill_display)}")

    # Добавляем FAN рейтинг
    if respondent.total_points:
        lines.append(f"<b>Рейтинг (FAN):</b> {int(respondent.total_points)}")

    # Добавляем статистику
    if respondent.matches_played:
        win_rate = (
            (respondent.matches_won / respondent.matches_played * 100)
            if respondent.matches_played > 0
            else 0
        )
        lines.append(
            f"<b>Статистика:</b> {respondent.matches_won}/{respondent.matches_played} побед ({win_rate:.0f}%)"
        )

    contact_links = sparring_response.contact_links()
    if contact_links:
        lines.append("")
        lines.append("<b>Контакты:</b>")
        for contact in contact_links:
            label = html.escape(str(contact.get("label") or ""))
            value = html.escape(str(contact.get("value") or ""))
            url = str(contact.get("url") or "")
            if url and not url.startswith("tel:"):
                lines.append(
                    f'• {label}: <a href="{html.escape(url, quote=True)}">{value}</a>'
                )
            else:
                lines.append(f"• {label}: {value}")

    lines.append("")
    lines.append("Выберите действие:")

    text = "\n".join(lines)

    # Кнопки: Профиль в боте, Подтвердить, Связаться (без ссылок на сайт)
    keyboard = [
        [
            {
                "text": "👤 Профиль игрока",
                "callback_data": f"sparring_profile_{sparring_response.pk}",
            }
        ],
        [
            {
                "text": "✅ Подтвердить",
                "callback_data": f"confirm_match_{sparring_response.pk}",
            }
        ],
    ]
    contact_method = sparring_response.contact_method
    has_contact = (
        (contact_method == "telegram" and respondent.telegram)
        or (contact_method == "whatsapp" and respondent.whatsapp)
        or (contact_method == "max" and respondent.max_contact)
    )
    if has_contact:
        keyboard.append(
            [
                {
                    "text": "💬 Связаться",
                    "callback_data": f"contact_{sparring_response.pk}",
                }
            ]
        )

    reply_markup = {"inline_keyboard": keyboard}

    ok = send_to_user_by_user(
        author,
        text,
        reply_markup=reply_markup,
        event_key="sparring.response",
        skip_email=True,
    )
    logger.info(
        "Отклик на спарринг: request_id={}, respondent_id={}, telegram_queued={}",
        request.pk,
        respondent.pk,
        ok,
    )


def notify_sparring_response_accepted(
    sparring_response: SparringResponse, match: Match
) -> None:
    """Уведомление откликнувшемуся игроку: ваш отклик принят, матч создан (в бот)."""
    if not bot.is_configured():
        return
    respondent_user = getattr(sparring_response.respondent, "user", None)
    if not respondent_user:
        return
    deadline_str = (
        match.deadline.strftime("%d.%m.%Y") if match.deadline else "не указан"
    )
    text = (
        "✅ <b>Ваш отклик на заявку на спарринг принят!</b>\n\n"
        "Матч создан. Внесите результат до дедлайна в разделе «Мои матчи».\n"
        f"Дедлайн: {deadline_str}."
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "📝 Внести результат",
                    "callback_data": f"result_enter_{match.pk}",
                }
            ],
            [{"text": "📅 Мои матчи", "callback_data": "menu_my_matches"}],
        ],
    }
    send_to_user_by_user(
        respondent_user, text, reply_markup=reply_markup, event_key="sparring.accepted"
    )
    logger.info(
        "Принятый отклик: response_id={}, respondent_id={}",
        sparring_response.pk,
        sparring_response.respondent_id,
    )


def notify_doubles_join_accepted(join_request: DoublesJoinRequest) -> None:
    """Сохранить Telegram для всех участников принятого отклика внутри сервисной транзакции."""
    if not bot.is_configured():
        return
    context = (
        "командный спарринг"
        if join_request.match_request.kind == "team"
        else "парный матч 2×2"
    )
    text = (
        f"✅ <b>Ваш отклик на {context} №{join_request.match_request_id} принят!</b>\n\n"
        "Ожидайте формирования матчей автором заявки. Затем матчи появятся в «Мои матчи»."
    )
    for m in join_request.members.select_related("player__user").order_by("order"):
        if m.player and getattr(m.player, "user_id", None):
            send_to_user_by_user(
                m.player.user, text, event_key="sparring.doubles_joined"
            )
    logger.info(
        "Принятый парный отклик: join_request_id={}",
        join_request.pk,
    )


def notify_team_sparring_ready(match_request: DoublesMatchRequest) -> None:
    """
    Уведомление автору командного спарринга:
    команды сформированы (4 участника), можно сформировать матчи.
    """
    if not bot.is_configured():
        return
    author_user = getattr(match_request.created_by, "user", None)
    if not author_user:
        return

    text = (
        "🎾 <b>Командный спарринг готов</b>\n\n"
        "Вы одобрили все отклики, обе команды сформированы (4 участника).\n\n"
        "Нажмите кнопку ниже, чтобы создать серию матчей:\n"
        "• 4 одиночных матча (каждый против каждого из другой команды)\n"
        "• 1 парный матч 2×2\n\n"
        "После создания матчи появятся в разделе «Мои матчи»."
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Сформировать матчи",
                    "callback_data": f"team_sparring_generate_{match_request.pk}",
                }
            ],
            [
                {
                    "text": "📅 Мои матчи",
                    "callback_data": "menu_my_matches",
                }
            ],
        ],
    }
    ok = send_to_user_by_user(
        author_user, text, reply_markup=reply_markup, event_key="sparring.team_ready"
    )
    logger.info(
        "Командный спарринг готов: request_id={}, user_id={}, queued_or_email_accepted={}",
        match_request.pk,
        getattr(author_user, "pk", None),
        ok,
    )


def _sparring_inviter_card_lines(inviter: Player) -> list[str]:
    """Строки HTML-описания пригласившего для Telegram."""
    lines: list[str] = [
        f"<b>{html.escape(str(inviter))}</b>",
    ]
    if inviter.skill_level:
        skill_display = dict(SkillLevel.choices).get(
            inviter.skill_level, inviter.skill_level
        )
        lines.append(f"<b>Сила:</b> {html.escape(str(skill_display))}")
    if inviter.ntrp_level is not None:
        lines.append(f"<b>NTRP:</b> {inviter.ntrp_level}")
    if inviter.total_points:
        lines.append(f"<b>Рейтинг (FAN):</b> {int(inviter.total_points)}")
    return lines


def _notify_by_email_and_telegram(
    user: User | None,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    *,
    event_key: str = "platform.user_message",
) -> bool:
    """Отправить уведомление на почту и в Telegram.

    Письмо уходит и без настроенного бота. Если бот настроен, почту и Telegram
    отправляет ``send_to_user_by_user``, чтобы письмо не ушло дважды.

    Args:
        user: Получатель.
        text: Текст в HTML, как для Telegram.
        reply_markup: Клавиатура Telegram.

    Returns:
        True, если Telegram поставлен в очередь либо email принят транспортом.
    """
    if bot.is_configured():
        return send_to_user_by_user(
            user, text, reply_markup=reply_markup, event_key=event_key
        )
    return _send_notification_email_to_user(user=user, text=text, event_key=event_key)


def notify_sparring_invitation_created(invitation: SparringInvitation) -> None:
    """
    Уведомление приглашённому: приглашение на спарринг (ЛК, почта и Telegram).
    """
    from django.urls import reverse

    invitee_user = invitation.invitee.user
    inviter = invitation.inviter
    friendly = "да" if invitation.is_friendly else "нет"
    date_str = (
        invitation.proposed_date.strftime("%d.%m.%Y")
        if invitation.proposed_date
        else "не указана"
    )
    url = reverse("sparring_my_invitations")
    msg_lk = (
        f"{inviter} пригласил вас на спарринг. Дружеская игра: {friendly}. "
        f"Дата: {date_str}. Откройте «Мои приглашения», чтобы подтвердить."
    )
    if len(msg_lk) > 255:
        msg_lk = msg_lk[:252] + "..."
    try:
        create_notification(
            user=invitee_user, message=msg_lk, url=url, event_key="sparring.invited"
        )
    except Exception as e:
        logger.warning(
            "Приглашение не сохранено в ЛК: user_id={}, error={}",
            getattr(invitee_user, "pk", None),
            type(e).__name__,
        )

    site_base = _get_site_base_url().rstrip("/")
    invitations_url = f"{site_base}{url}"
    lines = [
        "🎾 <b>Приглашение на спарринг</b>",
        "",
        *_sparring_inviter_card_lines(inviter),
        "",
        f"<b>Дружеская игра:</b> {friendly}",
        f"<b>Предполагаемая дата:</b> {date_str}",
        "",
        "Подтвердите приглашение — будет создан матч в «Мои матчи».",
        "",
        invitations_url,
    ]
    text = "\n".join(lines)
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Подтвердить",
                    "callback_data": f"sparring_invite_accept_{invitation.pk}",
                }
            ],
            [{"text": "📋 Мои приглашения", "url": invitations_url}],
        ],
    }
    ok = _notify_by_email_and_telegram(
        invitee_user, text, reply_markup=reply_markup, event_key="sparring.invited"
    )
    logger.info(
        "Приглашение: invitation_id={}, invitee_id={}, queued_or_email_accepted={}",
        invitation.pk,
        getattr(invitee_user, "pk", None),
        ok,
    )


def notify_sparring_invitation_accepted_inviter(
    invitation: SparringInvitation, match: Match
) -> None:
    """
    Уведомление пригласившему: приглашение принято, матч создан (ЛК, почта и Telegram).
    """
    from django.urls import reverse

    inviter_user = invitation.inviter.user
    invitee = invitation.invitee
    deadline_str = (
        match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else "не указан"
    )
    match_url = reverse("match_detail", args=[match.pk])
    msg_lk = f"{invitee} принял(а) ваше приглашение на спарринг. Матч создан. Дедлайн: {deadline_str}."
    if len(msg_lk) > 255:
        msg_lk = msg_lk[:252] + "..."
    create_notification(
        user=inviter_user,
        message=msg_lk,
        url=match_url,
        event_key="sparring.invitation_accepted",
    )

    absolute_match_url = f"{_get_site_base_url().rstrip('/')}{match_url}"
    text = (
        f"✅ <b>Приглашение принято</b>\n\n"
        f"{html.escape(str(invitee))} согласился на спарринг.\n"
        f"Матч: {html.escape(match.get_player1_display())} — "
        f"{html.escape(match.get_player2_display())}\n"
        f"Дедлайн: {deadline_str}\n\n"
        f"{absolute_match_url}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "📝 Внести результат",
                    "callback_data": f"result_enter_{match.pk}",
                }
            ],
            [{"text": "📅 Мои матчи", "callback_data": "menu_my_matches"}],
        ],
    }
    ok = _notify_by_email_and_telegram(
        inviter_user,
        text,
        reply_markup=reply_markup,
        event_key="sparring.invitation_accepted",
    )
    logger.info(
        "Принятое приглашение: invitation_id={}, inviter_id={}, queued_or_email_accepted={}",
        invitation.pk,
        getattr(inviter_user, "pk", None),
        ok,
    )
