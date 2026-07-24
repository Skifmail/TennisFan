"""
Уведомления пользователей в Telegram (пользовательский бот).
Отправка по User или chat_id; тексты для регистрации, матча, предложения результата.
"""

import html
import logging
import threading

from django.conf import settings
from django.core.mail import send_mail
from django.utils.html import strip_tags

from apps.core.models import UserTelegramLink
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


def send_to_user_by_user(
    user,
    text: str,
    reply_markup: dict | None = None,
    *,
    skip_email: bool = False,
) -> bool:
    """Отправить сообщение пользователю по User (если привязан Telegram).

    Args:
        user: Пользователь-получатель.
        text (str): Текст сообщения (HTML для Telegram).
        reply_markup (dict | None): Клавиатура Telegram.
        skip_email (bool): Не дублировать на почту (если письмо уже уходит отдельно).

    Returns:
        bool: ``True``, если доставлено хотя бы в один канал.
    """
    email_ok = False
    if not skip_email:
        email_ok = _send_notification_email_to_user(user=user, text=text)
    chat_id = get_chat_id_for_user(user)
    if chat_id is None:
        return email_ok
    telegram_ok = bot.send_to_user(chat_id, text, reply_markup=reply_markup)
    return telegram_ok or email_ok


def _send_notification_email_to_user(user, text: str) -> bool:
    """Отправить пользователю email-дубль Telegram-уведомления.

    Args:
        user: Пользователь-получатель уведомления.
        text (str): Текст Telegram-уведомления (HTML-формат).

    Returns:
        bool: ``True``, если email отправлен успешно, иначе ``False``.
    """
    if not bool(getattr(settings, "USER_NOTIFICATIONS_EMAIL_ENABLED", True)):
        return False
    if user is None:
        return False
    recipient = str(getattr(user, "email", "") or "").strip()
    if not recipient or "@" not in recipient:
        return False

    body = strip_tags(text or "").strip()
    if not body:
        return False
    subject = _build_user_email_subject(body)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru")

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
        return True
    except Exception as exc:
        logger.warning(
            "User notification email failed for user_id=%s email=%s: %s",
            getattr(user, "pk", None),
            recipient,
            exc,
        )
        return False


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
    send_to_user_by_user(user, text)


def notify_tournament_removed_refund(user, tournament, feedback_url: str) -> None:
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
    send_to_user_by_user(user, text, reply_markup=reply_markup)


