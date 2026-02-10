"""
Webhook пользовательского Telegram-бота и редирект привязки с сайта.
"""

import json
import logging

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from django.core.cache import cache
from django.db.models import Q
from django.urls import reverse

from apps.core.models import UserTelegramLink
from apps.users.models import Notification, Player
from apps.tournaments.models import DeadlineExtensionRequest, Match, MatchResultProposal
from apps.tournaments.utils import get_match_opponent_users, get_match_participants
from apps.tournaments.proposal_service import apply_proposal
from apps.core import telegram_notify as admin_notify

from . import services as bot
from . import notifications as tg_notify

logger = logging.getLogger(__name__)

CACHE_KEY_RESULT_ENTRY = "tg_result_entry:%s"
CACHE_RESULT_ENTRY_TIMEOUT = 300  # 5 min


def _parse_score_input(text: str):
    """
    Парсит счёт вида "6:4 6:3" или "6:4 3:6 10:7".
    Возвращает (sets_list, None) или (None, error_msg).
    sets_list = [(games_side1, games_side2), ...] для 1–3 сетов.
    """
    text = (text or "").strip().replace(",", " ")
    parts = text.split()
    if not parts or len(parts) > 3:
        return None, "Укажите 1–3 сета через пробел, например: 6:4 6:3"
    sets_list = []
    for i, part in enumerate(parts):
        if ":" in part:
            a, _, b = part.partition(":")
        elif "-" in part:
            a, _, b = part.partition("-")
        else:
            return None, "Формат сета: геймы:геймы, например 6:4"
        try:
            ga, gb = int(a.strip()), int(b.strip())
        except ValueError:
            return None, "Геймы должны быть числами"
        if ga < 0 or gb < 0 or ga > 20 or gb > 20:
            return None, "Геймы: от 0 до 7 (или до 10 в тайбрейке)"
        sets_list.append((ga, gb))
    return sets_list, None


def _proposer_is_side1(match: Match, player: Player) -> bool:
    """Играет ли участник за первую сторону (player1 / team1)."""
    if match.team1_id and match.team2_id:
        return match.team1 and player in (match.team1.player1, match.team1.player2)
    return match.player1_id == player.pk


def _webhook_secret_ok(request) -> bool:
    """Проверка секрета webhook (X-Telegram-Bot-Api-Secret-Token)."""
    secret = getattr(settings, "TELEGRAM_USER_BOT_WEBHOOK_SECRET", None) or ""
    if not secret:
        return True
    return request.headers.get("X-Telegram-Bot-Api-Secret-Token") == secret


# Тексты кнопок реплай-меню (должны совпадать при обработке сообщения)
REPLY_BTN_MY_PROFILE = "👤 Мой профиль"
REPLY_BTN_MY_MATCHES = "🎾 Мои матчи"
REPLY_BTN_MY_SUBSCRIPTIONS = "📋 Мои подписки"
REPLY_BTN_GO_TO_SITE = "🌐 Перейти на сайт"

REPLY_MENU_BUTTONS = (REPLY_BTN_MY_PROFILE, REPLY_BTN_MY_MATCHES, REPLY_BTN_MY_SUBSCRIPTIONS, REPLY_BTN_GO_TO_SITE)


def _reply_menu_keyboard():
    """Реплай-клавиатура под полем ввода: Мой профиль, Мои матчи, Мои подписки, Перейти на сайт."""
    return {
        "keyboard": [
            [{"text": REPLY_BTN_MY_PROFILE}, {"text": REPLY_BTN_MY_MATCHES}],
            [{"text": REPLY_BTN_MY_SUBSCRIPTIONS}, {"text": REPLY_BTN_GO_TO_SITE}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _main_menu_keyboard(site_base_url: str):
    """Inline-клавиатура (дублирует меню для старых клиентов): Мои матчи, Мой профиль, Подписка."""
    return {
        "inline_keyboard": [
            [
                {"text": "🎾 Мои матчи", "callback_data": "menu_my_matches"},
                {"text": "👤 Мой профиль", "callback_data": "menu_my_profile"},
            ],
            [
                {"text": "📋 Моя подписка", "callback_data": "menu_my_subscription"},
                {"text": "🌐 На сайт", "url": site_base_url.rstrip("/")},
            ],
        ]
    }


def _get_link_by_chat_id(chat_id) -> UserTelegramLink | None:
    """Найти привязку по chat_id (бот поддержки или пользовательский бот)."""
    if chat_id is None:
        return None
    return UserTelegramLink.objects.filter(
        Q(telegram_chat_id=chat_id) | Q(user_bot_chat_id=chat_id)
    ).first()


def _get_site_base_url() -> str:
    """Базовый URL сайта для ссылок в боте (без слэжа в конце)."""
    base = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", None) or ""
    if base:
        return base.rstrip("/") + "/"
    # Fallback для разработки
    return "https://tennisfan.ru/" if not settings.DEBUG else "http://localhost:8000/"


def _answer_callback(callback_query_id: str, text: str | None = None, show_alert: bool = False) -> None:
    """Ответить на callback_query в Telegram (убрать «часики», опционально показать текст)."""
    token = bot._get_bot_token()
    if not token or not callback_query_id:
        return
    payload = {"callback_query_id": str(callback_query_id)}
    if text:
        payload["text"] = text[:200]
    if show_alert:
        payload["show_alert"] = True
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json=payload,
            timeout=5,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning("answerCallbackQuery failed: %s", e)


def _edit_message_remove_reply_markup(chat_id: int, message_id: int) -> None:
    """Убрать inline-кнопки у сообщения (после подтверждения/отклонения)."""
    token = bot._get_bot_token()
    if not token:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
            timeout=5,
        )
        r.raise_for_status()
    except Exception as e:
        logger.debug("editMessageReplyMarkup failed: %s", e)


def _edit_message_text(chat_id: int, message_id: int, text: str) -> None:
    """Редактировать текст сообщения в Telegram."""
    token = bot._get_bot_token()
    if not token:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
        r.raise_for_status()
    except Exception as e:
        logger.debug("editMessageText failed: %s", e)


def _handle_proposal_callback(callback_query: dict, base_url: str) -> bool:
    """
    Обработка callback proposal_confirm_<pk> / proposal_reject_<pk>.
    Возвращает True, если callback обработан (подтверждение/отклонение результата).
    """
    callback_data = (callback_query.get("callback_data") or "").strip()
    cq_id = callback_query.get("id")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    if not callback_data.startswith("proposal_confirm_") and not callback_data.startswith("proposal_reject_"):
        return False

    prefix = "proposal_confirm_" if callback_data.startswith("proposal_confirm_") else "proposal_reject_"
    try:
        pk = int(callback_data[len(prefix):])
    except (ValueError, TypeError):
        _answer_callback(cq_id, "Неверные данные.", show_alert=True)
        return True

    proposal = (
        MatchResultProposal.objects.select_related(
            "match__tournament", "match__player1", "match__player2",
            "match__team1__player1", "match__team1__player2",
            "match__team2__player1", "match__team2__player2",
            "proposer__user",
        )
        .filter(pk=pk)
        .first()
    )
    if not proposal:
        _answer_callback(cq_id, "Предложение не найдено.", show_alert=True)
        return True
    if proposal.status != Match.ProposalStatus.PENDING:
        _answer_callback(cq_id, "Этот результат уже обработан.", show_alert=True)
        return True

    if not chat_id:
        _answer_callback(cq_id, "Ошибка чата.", show_alert=True)
        return True

    link = _get_link_by_chat_id(chat_id)
    if not link:
        _answer_callback(cq_id, "Подключите бота с сайта (профиль → Telegram).", show_alert=True)
        return True

    user = link.user
    player = getattr(user, "player", None)
    if not player:
        _answer_callback(cq_id, "Создайте профиль игрока на сайте.", show_alert=True)
        return True

    participants = get_match_participants(proposal.match)
    if player not in participants:
        _answer_callback(cq_id, "Вы не участвуете в этом матче.", show_alert=True)
        return True
    if proposal.proposer_id == player.pk:
        _answer_callback(cq_id, "Вы не можете подтверждать свой запрос.", show_alert=True)
        return True
    opponent_users = get_match_opponent_users(proposal.match, proposal.proposer)
    if user not in opponent_users:
        _answer_callback(
            cq_id,
            "Подтвердить результат может только соперник (ваш сокомандник вносил результат).",
            show_alert=True,
        )
        return True

    if callback_data.startswith("proposal_confirm_"):
        try:
            # Сохраняем оригинальный текст сообщения для редактирования
            original_message_text = None
            if message_id:
                # Получаем оригинальный текст из callback_query
                message_text = message.get("text", "")
                if not message_text:
                    # Пробуем получить из другого формата сообщения
                    message_text = message.get("caption", "")
                if message_text:
                    original_message_text = message_text
            
            apply_proposal(proposal)
            tg_notify.notify_proposal_confirmed(proposal)
            
            # Редактируем сообщение, добавляя строку о подтверждении
            if message_id and original_message_text:
                updated_text = original_message_text + "\n\n✅ <b>Результат подтверждён.</b>"
                _edit_message_text(chat_id, message_id, updated_text)
            elif message_id:
                _edit_message_remove_reply_markup(chat_id, message_id)
            
            if cq_id:
                _answer_callback(cq_id, "✅ Результат подтверждён.")
        except Exception as e:
            logger.exception("apply_proposal in webhook: %s", e)
            if cq_id:
                _answer_callback(cq_id, "❌ Ошибка при подтверждении.", show_alert=True)
    else:
        try:
            tg_notify.notify_proposal_rejected(proposal)
            Notification.objects.create(
                user=proposal.proposer.user,
                message=f"{player} отклонил результат матча. Введите свой результат.",
                url=reverse("my_matches"),
            )
            proposal.delete()
            if message_id:
                _edit_message_remove_reply_markup(chat_id, message_id)
            if cq_id:
                _answer_callback(cq_id, "❌ Результат отклонён.")
        except Exception as e:
            logger.exception("proposal reject in webhook: %s", e)
            if cq_id:
                _answer_callback(cq_id, "❌ Ошибка при отклонении.", show_alert=True)

    return True


def _handle_extension_request_callback(callback_query: dict, base_url: str) -> bool:
    """
    Обработка callback extension_request_<match_pk>: создать запрос на продление, уведомить админа.
    Возвращает True, если callback обработан.
    """
    callback_data = (callback_query.get("callback_data") or "").strip()
    if not callback_data.startswith("extension_request_"):
        return False

    cq_id = callback_query.get("id")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")

    try:
        match_pk = int(callback_data[len("extension_request_"):])
    except (ValueError, TypeError):
        _answer_callback(cq_id, "Неверные данные.", show_alert=True)
        return True

    match = (
        Match.objects.select_related("tournament", "player1", "player2", "team1", "team2")
        .filter(
            pk=match_pk,
            status=Match.MatchStatus.SCHEDULED,
            deadline__isnull=False,
        )
        .first()
    )
    if not match:
        _answer_callback(cq_id, "Матч не найден или дедлайн уже прошёл.", show_alert=True)
        return True

    if not chat_id:
        _answer_callback(cq_id, "Ошибка чата.", show_alert=True)
        return True

    link = _get_link_by_chat_id(chat_id)
    if not link:
        _answer_callback(cq_id, "Подключите бота с сайта (профиль → Telegram).", show_alert=True)
        return True

    user = link.user
    player = getattr(user, "player", None)
    if not player:
        _answer_callback(cq_id, "Создайте профиль игрока на сайте.", show_alert=True)
        return True

    participants = get_match_participants(match)
    if player not in participants:
        _answer_callback(cq_id, "Вы не участвуете в этом матче.", show_alert=True)
        return True

    # Один активный запрос на матч от этого игрока
    existing = DeadlineExtensionRequest.objects.filter(
        match=match,
        requested_by=player,
        status=DeadlineExtensionRequest.Status.PENDING,
    ).exists()
    if existing:
        _answer_callback(cq_id, "Запрос на продление уже отправлен.", show_alert=True)
        return True

    ext = DeadlineExtensionRequest.objects.create(
        match=match,
        requested_by=player,
        status=DeadlineExtensionRequest.Status.PENDING,
    )
    admin_url = base_url.rstrip("/") + f"/admin/tournaments/deadlineextensionrequest/{ext.pk}/change/"
    admin_list_url = base_url.rstrip("/") + "/admin/tournaments/deadlineextensionrequest/"
    deadline_str = match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else "—"
    text_for_admin = (
        f"🔄 <b>Запрос на продление дедлайна</b>\n\n"
        f"Игрок: {player}\n"
        f"Матч: {match} ({match.tournament.name})\n"
        f"Текущий дедлайн: {deadline_str}\n\n"
        f"<a href=\"{admin_list_url}\">Список запросов в админке</a>"
    )
    try:
        admin_notify.send_admin_message(text_for_admin)
    except Exception as e:
        logger.warning("Notify admin about extension request: %s", e)

    _answer_callback(cq_id, "Запрос отправлен. Администратор рассмотрит его в ближайшее время.")
    return True


def _handle_result_enter_callback(callback_query: dict) -> bool:
    """
    Кнопка «Внести результат»: показываем выбор типа результата (обычный матч, тех. победа, тех. поражение).
    """
    callback_data = (callback_query.get("callback_data") or "").strip()
    if not callback_data.startswith("result_enter_"):
        return False

    cq_id = callback_query.get("id")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return False

    try:
        match_pk = int(callback_data[len("result_enter_"):])
    except (ValueError, TypeError):
        _answer_callback(cq_id, "Ошибка данных.", show_alert=True)
        return True

    link = _get_link_by_chat_id(chat_id)
    if not link:
        _answer_callback(cq_id, "Сначала подключите бота с сайта.", show_alert=True)
        return True

    player = getattr(link.user, "player", None)
    if not player:
        _answer_callback(cq_id, "Нет профиля игрока.", show_alert=True)
        return True

    match = (
        Match.objects.filter(pk=match_pk)
        .select_related("tournament", "player1", "player2", "team1", "team2")
        .first()
    )
    if not match:
        _answer_callback(cq_id, "Матч не найден.", show_alert=True)
        return True

    if match.result_proposals.filter(status=Match.ProposalStatus.PENDING).exists():
        _answer_callback(
            cq_id,
            "По этому матчу уже отправлен результат и он ожидает подтверждения.",
            show_alert=True,
        )
        return True

    participants = get_match_participants(match)
    if player not in participants:
        _answer_callback(cq_id, "Вы не участвуете в этом матче.", show_alert=True)
        return True

    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        _answer_callback(cq_id, "Матч уже завершён.", show_alert=True)
        return True

    side_text = "первую ({}).".format(match.get_player1_display()) if _proposer_is_side1(match, player) else "вторую ({}).".format(match.get_player2_display())
    
    # Показываем кнопки выбора типа результата
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📝 Обычный матч (ввести счёт)", "callback_data": f"result_type_{match_pk}_normal"}
            ],
            [
                {"text": "✅ Тех. победа (соперник не вышел)", "callback_data": f"result_type_{match_pk}_walkover_win"},
                {"text": "❌ Тех. поражение (мы не вышли)", "callback_data": f"result_type_{match_pk}_walkover_loss"}
            ],
        ]
    }
    
    bot.send_message(
        chat_id,
        f"📝 <b>Внести результат</b>\n\n"
        f"Матч: {match.tournament.name}, {match.round_name or '—'}\n"
        f"{match.get_player1_display()} — {match.get_player2_display()}\n\n"
        f"Вы играете за {side_text}\n\n"
        f"Выберите тип результата:",
        reply_markup=keyboard,
    )
    _answer_callback(cq_id, "Выберите тип результата")
    return True