def notify_postpayment_opened(
    user, tournament, amount: str, due_at_text: str, payment_url: str
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
    send_to_user_by_user(user, text)


def notify_postpayment_1h_reminder(
    user, tournament, due_at_text: str, payment_url: str
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
    send_to_user_by_user(user, text)


def notify_postpayment_removed(user, tournament) -> None:
    """Уведомить игрока об удалении из турнира за неоплату."""
    if not bot.is_configured():
        return
    text = (
        f"⚠️ <b>Регистрация отменена</b>\n\n"
        f"Вы удалены из турнира «{tournament.name}», потому что срок постоплаты истёк."
    )
    send_to_user_by_user(user, text)


def _player_contact_lines(player) -> list[str]:
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


def _opponent_contacts_block(opponents: list) -> str:
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


def _match_info_text(match, opponents: list | None = None) -> str:
    """Текст с информацией о матче для уведомления (без ссылки на сайт).

    Args:
        match: Объект ``Match``.
        opponents (list | None): Соперники получателя для блока контактов.
            Если ``None`` — блок контактов не добавляется (общий текст).

    Returns:
        str: HTML-текст уведомления о новом матче.
    """
    side1 = match.get_player1_display()
    side2 = match.get_player2_display()
    deadline_str = (
        match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else "не указан"
    )

    # Для спарринговых матчей tournament может быть None
    if match.is_sparring():
        tournament_info = "Спарринг (личная встреча)"
        round_info = "—"
    else:
        tournament_info = match.tournament.name if match.tournament else "—"
        if (
            match.tournament
            and getattr(match.tournament, "format", None) == "weekend_day"
        ):
            tournament_info = f"{tournament_info} (OneDay)"
        round_info = match.round_name or "—"

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


def notify_bracket_formed(tournament, subtitle: str | None = None) -> None:
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
            Notification.objects.create(user=user, message=message_lk, url=url)
        except Exception as e:
            logger.warning(
                "notify_bracket_formed Notification for user %s: %s", user.pk, e
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
        send_to_user_by_user(user, text, reply_markup=reply_markup)


def notify_match_created(match) -> None:
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
        send_to_user_by_user(player.user, text, reply_markup=reply_markup)


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
            score = (
                " / ".join(
                    f"{getattr(proposal, f'player1_set{i}')}:{getattr(proposal, f'player2_set{i}')}"
                    for i in (1, 2, 3)
                    if getattr(proposal, f"player1_set{i}") is not None
                )
                or "—"
            )
        except Exception:
            score = "—"
        warning_text = ""

    tournament_name = match.tournament.name if match.tournament else "спарринг"
    text = (
        f"📩 <b>{proposer} внёс результат матча</b>\n\n"
        f"Турнир: {tournament_name}\n"
        f"Результат: {result_text}\n"
        f"Счёт: {score}{warning_text}\n\n"
        "✅ Матч завершён."
    )
    for user in get_match_opponent_users(match, proposer):
        send_to_user_by_user(user, text)


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
        return str(proposal.get_result_display())
    except Exception:
        return str(result_val or "—")


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


def notify_result_confirmed_to_participants(match) -> None:
    """
    Уведомление всем участникам матча в Telegram: результат подтверждён,
    начислено/вычтено очков рейтинга, прогресс силы.
    """
    if not bot.is_configured():
        return
    from apps.tournaments.utils import get_match_participants

    participants = [
        p
        for p in get_match_participants(match)
        if p and not getattr(p, "is_bye", False) and getattr(p, "user_id", None)
    ]
    is_friendly = match.is_friendly_sparring()
    winner = match.winner
    winner_team = match.winner_team

    for p in participants:
        user = getattr(p, "user", None)
        if not user:
            continue
        is_winner = (
            p == winner
            or (winner_team and p in (winner_team.player1, winner_team.player2))
            or (
                match.is_doubles_sparring()
                and winner
                and p.pk in (match.player1_id, match.partner1_id)
                and winner.pk in (match.player1_id, match.partner1_id)
            )
            or (
                match.is_doubles_sparring()
                and winner
                and p.pk in (match.player2_id, match.partner2_id)
                and winner.pk in (match.player2_id, match.partner2_id)
            )
        )
        if is_friendly:
            result_line = "Вы выиграли." if is_winner else "Вы проиграли."
        else:
            p.refresh_from_db()
            changes = p.get_rating_changes()
            fan = changes.get("fan", {})
            delta = fan.get("delta") or 0
            from apps.users.rating_utils import rating_to_ntrp_level

            if delta != 0:
                d_str = f"+{int(delta)}" if delta > 0 else str(int(delta))
                rating_before = float(p.total_points) - float(delta)
                ntrp_before = rating_to_ntrp_level(rating_before)
                ntrp_after = rating_to_ntrp_level(float(p.total_points))
                result_line = (
                    f"Вы выиграли. Вам начислено {d_str} очков рейтинга. Сила: {ntrp_before:.1f} → {ntrp_after:.1f}."
                    if is_winner
                    else f"Вы проиграли. У вас вычтено {abs(int(delta))} очков рейтинга. Сила: {ntrp_before:.1f} → {ntrp_after:.1f}."
                )
            else:
                result_line = "Вы выиграли." if is_winner else "Вы проиграли."

        text = (
            "✅ <b>Результат матча подтверждён</b>\n\n"
            f"Матч: {match.get_player1_display()} — {match.get_player2_display()}\n"
            f"Счёт: {match.score_display() or '—'}\n\n"
            f"{result_line}"
        )
        send_to_user_by_user(user, text)
    logger.info(
        "notify_result_confirmed_to_participants: match=%s, participants=%s",
        match.pk,
        len(participants),
    )


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

    # Определяем информацию о турнире/спарринге
    if match.tournament:
        tournament_info = f"Турнир: {match.tournament.name}"
    elif match.is_sparring():
        tournament_info = "Спарринг (личная встреча)"
    else:
        tournament_info = "Матч"

    text = (
        "✅ <b>Результат подтверждён</b>\n\n"
        f"{tournament_info}\n"
        f"Матч: {match.get_player1_display()} vs {match.get_player2_display()}\n"
        f"Результат: {result}\n"
        f"Счёт: {score}\n"
        f"Победитель: {winner_name}{penalty_text}\n\n"
        f"<b>{opponent}</b> подтвердил(а) результат."
    )
    ok = send_to_user_by_user(proposer_user, text)
    logger.info(
        "notify_proposal_confirmed: proposer=%s, user=%s, sent=%s",
        proposal.proposer,
        proposer_user,
        ok,
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
    ok = send_to_user_by_user(proposer_user, text)
    logger.info(
        "notify_proposal_rejected: proposer=%s, user=%s, sent=%s",
        proposal.proposer,
        proposer_user,
        ok,
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

    # Определяем информацию о турнире/спарринге
    if match.tournament:
        tournament_info = f"Турнир: {match.tournament.name}"
    elif match.is_sparring():
        tournament_info = "Спарринг (личная встреча)"
    else:
        tournament_info = "Матч"

    text = (
        f"⏰ <b>Напоминание: до дедлайна матча {days_left} дн.</b>\n\n"
        f"{tournament_info}\n"
        f"Этап: {match.round_name or '—'}\n"
        f"{side1} — {side2}\n"
        f"Дедлайн: {deadline_str}"
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
    send_to_user_by_user(user, text)


def _format_new_tournament_message(tournament) -> str:
    """Формирует подробный текст уведомления о новом турнире (HTML для Telegram)."""
    format_display = (
        "OneDay"
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
            f"📅 Начало: {tournament.start_date.strftime('%d.%m.%Y')}",
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


def _get_new_tournament_users(tournament) -> list:
    """Вернуть пользователей для ЛК и email о новом турнире.

    Args:
        tournament: Созданный турнир.

    Returns:
        list: Список User (платформенный турнир — все игроки; клубный — активные члены).
    """
    from apps.users.models import User

    if getattr(tournament, "club_id", None):
        from apps.clubs.models import ClubMember, ClubMemberStatus

        user_ids = list(
            ClubMember.objects.filter(
                club_id=tournament.club_id,
                status=ClubMemberStatus.ACTIVE,
            ).values_list("user_id", flat=True)
        )
        if not user_ids:
            return []
        return list(User.objects.filter(pk__in=user_ids).order_by("pk"))

    return list(
        User.objects.filter(player__isnull=False, player__is_bye=False)
        .distinct()
        .order_by("pk")
    )


def _notify_new_tournament_lk_and_email(tournament) -> tuple[int, int]:
    """Создать ЛК-уведомления и отправить email о новом турнире.

    Args:
        tournament: Созданный турнир.

    Returns:
        tuple[int, int]: Число ЛК-уведомлений и успешно отправленных писем.
    """
    from django.urls import reverse

    from apps.core.email_service import send_new_tournament_email
    from apps.users.models import Notification

    users = _get_new_tournament_users(tournament)
    url = reverse("tournament_detail", args=[tournament.slug])
    message_lk = f"Новый турнир: «{tournament.name}». Приглашаем принять участие!"
    if len(message_lk) > 255:
        message_lk = message_lk[:252] + "..."

    lk_count = 0
    email_count = 0
    for user in users:
        try:
            Notification.objects.create(user=user, message=message_lk, url=url)
            lk_count += 1
        except Exception as exc:
            logger.warning(
                "New tournament LK notify failed for user %s: %s", user.pk, exc
            )
        try:
            if send_new_tournament_email(user, tournament):
                email_count += 1
        except Exception as exc:
            logger.warning("New tournament email failed for user %s: %s", user.pk, exc)
    return lk_count, email_count


def _get_new_tournament_recipients(tournament) -> list[UserTelegramLink]:
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

    return list(
        UserTelegramLink.objects.filter(user_bot_chat_id__isnull=False)
        .exclude(user_bot_chat_id=0)
        .distinct()
    )


def _send_new_tournament_notification(tournament_pk: int) -> None:
    """В фоне отправить ЛК, email и Telegram о новом турнире."""
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
            logger.warning(
                "New tournament notify: tournament pk=%s not found", tournament_pk
            )
            return

        lk_count, email_count = _notify_new_tournament_lk_and_email(tournament)
        logger.info(
            "New tournament pk=%s: LK=%s email=%s",
            tournament_pk,
            lk_count,
            email_count,
        )

        if not bot.is_configured():
            logger.warning(
                "New tournament notify: bot not configured, skip Telegram, pk=%s",
                tournament_pk,
            )
            return
        links = _get_new_tournament_recipients(tournament)
        total = len(links)
        if total == 0:
            logger.info(
                "New tournament pk=%s: no Telegram recipients",
                tournament_pk,
            )
            return
        message_text = _format_new_tournament_message(tournament)
        sent = 0
        for link in links:
            try:
                if bot.send_to_user(link.user_bot_chat_id, message_text):
                    sent += 1
            except Exception as e:
                logger.warning(
                    "New tournament notify to %s failed: %s", link.user_bot_chat_id, e
                )
        logger.info(
            "New tournament pk=%s Telegram notified %s/%s users",
            tournament_pk,
            sent,
            total,
        )
    except Exception as e:
        logger.exception(
            "_send_new_tournament_notification pk=%s failed: %s", tournament_pk, e
        )


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
            logger.warning(
                "notify_tournament_start Notification for user %s: %s", user.pk, e
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
        send_to_user_by_user(user, text, reply_markup=reply_markup)


def notify_new_tournament_by_pk(tournament_pk: int) -> None:
    """
    Запуск рассылки о новом турнире по pk. Вызывать после transaction.on_commit(),
    чтобы турнир был уже закоммичен и виден в фоновом потоке.

    Рассылка включает ЛК-уведомления и email всем релевантным игрокам;
    Telegram — при настроенном боте.
    """
    logger.info("New tournament pk=%s, starting background notify", tournament_pk)
    thread = threading.Thread(
        target=_send_new_tournament_notification,
        args=(tournament_pk,),
        daemon=True,
        name=f"notify_tournament_{tournament_pk}",
    )
    thread.start()


def notify_subscription_expiring(
    user,
    subscription,
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

    ok = send_to_user_by_user(user, text, reply_markup=reply_markup)
    logger.info(
        "notify_subscription_expiring: user=%s, tier=%s, days_left=%s, sent=%s",
        user,
        tier_name,
        days_left,
        ok,
    )


def notify_sparring_response(sparring_response) -> None:
    """
    Уведомление автору заявки о новом отклике на спарринг.

    Отправляет сообщение с данными откликнувшегося игрока и кнопками:
    - Получить контакт
    - Профиль игрока
    - Подтвердить игру
    """
    if not bot.is_configured():
        return

    request = sparring_response.sparring_request
    respondent = sparring_response.respondent
    author = request.player.user

    # Формируем текст сообщения
    lines = [
        "🎾 <b>Новый отклик на вашу заявку!</b>",
        "",
        f"<b>Игрок:</b> {respondent}",
    ]

    # Добавляем возраст, если есть
    if respondent.age:
        lines.append(f"<b>Возраст:</b> {respondent.age} лет")

    # Добавляем уровень силы
    if respondent.skill_level:
        skill_display = dict(SkillLevel.choices).get(
            respondent.skill_level, respondent.skill_level
        )
        lines.append(f"<b>Уровень:</b> {skill_display}")

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

    ok = send_to_user_by_user(author, text, reply_markup=reply_markup)
    logger.info(
        "notify_sparring_response: request=%s, respondent=%s, sent=%s",
        request.pk,
        respondent.pk,
        ok,
    )


def notify_sparring_response_accepted(sparring_response, match) -> None:
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
    send_to_user_by_user(respondent_user, text, reply_markup=reply_markup)
    logger.info(
        "notify_sparring_response_accepted: response=%s, respondent=%s",
        sparring_response.pk,
        sparring_response.respondent_id,
    )


def notify_doubles_join_accepted(join_request) -> None:
    """Уведомление всем участникам отклика (парный 2×2): ваш отклик принят (в бот)."""
    if not bot.is_configured():
        return
    text = (
        "✅ <b>Ваш отклик на парный матч 2×2 принят!</b>\n\n"
        "Ожидайте подтверждения матча автором заявки. После подтверждения матч появится в «Мои матчи»."
    )
    for m in join_request.members.select_related("player__user"):
        if m.player and getattr(m.player, "user_id", None):
            send_to_user_by_user(m.player.user, text)
    logger.info(
        "notify_doubles_join_accepted: join_request=%s",
        join_request.pk,
    )


def notify_team_sparring_ready(match_request) -> None:
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
    ok = send_to_user_by_user(author_user, text, reply_markup=reply_markup)
    logger.info(
        "notify_team_sparring_ready: request=%s, user=%s, sent=%s",
        match_request.pk,
        getattr(author_user, "pk", None),
        ok,
    )


def _sparring_inviter_card_lines(inviter) -> list[str]:
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


def notify_sparring_invitation_created(invitation) -> None:
    """
    Уведомление приглашённому: приглашение на спарринг (ЛК и Telegram с кнопкой «Подтвердить»).
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
        Notification.objects.create(user=invitee_user, message=msg_lk, url=url)
    except Exception as e:
        logger.warning(
            "notify_sparring_invitation_created LK failed user=%s: %s",
            getattr(invitee_user, "pk", None),
            e,
        )

    if not bot.is_configured():
        return

    lines = [
        "🎾 <b>Приглашение на спарринг</b>",
        "",
        *_sparring_inviter_card_lines(inviter),
        "",
        f"<b>Дружеская игра:</b> {friendly}",
        f"<b>Предполагаемая дата:</b> {date_str}",
        "",
        "Подтвердите приглашение — будет создан матч в «Мои матчи».",
    ]
    text = "\n".join(lines)
    site_base = _get_site_base_url().rstrip("/")
    invitations_url = f"{site_base}{url}"
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
    ok = send_to_user_by_user(invitee_user, text, reply_markup=reply_markup)
    logger.info(
        "notify_sparring_invitation_created: invitation=%s invitee=%s sent=%s",
        invitation.pk,
        getattr(invitee_user, "pk", None),
        ok,
    )


def notify_sparring_invitation_accepted_inviter(invitation, match) -> None:
    """
    Уведомление пригласившему: приглашение принято, матч создан (ЛК и Telegram).
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
    try:
        Notification.objects.create(user=inviter_user, message=msg_lk, url=match_url)
    except Exception as e:
        logger.warning(
            "notify_sparring_invitation_accepted_inviter LK failed user=%s: %s",
            getattr(inviter_user, "pk", None),
            e,
        )

    if not bot.is_configured():
        return
    text = (
        f"✅ <b>Приглашение принято</b>\n\n"
        f"{invitee} согласился на спарринг.\n"
        f"Матч: {html.escape(match.get_player1_display())} — "
        f"{html.escape(match.get_player2_display())}\n"
        f"Дедлайн: {deadline_str}"
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
    ok = send_to_user_by_user(inviter_user, text, reply_markup=reply_markup)
    logger.info(
        "notify_sparring_invitation_accepted_inviter: invitation=%s inviter=%s sent=%s",
        invitation.pk,
        getattr(inviter_user, "pk", None),
        ok,
    )