def _handle_result_type_callback(callback_query: dict) -> bool:
    """
    Обработка выбора типа результата: обычный матч (просим счёт) или тех. победа/поражение (создаём proposal сразу).
    """
    callback_data = (callback_query.get("callback_data") or "").strip()
    if not callback_data.startswith("result_type_"):
        return False

    cq_id = callback_query.get("id")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return False

    try:
        # Формат: result_type_<match_pk>_<type>
        parts = callback_data.split("_")
        if len(parts) < 4:
            _answer_callback(cq_id, "Ошибка данных.", show_alert=True)
            return True
        match_pk = int(parts[2])
        result_type = "_".join(parts[3:])  # Может быть "normal", "walkover_win", "walkover_loss"
    except (ValueError, TypeError, IndexError):
        _answer_callback(cq_id, "Ошибка данных.", show_alert=True)
        return True

    link = _get_link_by_chat_id(chat_id)
    if not link:
        _answer_callback(cq_id, "Сначала подключите бота с сайта.", show_alert=True)
        return True

    player = getattr(link.user, "player", None)
    if not player:
        _answer_callback(cq_id, "Нет профиля игрока.", show_alert=True)
        return True

    match = (
        Match.objects.filter(pk=match_pk)
        .select_related("tournament", "player1", "player2", "team1", "team2")
        .first()
    )
    if not match:
        _answer_callback(cq_id, "Матч не найден.", show_alert=True)
        return True

    if match.result_proposals.filter(status=Match.ProposalStatus.PENDING).exists():
        _answer_callback(
            cq_id,
            "По этому матчу уже отправлен результат и он ожидает подтверждения.",
            show_alert=True,
        )
        return True

    participants = get_match_participants(match)
    if player not in participants:
        _answer_callback(cq_id, "Вы не участвуете в этом матче.", show_alert=True)
        return True

    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        _answer_callback(cq_id, "Матч уже завершён.", show_alert=True)
        return True

    # Если выбран обычный матч - просим ввести счёт
    if result_type == "normal":
        cache.set(CACHE_KEY_RESULT_ENTRY % chat_id, match_pk, CACHE_RESULT_ENTRY_TIMEOUT)
        side_text = "первую ({}).".format(match.get_player1_display()) if _proposer_is_side1(match, player) else "вторую ({}).".format(match.get_player2_display())
        bot.send_message(
            chat_id,
            f"📝 <b>Внести результат</b>\n\n"
            f"Матч: {match.tournament.name}, {match.round_name or '—'}\n"
            f"{match.get_player1_display()} — {match.get_player2_display()}\n\n"
            f"Вы играете за {side_text}\n\n"
            f"<b>Формат счёта:</b> в каждом сете сначала геймы <b>вашей</b> команды, затем геймы соперника.\n"
            f"Пример: <code>6:4 6:3</code> — вы выиграли оба сета. <code>3:6 4:6</code> — вы проиграли оба (вы 3 и 4, соперник 6 и 6).\n\n"
            f"Введите счёт через пробел по сетам, например: <code>6:4 6:3</code> или <code>6:4 3:6 10:7</code> (тайбрейк).\n\n"
            f"Отправьте счёт в чат (или /cancel чтобы отменить).",
        )
        _answer_callback(cq_id, "Введите счёт в следующем сообщении")
        return True

    # Если выбрана тех. победа или тех. поражение - создаём proposal сразу
    if result_type in ("walkover_win", "walkover_loss"):
        result_choice = Match.ResultChoice.WALKOVER_WIN if result_type == "walkover_win" else Match.ResultChoice.WALKOVER_LOSS
        proposal = MatchResultProposal.objects.create(
            match=match,
            proposer=player,
            result=result_choice,
            player1_set1=None,
            player2_set1=None,
            player1_set2=None,
            player2_set2=None,
            player1_set3=None,
            player2_set3=None,
        )
        for opp_user in get_match_opponent_users(match, player):
            Notification.objects.create(
                user=opp_user,
                message=f"{player} предложил результат матча в турнире {match.tournament.name}. У вас 3 часа на подтверждение.",
                url=reverse("my_matches"),
            )
        try:
            tg_notify.notify_result_proposal(proposal)
        except Exception:
            pass
        result_text = "техническую победу" if result_type == "walkover_win" else "техническое поражение"
        bot.send_message(
            chat_id,
            f"✅ Результат отправлен на подтверждение сопернику.\n\n"
            f"Вы указали: <b>{result_text}</b>\n"
            f"Ожидайте подтверждения в боте.",
        )
        _answer_callback(cq_id, "Результат отправлен")
        return True

    _answer_callback(cq_id, "Неизвестный тип результата.", show_alert=True)
    return True


def _handle_menu_callback(callback_query: dict, base_url: str = "") -> bool:
    """
    Обработка кнопок меню: menu_my_matches, menu_my_profile, menu_my_subscription, menu_go_to_site.
    Отправляет контент прямо в чат бота (матчи, профиль, подписка, ссылка на сайт).
    """
    callback_data = (callback_query.get("callback_data") or "").strip()
    if callback_data not in ("menu_my_matches", "menu_my_profile", "menu_my_subscription", "menu_go_to_site"):
        return False

    cq_id = callback_query.get("id")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return False

    try:
        link = _get_link_by_chat_id(chat_id)
        if not link:
            _answer_callback(cq_id, "Сначала подключите бота с сайта (профиль → Telegram).", show_alert=True)
            return True

        user = link.user
        if callback_data == "menu_go_to_site":
            _handle_menu_callback_action(chat_id, cq_id, callback_data, user, None, base_url=base_url)
        else:
            player = getattr(user, "player", None)
            if not player:
                try:
                    player = Player.objects.create(user=user)
                except Exception:
                    _answer_callback(cq_id, "Ошибка профиля игрока.", show_alert=True)
                    return True
            _handle_menu_callback_action(chat_id, cq_id, callback_data, user, player, base_url=base_url)
    except Exception as e:
        logger.exception("_handle_menu_callback failed: %s", e)
        _answer_callback(cq_id, "Ошибка. Попробуйте ещё раз.", show_alert=True)
    return True


def _handle_menu_callback_action(
    chat_id,
    cq_id: str | None,
    callback_data: str,
    user,
    player,
    base_url: str = "",
) -> None:
    """Отправка контента по выбранному пункту меню. cq_id=None при нажатии реплай-кнопки."""
    def _answer(caption: str) -> None:
        if cq_id:
            _answer_callback(cq_id, caption)

    if callback_data == "menu_my_matches":
        scheduled = (
            Match.objects.filter(
                Q(player1=player) | Q(player2=player)
                | Q(team1__player1=player) | Q(team1__player2=player)
                | Q(team2__player1=player) | Q(team2__player2=player),
                status=Match.MatchStatus.SCHEDULED,
            )
            # Если по матчу уже отправлен результат и он ждёт подтверждения — не показываем
            # его в списке «куда можно внести результат».
            .exclude(result_proposals__status=Match.ProposalStatus.PENDING)
            .distinct()
            .select_related("tournament", "player1", "player2", "team1", "team2")
            .order_by("deadline", "scheduled_datetime")[:20]
        )
        scheduled_list = list(scheduled)

        lines = [
            "🎾 <b>Мои матчи</b>",
            "<i>Только предстоящие. По сыгранным — смотрите на сайте.</i>",
            "",
        ]
        if not scheduled_list:
            lines.append("Нет предстоящих матчей.")
        else:
            for i, m in enumerate(scheduled_list, 1):
                deadline_str = m.deadline.strftime("%d.%m.%Y %H:%M") if m.deadline else "—"
                round_name = m.round_name or "—"
                p1 = m.get_player1_display()
                p2 = m.get_player2_display()
                lines.append("─────────────────")
                lines.append(f"<b>{i}. {m.tournament.name}</b> · {round_name}")
                lines.append(f"   {p1}\n   vs\n   {p2}")
                lines.append(f"   📅 Дедлайн: {deadline_str}")
                lines.append("")
            lines.append("─────────────────")
            lines.append("")
            lines.append("<b>Внести результат</b> — выберите матч кнопкой ниже:")

        text = "\n".join(lines)

        reply_markup = None
        if scheduled_list:
            keyboard = []
            for i, m in enumerate(scheduled_list[:10], 1):
                label = f"{m.tournament.name}, {m.round_name or 'раунд'}"
                btn_text = f"📝 Матч {i}: {label}"
                if len(btn_text) > 64:
                    btn_text = (f"📝 Матч {i}: {label}"[:61]).rstrip() + "…"
                keyboard.append([{"text": btn_text, "callback_data": f"result_enter_{m.pk}"}])
            reply_markup = {"inline_keyboard": keyboard}

        ok = bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer("Список матчей" if ok else "Сообщение не отправлено")
        if not ok:
            logger.warning("menu_my_matches: send_message failed for chat_id=%s", chat_id)

    elif callback_data == "menu_my_profile":
        text = (
            f"👤 <b>Мой профиль</b>\n\n"
            f"<b>{player}</b>\n"
            f"Уровень: {player.get_skill_level_display()}\n"
            f"Город: {player.city or '—'}\n"
            f"Очков: {player.total_points}\n"
            f"Матчей: {player.matches_played}\n"
            f"Побед: {player.win_rate}%"
        )
        ok = bot.send_to_user(chat_id, text)
        _answer("Профиль" if ok else "Ошибка отправки")
        if not ok:
            logger.warning("menu_my_profile: send_message failed for chat_id=%s", chat_id)

    elif callback_data == "menu_my_subscription":
        try:
            sub = getattr(user, "subscription", None)
        except Exception:
            sub = None
        if not sub:
            text = "📋 <b>Моя подписка</b>\n\nНет активной подписки.\nОформить на сайте в разделе «Тарифы»."
        else:
            status = "Активна" if sub.is_valid() else "Истекла"
            end_str = sub.end_date.strftime("%d.%m.%Y") if sub.end_date else "—"
            slots = sub.get_remaining_slots() if hasattr(sub, "get_remaining_slots") else "—"
            tier_name = getattr(sub.tier, "get_name_display", lambda: str(sub.tier))()
            text = (
                f"📋 <b>Моя подписка</b>\n\n"
                f"Тариф: {tier_name}\n"
                f"Статус: {status}\n"
                f"До: {end_str}\n"
                f"Регистраций в месяц: {slots}"
            )
        ok = bot.send_to_user(chat_id, text)
        _answer("Подписка" if ok else "Ошибка отправки")
        if not ok:
            logger.warning("menu_my_subscription: send_message failed for chat_id=%s", chat_id)

    elif callback_data == "menu_go_to_site" and base_url:
        site_url = base_url.rstrip("/")
        bot.send_to_user(
            chat_id,
            f"🌐 <b>Перейти на сайт</b>\n\n<a href=\"{site_url}\">{site_url}</a>",
            reply_markup=_reply_menu_keyboard(),
        )
        _answer("Ссылка отправлена")


@csrf_exempt
@require_http_methods(["POST"])
def user_bot_webhook(request):
    """
    Webhook пользовательского бота (TELEGRAM_USER_BOT_TOKEN).
    - /start с токеном: привязка chat_id к пользователю (UserTelegramLink).
    - /start без токена: меню или «подключите с сайта».
    - Callback от кнопок меню: контент в боте (матчи, профиль, подписка).
    """
    if not _webhook_secret_ok(request):
        return JsonResponse({"ok": False}, status=403)

    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": True})

    base_url = _get_site_base_url()

    # Callback от inline-кнопок: подтверждение/отклонение результата или просто снять «часики»
    callback_query = data.get("callback_query") or {}
    if callback_query:
        # Telegram API присылает данные кнопки в поле "data", а не "callback_data"
        if "data" in callback_query:
            callback_query.setdefault("callback_data", callback_query["data"])
        callback_data = (callback_query.get("callback_data") or "")[:50]
        logger.info("user_bot callback_query: chat_id=%s data=%s", callback_query.get("message", {}).get("chat", {}).get("id"), callback_data)
        handled = _handle_proposal_callback(callback_query, base_url)
        if not handled:
            handled = _handle_extension_request_callback(callback_query, base_url)
        if not handled:
            handled = _handle_result_type_callback(callback_query)
        if not handled:
            handled = _handle_result_enter_callback(callback_query)
        if not handled:
            handled = _handle_menu_callback(callback_query, base_url)
        cq_id = callback_query.get("id")
        if cq_id and not handled:
            token = bot._get_bot_token()
            if token:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                        json={"callback_query_id": str(cq_id)},
                        timeout=5,
                    )
                except Exception:
                    pass
        return JsonResponse({"ok": True})

    message = data.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()

    if not chat_id:
        return JsonResponse({"ok": True})

    # Только личный чат
    if message.get("chat", {}).get("type") != "private":
        return JsonResponse({"ok": True})

    # /start
    if text.startswith("/start"):
        token = ""
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            token = (parts[1] or "").strip()

        if token:
            link = UserTelegramLink.objects.filter(binding_token=token).first()
            if link:
                link.user_bot_chat_id = chat_id
                link.binding_token = ""
                link.token_created_at = None
                link.save(update_fields=["user_bot_chat_id", "binding_token", "token_created_at"])
                welcome = (
                    "✅ <b>Бот подключён</b>\n\n"
                    "Теперь вы будете получать уведомления о регистрациях на турниры, "
                    "о матчах и дедлайнах. Выберите действие кнопками ниже:"
                )
                bot.send_message(chat_id, welcome, reply_markup=_reply_menu_keyboard())
            else:
                bot.send_message(
                    chat_id,
                    "Токен не найден или устарел. Зайдите в личный кабинет на сайте и нажмите «Подключить Telegram-бот» заново.",
                )
        else:
            link = _get_link_by_chat_id(chat_id)
            if link:
                if not link.user_bot_chat_id:
                    link.user_bot_chat_id = chat_id
                    link.save(update_fields=["user_bot_chat_id"])
                bot.send_message(
                    chat_id,
                    "Снова привет! Выберите действие:",
                    reply_markup=_reply_menu_keyboard(),
                )
            else:
                bot.send_message(
                    chat_id,
                    "Чтобы получать уведомления о турнирах и матчах, подключите бота с сайта:\n"
                    "Профиль → блок «Telegram-бот» → «Подключить Telegram-бот».",
                )
        return JsonResponse({"ok": True})

    # Ввод результата матча (после нажатия «Внести результат»)
    cache_key = CACHE_KEY_RESULT_ENTRY % chat_id
    match_pk = cache.get(cache_key)
    if match_pk is not None:
        link = _get_link_by_chat_id(chat_id)
        if link:
            player = getattr(link.user, "player", None)
            if player:
                if text == "/cancel":
                    cache.delete(cache_key)
                    bot.send_message(chat_id, "Отменено.")
                    return JsonResponse({"ok": True})
                sets_list, err = _parse_score_input(text)
                if err:
                    bot.send_message(chat_id, f"❌ {err}")
                    return JsonResponse({"ok": True})
                match = (
                    Match.objects.filter(pk=match_pk)
                    .select_related("tournament", "player1", "player2", "team1", "team2")
                    .first()
                )
                if not match or match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                    cache.delete(cache_key)
                    bot.send_message(chat_id, "Матч не найден или уже завершён.")
                    return JsonResponse({"ok": True})
                participants = get_match_participants(match)
                if player not in participants:
                    cache.delete(cache_key)
                    bot.send_message(chat_id, "Вы не участвуете в этом матче.")
                    return JsonResponse({"ok": True})
                if match.result_proposals.filter(status=Match.ProposalStatus.PENDING).exists():
                    cache.delete(cache_key)
                    bot.send_message(
                        chat_id,
                        "⏳ По этому матчу уже отправлен результат и он ожидает подтверждения соперником. "
                        "Если соперник отклонит результат и не отправит свой — вы сможете отправить результат снова.",
                    )
                    return JsonResponse({"ok": True})
                is_p1 = _proposer_is_side1(match, player)
                p1_s1 = p1_s2 = p1_s3 = p2_s1 = p2_s2 = p2_s3 = None
                for i, (a, b) in enumerate(sets_list):
                    if i == 0:
                        p1_s1, p2_s1 = (a, b) if is_p1 else (b, a)
                    elif i == 1:
                        p1_s2, p2_s2 = (a, b) if is_p1 else (b, a)
                    else:
                        p1_s3, p2_s3 = (a, b) if is_p1 else (b, a)
                if is_p1:
                    sets_won_p1 = sum(1 for (a, b) in sets_list if a > b)
                else:
                    sets_won_p1 = sum(1 for (a, b) in sets_list if b > a)
                sets_won_p2 = len(sets_list) - sets_won_p1
                result = Match.ResultChoice.WIN if (is_p1 and sets_won_p1 > sets_won_p2) or (not is_p1 and sets_won_p2 > sets_won_p1) else Match.ResultChoice.LOSS
                proposal = MatchResultProposal.objects.create(
                    match=match,
                    proposer=player,
                    result=result,
                    player1_set1=p1_s1,
                    player2_set1=p2_s1,
                    player1_set2=p1_s2,
                    player2_set2=p2_s2,
                    player1_set3=p1_s3,
                    player2_set3=p2_s3,
                )
                for opp_user in get_match_opponent_users(match, player):
                    Notification.objects.create(
                        user=opp_user,
                        message=f"{player} предложил результат матча в турнире {match.tournament.name}. У вас 3 часа на подтверждение.",
                        url=reverse("my_matches"),
                    )
                try:
                    tg_notify.notify_result_proposal(proposal)
                except Exception:
                    pass
                cache.delete(cache_key)
                bot.send_message(chat_id, "✅ Результат отправлен на подтверждение сопернику. Ожидайте подтверждения в боте.")
                return JsonResponse({"ok": True})
        cache.delete(cache_key)

    # Нажатие реплай-кнопок меню
    if text in REPLY_MENU_BUTTONS:
        if text == REPLY_BTN_GO_TO_SITE:
            site_url = base_url.rstrip("/")
            bot.send_message(
                chat_id,
                f"🌐 <b>Перейти на сайт</b>\n\n<a href=\"{site_url}\">{site_url}</a>",
                reply_markup=_reply_menu_keyboard(),
            )
            return JsonResponse({"ok": True})
        else:
            link = _get_link_by_chat_id(chat_id)
            if not link:
                bot.send_message(chat_id, "Сначала подключите бота с сайта (профиль → Telegram).")
            else:
                user = link.user
                player = getattr(user, "player", None)
                if not player:
                    try:
                        player = Player.objects.create(user=user)
                    except Exception:
                        bot.send_message(chat_id, "Ошибка профиля игрока.")
                        return JsonResponse({"ok": True})
                reply_to_callback = {
                    REPLY_BTN_MY_PROFILE: "menu_my_profile",
                    REPLY_BTN_MY_MATCHES: "menu_my_matches",
                    REPLY_BTN_MY_SUBSCRIPTIONS: "menu_my_subscription",
                }[text]
                _handle_menu_callback_action(
                    chat_id, None, reply_to_callback, user, player, base_url=base_url
                )
        return JsonResponse({"ok": True})

    # Любое другое сообщение — показываем меню
    link = _get_link_by_chat_id(chat_id)
    if link:
        bot.send_message(
            chat_id,
            "Выберите действие:",
            reply_markup=_reply_menu_keyboard(),
        )
    else:
        bot.send_message(
            chat_id,
            "Подключите бота с сайта (профиль → Telegram), чтобы пользоваться меню.",
        )

    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["GET"])
def connect_redirect(request):
    """
    Редирект на t.me/BotUsername?start=TOKEN для привязки Telegram.
    Генерирует токен, сохраняет в UserTelegramLink, перенаправляет пользователя в бота.
    """
    if not bot.is_configured():
        messages.error(request, "Telegram-бот временно недоступен. Проверьте TELEGRAM_USER_BOT_TOKEN в .env и перезапустите сервер.")
        try:
            return redirect("profile", pk=request.user.player.pk)
        except Exception:
            return redirect("profile_edit")

    link, _ = UserTelegramLink.objects.get_or_create(
        user=request.user,
        defaults={"telegram_chat_id": None, "user_bot_chat_id": None},
    )
    token = link.get_or_create_binding_token()
    username = bot.get_bot_username()
    if not username:
        messages.error(request, "Не удалось получить ссылку на бота.")
        try:
            return redirect("profile", pk=request.user.player.pk)
        except Exception:
            return redirect("profile_edit")
    url = f"https://t.me/{username}?start={token}"
    return redirect(url)
