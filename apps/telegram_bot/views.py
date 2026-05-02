"""
Webhook пользовательского Telegram-бота и редирект привязки с сайта.
"""

import json
import logging
import secrets
from typing import Any, cast

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.core import telegram_notify as admin_notify
from apps.core.models import TelegramTransferConsentLog, UserTelegramLink
from apps.subscriptions.models import SubscriptionTier
from apps.tournaments.models import DeadlineExtensionRequest, Match, MatchResultProposal
from apps.tournaments.proposal_service import apply_proposal
from apps.tournaments.utils import get_match_opponent_users, get_match_participants
from apps.users.models import Notification, Player

from . import notifications as tg_notify
from . import services as bot
from .telegram_http import is_telegram_api_enabled, telegram_requests_proxies


def _get_site_base_url() -> str:
    """Получить базовый URL сайта для ссылок."""
    from django.conf import settings

    base = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", None) or ""
    if base:
        return base.rstrip("/") + "/"
    return "http://localhost:8000/" if settings.DEBUG else "https://tennisfan.ru/"


logger = logging.getLogger(__name__)

CACHE_KEY_RESULT_ENTRY = "tg_result_entry:%s"
CACHE_RESULT_ENTRY_TIMEOUT = 300  # 5 min
TELEGRAM_TRANSFER_CONSENT_VERSION = "v1-2026-02-10"


def _get_redirect_url_after_bot_settings_change(request) -> str:
    next_url = str(request.POST.get("next", "")).strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return cast(str, reverse("profile", kwargs={"pk": request.user.player.pk}))


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
    for part in parts:
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
        return bool(
            match.team1 and player in (match.team1.player1, match.team1.player2)
        )
    return bool(match.player1_id == player.pk)


def _webhook_secret_ok(request) -> bool:
    """Проверка секрета webhook (X-Telegram-Bot-Api-Secret-Token)."""
    secret = getattr(settings, "TELEGRAM_USER_BOT_WEBHOOK_SECRET", None) or ""
    if not secret:
        return True
    return bool(request.headers.get("X-Telegram-Bot-Api-Secret-Token") == secret)


def _get_client_ip(request) -> str | None:
    """Определить IP клиента с учётом reverse proxy."""
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded:
        # Берём первый IP в цепочке X-Forwarded-For
        return forwarded.split(",")[0].strip()
    real_ip = (request.META.get("HTTP_X_REAL_IP") or "").strip()
    if real_ip:
        return real_ip
    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    return remote_addr or None


# Тексты кнопок реплай-меню (должны совпадать при обработке сообщения)
REPLY_BTN_MY_PROFILE = "👤 Мой профиль"
REPLY_BTN_MY_MATCHES = "🎾 Мои матчи"
REPLY_BTN_MY_SUBSCRIPTIONS = "📋 Мои подписки"
REPLY_BTN_PRIVATE_CHAT = "💬 Чат игроков"
REPLY_BTN_GO_TO_SITE = "🌐 Перейти на сайт"
REPLY_BTN_SPARRING = "🎾 Спарринг"
REPLY_BTN_SPARRING_MY_REQUESTS = "📝 Мои заявки"
REPLY_BTN_SPARRING_MY_RESPONSES = "🙋 Мои отклики"
REPLY_BTN_SPARRING_RESPONSES_TO_ME = "📬 Отклики на мои заявки"
REPLY_BTN_BACK_TO_MENU = "В меню"

REPLY_MENU_BUTTONS = (
    REPLY_BTN_MY_PROFILE,
    REPLY_BTN_MY_MATCHES,
    REPLY_BTN_MY_SUBSCRIPTIONS,
    REPLY_BTN_PRIVATE_CHAT,
    REPLY_BTN_GO_TO_SITE,
    REPLY_BTN_SPARRING,
)
REPLY_SPARRING_BUTTONS = (
    REPLY_BTN_SPARRING_MY_REQUESTS,
    REPLY_BTN_SPARRING_MY_RESPONSES,
    REPLY_BTN_SPARRING_RESPONSES_TO_ME,
    REPLY_BTN_BACK_TO_MENU,
)


def _reply_menu_keyboard():
    """Реплай-клавиатура: профиль, матчи, подписка, чат; в последней строке Спарринг и На сайт."""
    return {
        "keyboard": [
            [{"text": REPLY_BTN_MY_PROFILE}, {"text": REPLY_BTN_MY_MATCHES}],
            [{"text": REPLY_BTN_MY_SUBSCRIPTIONS}, {"text": REPLY_BTN_PRIVATE_CHAT}],
            [{"text": REPLY_BTN_SPARRING}, {"text": REPLY_BTN_GO_TO_SITE}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _reply_sparring_keyboard():
    """Реплай-клавиатура подменю спарринга."""
    return {
        "keyboard": [
            [{"text": REPLY_BTN_SPARRING_MY_REQUESTS}],
            [{"text": REPLY_BTN_SPARRING_MY_RESPONSES}],
            [{"text": REPLY_BTN_SPARRING_RESPONSES_TO_ME}],
            [{"text": REPLY_BTN_BACK_TO_MENU}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _main_menu_keyboard(site_base_url: str):
    """Inline-клавиатура (дублирует меню для старых клиентов)."""
    return {
        "inline_keyboard": [
            [
                {"text": "🎾 Мои матчи", "callback_data": "menu_my_matches"},
                {"text": "👤 Мой профиль", "callback_data": "menu_my_profile"},
            ],
            [
                {"text": "📋 Моя подписка", "callback_data": "menu_my_subscription"},
                {"text": "💬 Чат игроков", "callback_data": "menu_private_chat"},
            ],
            [
                {"text": "🌐 На сайт", "url": site_base_url.rstrip("/")},
            ],
        ]
    }


def _get_link_by_chat_id(chat_id: int | None) -> UserTelegramLink | None:
    """Найти привязку по chat_id (бот поддержки или пользовательский бот)."""
    if chat_id is None:
        return None
    link = UserTelegramLink.objects.filter(
        Q(telegram_chat_id=chat_id) | Q(user_bot_chat_id=chat_id)
    ).first()
    return cast("UserTelegramLink | None", link)


def _answer_callback(
    callback_query_id: str | int | None,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    """Ответить на callback_query в Telegram (убрать «часики», опционально показать текст)."""
    if not is_telegram_api_enabled():
        return
    token = bot._get_bot_token()
    if not token or callback_query_id is None:
        return
    payload: dict[str, Any] = {"callback_query_id": str(callback_query_id)}
    if text:
        payload["text"] = text[:200]
    if show_alert:
        payload["show_alert"] = True
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json=payload,
            timeout=5,
            proxies=telegram_requests_proxies(),
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning("answerCallbackQuery failed: %s", e)


def _edit_message_remove_reply_markup(chat_id: int, message_id: int) -> None:
    """Убрать inline-кнопки у сообщения (после подтверждения/отклонения)."""
    if not is_telegram_api_enabled():
        return
    token = bot._get_bot_token()
    if not token:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/editMessageReplyMarkup",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": []},
            },
            timeout=5,
            proxies=telegram_requests_proxies(),
        )
        r.raise_for_status()
    except Exception as e:
        logger.debug("editMessageReplyMarkup failed: %s", e)


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

    if not callback_data.startswith(
        "proposal_confirm_"
    ) and not callback_data.startswith("proposal_reject_"):
        return False

    prefix = (
        "proposal_confirm_"
        if callback_data.startswith("proposal_confirm_")
        else "proposal_reject_"
    )
    try:
        pk = int(callback_data[len(prefix) :])
    except (ValueError, TypeError):
        _answer_callback(cq_id, "Неверные данные.", show_alert=True)
        return True

    proposal = (
        MatchResultProposal.objects.select_related(
            "match__tournament",
            "match__player1",
            "match__player2",
            "match__team1__player1",
            "match__team1__player2",
            "match__team2__player1",
            "match__team2__player2",
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
        _answer_callback(
            cq_id, "Подключите бота с сайта (профиль → Telegram).", show_alert=True
        )
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
        _answer_callback(
            cq_id, "Вы не можете подтверждать свой запрос.", show_alert=True
        )
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
        # 1. Применяем результат матча
        apply_ok = False
        try:
            apply_proposal(proposal)
            apply_ok = True
        except Exception as e:
            logger.exception("apply_proposal in webhook: %s", e)

        # 2. Уведомления в ЛК и Telegram (с рейтингом и силой) обоим участникам создаются внутри apply_proposal

        # 3. Убираем кнопки из исходного сообщения
        try:
            if message_id:
                _edit_message_remove_reply_markup(chat_id, message_id)
        except Exception as e:
            logger.exception("edit_message_remove_reply_markup failed: %s", e)

        # 4. Callback-ответ (всплывающий тост)
        if apply_ok:
            _answer_callback(cq_id, "✅ Результат подтверждён.")
        else:
            _answer_callback(
                cq_id,
                "⚠️ Результат обработан с ошибкой. Проверьте матч.",
                show_alert=True,
            )
    else:
        # 1. Уведомляем инициатора (PROPOSER) в Telegram
        try:
            tg_notify.notify_proposal_rejected(proposal)
        except Exception as e:
            logger.exception("notify_proposal_rejected failed: %s", e)

        # 2. Создаём уведомление на сайте для инициатора
        try:
            Notification.objects.create(
                user=proposal.proposer.user,
                message=f"{player} отклонил результат матча. Введите свой результат.",
                url=reverse("my_matches"),
            )
        except Exception as e:
            logger.exception("create rejection notification failed: %s", e)

        # 3. Удаляем proposal
        try:
            proposal.delete()
        except Exception as e:
            logger.exception("proposal.delete failed: %s", e)

        # 4. Убираем кнопки из исходного сообщения
        try:
            if message_id:
                _edit_message_remove_reply_markup(chat_id, message_id)
        except Exception as e:
            logger.exception("edit_message_remove_reply_markup failed: %s", e)

        # 5. Отправляем сообщение-отклонение нажавшему (OPPONENT)
        try:
            match = proposal.match
            bot.send_message(
                chat_id,
                "❌ <b>Результат отклонён.</b>\n\n"
                f"Турнир: {match.tournament.name}\n"
                f"Матч: {match.get_player1_display()} vs {match.get_player2_display()}\n\n"
                f"Вы отклонили предложенный счёт. {proposal.proposer} уведомлён(а).",
            )
        except Exception as e:
            logger.exception("send reject message to opponent failed: %s", e)

        # 6. Callback-ответ (всплывающий тост)
        _answer_callback(cq_id, "❌ Результат отклонён.")

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
        match_pk = int(callback_data[len("extension_request_") :])
    except (ValueError, TypeError):
        _answer_callback(cq_id, "Неверные данные.", show_alert=True)
        return True

    match = (
        Match.objects.select_related(
            "tournament", "player1", "player2", "team1", "team2"
        )
        .filter(
            pk=match_pk,
            status=Match.MatchStatus.SCHEDULED,
            deadline__isnull=False,
        )
        .first()
    )
    if not match:
        _answer_callback(
            cq_id, "Матч не найден или дедлайн уже прошёл.", show_alert=True
        )
        return True

    if not chat_id:
        _answer_callback(cq_id, "Ошибка чата.", show_alert=True)
        return True

    link = _get_link_by_chat_id(chat_id)
    if not link:
        _answer_callback(
            cq_id, "Подключите бота с сайта (профиль → Telegram).", show_alert=True
        )
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

    DeadlineExtensionRequest.objects.create(
        match=match,
        requested_by=player,
        status=DeadlineExtensionRequest.Status.PENDING,
    )
    admin_list_url = (
        base_url.rstrip("/") + "/admin/tournaments/deadlineextensionrequest/"
    )
    deadline_str = match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else "—"
    text_for_admin = (
        f"🔄 <b>Запрос на продление дедлайна</b>\n\n"
        f"Игрок: {player}\n"
        f"Матч: {match} ({match.tournament.name})\n"
        f"Текущий дедлайн: {deadline_str}\n\n"
        f'<a href="{admin_list_url}">Список запросов в админке</a>'
    )
    try:
        admin_notify.send_admin_message(text_for_admin)
    except Exception as e:
        logger.warning("Notify admin about extension request: %s", e)

    _answer_callback(
        cq_id, "Запрос отправлен. Администратор рассмотрит его в ближайшее время."
    )
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
        match_pk = int(callback_data[len("result_enter_") :])
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

    side_text = (
        f"первую ({match.get_player1_display()})."
        if _proposer_is_side1(match, player)
        else f"вторую ({match.get_player2_display()})."
    )

    # Показываем кнопки выбора типа результата
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "📝 Обычный матч (ввести счёт)",
                    "callback_data": f"result_type_{match_pk}_normal",
                }
            ],
            [
                {
                    "text": "✅ Тех. победа (соперник не вышел)",
                    "callback_data": f"result_type_{match_pk}_walkover_win",
                },
                {
                    "text": "❌ Тех. поражение (мы не вышли)",
                    "callback_data": f"result_type_{match_pk}_walkover_loss",
                },
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
        result_type = "_".join(
            parts[3:]
        )  # Может быть "normal", "walkover_win", "walkover_loss"
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
        cache.set(
            CACHE_KEY_RESULT_ENTRY % chat_id, match_pk, CACHE_RESULT_ENTRY_TIMEOUT
        )
        side_text = (
            f"первую ({match.get_player1_display()})."
            if _proposer_is_side1(match, player)
            else f"вторую ({match.get_player2_display()})."
        )
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
        result_choice = (
            Match.ResultChoice.WALKOVER_WIN
            if result_type == "walkover_win"
            else Match.ResultChoice.WALKOVER_LOSS
        )
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
        tour_name = match.tournament.name if match.tournament else "спарринг"
        for opp_user in get_match_opponent_users(match, player):
            Notification.objects.create(
                user=opp_user,
                message=f"{player} предложил результат матча в турнире {tour_name}. У вас 3 часа на подтверждение.",
                url=reverse("my_matches"),
            )
        try:
            tg_notify.notify_result_proposal(proposal)
        except Exception as e:
            logger.exception("notify_result_proposal failed: %s", e)
        result_text = (
            "техническую победу"
            if result_type == "walkover_win"
            else "техническое поражение"
        )
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


def _format_sparring_player_card(player: Player) -> str:
    """Форматирует карточку игрока для бота (моноширинный блок)."""
    from apps.users.models import SkillLevel

    name = str(player)
    age = player.age or "—"
    ntrp = float(player.ntrp_level) if player.ntrp_level else "—"
    fan_rating = int(player.total_points) if player.total_points else "—"
    played = player.matches_played or 0
    won = player.matches_won or 0
    skill = (
        dict(SkillLevel.choices).get(player.skill_level, player.skill_level)
        if player.skill_level
        else "—"
    )
    lines = [
        f"Имя: {name}",
        f"Возраст: {age}",
        f"Сила: {ntrp}",
        f"Уровень: {skill}",
        f"FAN: {fan_rating}",
        f"Игр: {played} (W:{won} / L:{played - won})",
    ]
    body = "\n".join(lines)
    return f"👤 <b>Профиль игрока</b>\n\n<pre>{body}</pre>"


def _handle_sparring_callback(callback_query: dict, base_url: str = "") -> bool:
    """
    Обработка callback для спаррингов:
    - sparring_my_requests, sparring_my_responses, sparring_responses_to_me
    - sparring_del_req_<id>, sparring_cancel_resp_<id>
    - sparring_req_<id>, sparring_cand_<id>, sparring_profile_<id>
    - contact_{response_id}, confirm_match_{response_id}
    - team_sparring_generate_{request_id}
    """
    from apps.sparring.doubles_services import (
        DoublesMatchKind,
        DoublesMatchRequestStatus,
        confirm_team_sparring_series,
    )
    from apps.sparring.models import (
        DoublesMatchRequest,
        SparringRequest,
        SparringResponse,
    )

    callback_data = (callback_query.get("callback_data") or "").strip()
    cq_id = callback_query.get("id")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    is_sparring = (
        callback_data.startswith("contact_")
        or callback_data.startswith("confirm_match_")
        or callback_data.startswith("sparring_invite_accept_")
        or callback_data.startswith("sparring_")
        or callback_data.startswith("team_sparring_generate_")
    )
    if not is_sparring:
        return False

    if not chat_id:
        _answer_callback(cq_id, "Ошибка чата.", show_alert=True)
        return True

    link = _get_link_by_chat_id(chat_id)
    if not link:
        _answer_callback(
            cq_id, "Подключите бота с сайта (профиль → Telegram).", show_alert=True
        )
        return True

    user = link.user
    player = getattr(user, "player", None)
    if not player:
        _answer_callback(cq_id, "Создайте профиль игрока на сайте.", show_alert=True)
        return True

    # ——— Подтверждение приглашения на спарринг (приглашённый) ———
    if callback_data.startswith("sparring_invite_accept_"):
        from apps.sparring.models import SparringInvitation
        from apps.sparring.services import accept_sparring_invitation
        from apps.sparring.utils import user_has_sparring_access
        from apps.telegram_bot.notifications import (
            notify_sparring_invitation_accepted_inviter,
        )

        try:
            inv_id = int(callback_data[len("sparring_invite_accept_") :])
        except (ValueError, TypeError):
            _answer_callback(cq_id, "Неверные данные.", show_alert=True)
            return True

        if not user_has_sparring_access(user):
            _answer_callback(
                cq_id,
                "Оформите подписку с доступом к спаррингам на сайте.",
                show_alert=True,
            )
            return True

        try:
            match = accept_sparring_invitation(inv_id, user.id)
        except ValueError as e:
            _answer_callback(cq_id, str(e), show_alert=True)
            return True

        inv = (
            SparringInvitation.objects.select_related("inviter__user", "invitee__user")
            .filter(pk=inv_id)
            .first()
        )
        if inv:
            try:
                notify_sparring_invitation_accepted_inviter(inv, match)
            except Exception as exc:
                logger.warning(
                    "notify_sparring_invitation_accepted_inviter failed: %s", exc
                )

        if message_id:
            _edit_message_remove_reply_markup(chat_id, message_id)

        _answer_callback(cq_id, "Матч создан! Проверьте «Мои матчи».")
        return True

    # ——— Командный спарринг: сформировать матчи ———
    if callback_data.startswith("team_sparring_generate_"):
        try:
            req_id = int(callback_data[len("team_sparring_generate_") :])
        except (ValueError, TypeError):
            _answer_callback(cq_id, "Неверные данные.", show_alert=True)
            return True

        req = (
            DoublesMatchRequest.objects.select_related("created_by")
            .prefetch_related("teams__members")
            .filter(pk=req_id)
            .first()
        )
        if not req:
            _answer_callback(cq_id, "Заявка не найдена.", show_alert=True)
            return True
        if req.created_by_id != player.id:
            _answer_callback(
                cq_id,
                "Только автор заявки может формировать матчи.",
                show_alert=True,
            )
            return True
        if req.kind != DoublesMatchKind.TEAM:
            _answer_callback(
                cq_id, "Эта заявка не является командным спаррингом.", show_alert=True
            )
            return True
        if req.status != DoublesMatchRequestStatus.READY:
            _answer_callback(
                cq_id,
                "Команды ещё не полные. Нужны 2 игрока в каждой команде.",
                show_alert=True,
            )
            return True

        try:
            matches = confirm_team_sparring_series(
                match_request_id=req_id, confirmed_by=player
            )
        except Exception as exc:
            logger.exception("team_sparring_generate failed: %s", exc)
            _answer_callback(
                cq_id,
                "Ошибка при создании матчей. Попробуйте позже.",
                show_alert=True,
            )
            return True

        total = len(matches)
        singles_count = max(0, total - 1)
        _answer_callback(cq_id, "Матчи созданы!")
        bot.send_to_user(
            chat_id,
            "✅ <b>Матчи командного спарринга созданы</b>\n\n"
            f"Создано одиночных матчей: <b>{singles_count}</b>\n"
            f"Создано парных матчей 2×2: <b>1</b>\n\n"
            "Проверьте раздел «Мои матчи» на сайте или в боте.",
        )
        return True

    # ——— A. Мои заявки ———
    if callback_data == "sparring_my_requests":
        requests_list = list(
            SparringRequest.objects.filter(
                player=player, status=SparringRequest.Status.ACTIVE
            ).order_by("-created_at")[:20]
        )
        if not requests_list:
            bot.send_to_user(chat_id, "📝 <b>Мои заявки</b>\n\nНет активных заявок.")
            _answer_callback(cq_id, "Мои заявки")
            return True
        lines = ["📝 <b>Мои заявки</b>", ""]
        keyboard = []
        for i, req in enumerate(requests_list, 1):
            short = (
                (req.description[:50] + "…")
                if len(req.description or "") > 50
                else (req.description or "—")
            )
            lines.append(f"<b>{i}.</b> {req.city} · {short}")
            lines.append("")
            keyboard.append(
                [
                    {
                        "text": f"🗑 Удалить заявку {i}",
                        "callback_data": f"sparring_del_req_{req.pk}",
                    }
                ]
            )
        text = "\n".join(lines)
        reply_markup = {"inline_keyboard": keyboard}
        bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer_callback(cq_id, "Мои заявки")
        return True

    # ——— Удалить заявку ———
    if callback_data.startswith("sparring_del_req_"):
        try:
            req_id = int(callback_data[len("sparring_del_req_") :])
        except (ValueError, TypeError):
            _answer_callback(cq_id, "Неверные данные.", show_alert=True)
            return True
        req = SparringRequest.objects.filter(pk=req_id, player=player).first()
        if not req:
            _answer_callback(cq_id, "Заявка не найдена.", show_alert=True)
            return True
        req.status = SparringRequest.Status.CLOSED
        req.save(update_fields=["status"])
        if message_id:
            bot.edit_message_text(
                chat_id,
                message_id,
                "📝 <b>Мои заявки</b>\n\n✅ Заявка удалена.",
                reply_markup={"inline_keyboard": []},
            )
        _answer_callback(cq_id, "Заявка удалена")
        return True

    # ——— B. Мои отклики ———
    if callback_data == "sparring_my_responses":
        responses_list = list(
            SparringResponse.objects.filter(respondent=player)
            .select_related("sparring_request__player")
            .order_by("-created_at")[:20]
        )
        if not responses_list:
            bot.send_to_user(
                chat_id, "🙋 <b>Мои отклики</b>\n\nВы ещё не откликались на заявки."
            )
            _answer_callback(cq_id, "Мои отклики")
            return True
        status_labels = {
            SparringResponse.ResponseStatus.PENDING: "На рассмотрении",
            SparringResponse.ResponseStatus.ACCEPTED: "Принят",
            SparringResponse.ResponseStatus.REJECTED: "Отклонен",
        }
        lines = ["🙋 <b>Мои отклики</b>", ""]
        keyboard = []
        for i, resp in enumerate(responses_list, 1):
            author = resp.sparring_request.player
            st = status_labels.get(resp.status, resp.status)
            lines.append(f"<b>{i}.</b> Заявка: {author} ({resp.sparring_request.city})")
            lines.append(f"   Статус: <b>{st}</b>")
            lines.append("")
            if resp.status == SparringResponse.ResponseStatus.PENDING:
                keyboard.append(
                    [
                        {
                            "text": f"❌ Отменить отклик {i}",
                            "callback_data": f"sparring_cancel_resp_{resp.pk}",
                        }
                    ]
                )
        text = "\n".join(lines)
        markup = {"inline_keyboard": keyboard} if keyboard else None
        bot.send_to_user(chat_id, text, reply_markup=markup)
        _answer_callback(cq_id, "Мои отклики")
        return True

    # ——— Отменить отклик ———
    if callback_data.startswith("sparring_cancel_resp_"):
        try:
            resp_id = int(callback_data[len("sparring_cancel_resp_") :])
        except (ValueError, TypeError):
            _answer_callback(cq_id, "Неверные данные.", show_alert=True)
            return True
        resp = SparringResponse.objects.filter(pk=resp_id, respondent=player).first()
        if not resp:
            _answer_callback(cq_id, "Отклик не найден.", show_alert=True)
            return True
        if resp.status != SparringResponse.ResponseStatus.PENDING:
            _answer_callback(cq_id, "Отклик уже обработан.", show_alert=True)
            return True
        resp.status = SparringResponse.ResponseStatus.REJECTED
        resp.save(update_fields=["status"])
        _answer_callback(cq_id, "Отклик отменён")
        bot.send_to_user(chat_id, "❌ Отклик отменён.")
        return True

    # ——— C. Отклики на мои заявки ———
    if callback_data == "sparring_responses_to_me":
        from django.db.models import Count

        my_requests_with_responses = list(
            SparringRequest.objects.filter(
                player=player,
                status=SparringRequest.Status.ACTIVE,
            )
            .annotate(resp_count=Count("responses"))
            .filter(resp_count__gt=0)
            .order_by("-created_at")[:20]
        )
        if not my_requests_with_responses:
            bot.send_to_user(
                chat_id, "📬 <b>Отклики на мои заявки</b>\n\nНет заявок с откликами."
            )
            _answer_callback(cq_id, "Отклики на мои заявки")
            return True
        lines = ["📬 <b>Отклики на мои заявки</b>", ""]
        keyboard = []
        for i, req in enumerate(my_requests_with_responses, 1):
            cnt = req.responses.count()
            lines.append(f"<b>{i}.</b> {req.city} — откликов: {cnt}")
            lines.append("")
            keyboard.append(
                [{"text": f"📋 Заявка {i}", "callback_data": f"sparring_req_{req.pk}"}]
            )
        text = "\n".join(lines)
        reply_markup = {"inline_keyboard": keyboard}
        bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer_callback(cq_id, "Отклики на мои заявки")
        return True

    # ——— Список кандидатов по заявке ———
    if callback_data.startswith("sparring_req_"):
        try:
            req_id = int(callback_data[len("sparring_req_") :])
        except (ValueError, TypeError):
            _answer_callback(cq_id, "Неверные данные.", show_alert=True)
            return True
        req = SparringRequest.objects.filter(pk=req_id, player=player).first()
        if not req:
            _answer_callback(cq_id, "Заявка не найдена.", show_alert=True)
            return True
        candidates = list(
            req.responses.select_related("respondent").order_by("-created_at")[:15]
        )
        lines = ["📬 <b>Кандидаты</b>", f"Заявка: {req.city}", ""]
        keyboard = []
        for i, resp in enumerate(candidates, 1):
            r = resp.respondent
            fan_rating = int(r.total_points) if r.total_points else "—"
            lines.append(f"<b>{i}.</b> {r} · FAN: {fan_rating}")
            keyboard.append(
                [{"text": f"👤 {i}. {r}", "callback_data": f"sparring_cand_{resp.pk}"}]
            )
        text = "\n".join(lines)
        reply_markup = {"inline_keyboard": keyboard}
        bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer_callback(cq_id, "Кандидаты")
        return True

    # ——— Карточка кандидата + действия ———
    if callback_data.startswith("sparring_cand_"):
        try:
            resp_id = int(callback_data[len("sparring_cand_") :])
        except (ValueError, TypeError):
            _answer_callback(cq_id, "Неверные данные.", show_alert=True)
            return True
        response = (
            SparringResponse.objects.select_related(
                "sparring_request__player", "respondent"
            )
            .filter(sparring_request__player=player, pk=resp_id)
            .first()
        )
        if not response:
            _answer_callback(cq_id, "Отклик не найден.", show_alert=True)
            return True
        respondent = response.respondent
        text = _format_sparring_player_card(respondent)
        keyboard = []
        keyboard.append(
            [
                {
                    "text": "👤 Профиль игрока",
                    "callback_data": f"sparring_profile_{response.pk}",
                }
            ]
        )
        keyboard.append(
            [
                {
                    "text": "✅ Подтвердить",
                    "callback_data": f"confirm_match_{response.pk}",
                }
            ]
        )
        keyboard.append(
            [{"text": "💬 Связаться", "callback_data": f"contact_{response.pk}"}]
        )
        reply_markup = {"inline_keyboard": keyboard}
        bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer_callback(cq_id, "Игрок")
        return True

    # ——— Профиль игрока (редактируем сообщение — карточка + кнопки действий) ———
    if callback_data.startswith("sparring_profile_"):
        try:
            resp_id = int(callback_data[len("sparring_profile_") :])
        except (ValueError, TypeError):
            _answer_callback(cq_id, "Неверные данные.", show_alert=True)
            return True
        response = (
            SparringResponse.objects.select_related("respondent")
            .filter(sparring_request__player=player, pk=resp_id)
            .first()
        )
        if not response:
            _answer_callback(cq_id, "Отклик не найден.", show_alert=True)
            return True
        respondent = response.respondent
        text = _format_sparring_player_card(respondent)
        keyboard = [
            [
                {
                    "text": "✅ Подтвердить",
                    "callback_data": f"confirm_match_{response.pk}",
                }
            ],
            [{"text": "💬 Связаться", "callback_data": f"contact_{response.pk}"}],
        ]
        reply_markup = {"inline_keyboard": keyboard}
        edited = message_id and bot.edit_message_text(
            chat_id, message_id, text, reply_markup=reply_markup
        )
        if not edited:
            bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer_callback(cq_id, "Профиль")
        return True

    # ——— contact_ и confirm_match_ (ниже — существующая логика) ———
    if not callback_data.startswith("contact_") and not callback_data.startswith(
        "confirm_match_"
    ):
        return False

    try:
        response_id = int(
            callback_data[len("contact_") :]
            if callback_data.startswith("contact_")
            else callback_data[len("confirm_match_") :]
        )
    except (ValueError, TypeError):
        _answer_callback(cq_id, "Неверные данные.", show_alert=True)
        return True

    try:
        response = SparringResponse.objects.select_related(
            "sparring_request__player__user",
            "respondent",
        ).get(pk=response_id)
    except SparringResponse.DoesNotExist:
        _answer_callback(cq_id, "Отклик не найден.", show_alert=True)
        return True

    if response.sparring_request.player.user_id != user.id:
        _answer_callback(cq_id, "Вы не являетесь автором этой заявки.", show_alert=True)
        return True

    # Обработка "Получить контакт" / "Связаться"
    if callback_data.startswith("contact_"):
        respondent = response.respondent
        contact_method = response.contact_method
        contact_info = ""

        if contact_method == "telegram" and respondent.telegram:
            uname = respondent.telegram.strip().lstrip("@")
            contact_info = f"Telegram: @{uname}\nСсылка: https://t.me/{uname}"
        elif contact_method == "whatsapp" and respondent.whatsapp:
            phone = "".join(c for c in respondent.whatsapp if c.isdigit())
            if phone.startswith("8") and len(phone) == 11:
                phone = "7" + phone[1:]
            elif phone.startswith("7") and len(phone) == 11:
                pass
            elif len(phone) == 10:
                phone = "7" + phone
            else:
                contact_info = f"WhatsApp: {respondent.whatsapp}"
            if phone:
                contact_info = (
                    f"WhatsApp: {respondent.whatsapp}\nСсылка: https://wa.me/{phone}"
                )
        elif contact_method == "max" and respondent.max_contact_display:
            contact_info = f"MAX: {respondent.max_contact_display}"
            if respondent.max_url:
                contact_info += f"\nСсылка: {respondent.max_url}"

        if contact_info:
            bot.send_to_user(
                chat_id, f"📱 <b>Контакт игрока {respondent}:</b>\n\n{contact_info}"
            )
            _answer_callback(cq_id, "Контакт отправлен.")
        else:
            _answer_callback(cq_id, "Контакт не указан.", show_alert=True)
        return True

    # Обработка "Подтвердить игру"
    if callback_data.startswith("confirm_match_"):
        # Проверяем, что отклик еще не обработан
        if response.status != SparringResponse.ResponseStatus.PENDING:
            _answer_callback(cq_id, "Этот отклик уже обработан.", show_alert=True)
            return True

        # Проверяем, что заявка еще активна
        if response.sparring_request.status != SparringRequest.Status.ACTIVE:
            _answer_callback(cq_id, "Заявка уже закрыта.", show_alert=True)
            return True

        # Создаем матч
        try:
            from apps.sparring.services import create_match_from_response

            match = create_match_from_response(response)

            # Обновляем статус отклика
            response.status = SparringResponse.ResponseStatus.ACCEPTED
            response.save(update_fields=["status", "updated_at"])

            # Закрываем заявку
            response.sparring_request.status = SparringRequest.Status.CLOSED
            response.sparring_request.save(update_fields=["status"])

            # Убираем кнопки из сообщения
            if message_id:
                _edit_message_remove_reply_markup(chat_id, message_id)

            # Отправляем подтверждение (без ссылки на сайт — всё в боте)
            deadline_str = (
                match.deadline.strftime("%d.%m.%Y")
                if match.deadline
                else "Не установлен"
            )
            bot.send_to_user(
                chat_id,
                f"✅ <b>Игра подтверждена!</b>\n\n"
                f"Матч создан: {match.get_player1_display()} vs {match.get_player2_display()}\n"
                f"Дедлайн: {deadline_str}",
            )

            # Уведомляем откликнувшегося игрока
            try:
                from django.urls import reverse

                from apps.telegram_bot.notifications import send_to_user_by_user

                send_to_user_by_user(
                    response.respondent.user,
                    f"✅ <b>Ваш отклик принят!</b>\n\n"
                    f"Автор заявки подтвердил игру.\n"
                    f"Матч: {match.get_player1_display()} vs {match.get_player2_display()}\n"
                    f"Дедлайн: {deadline_str}",
                )

                # Создаем уведомление в личном кабинете для откликнувшегося игрока
                try:
                    Notification.objects.create(
                        user=response.respondent.user,
                        message=f"Ваш отклик на заявку на спарринг принят! Матч создан. Дедлайн: {deadline_str}.",
                        url=reverse("match_detail", args=[match.pk]),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to create notification for sparring response acceptance (telegram): %s",
                        e,
                    )
            except Exception as e:
                logger.warning(
                    "Failed to notify respondent about match confirmation: %s", e
                )

            _answer_callback(cq_id, "Игра подтверждена, матч создан!")
        except Exception as e:
            logger.exception("Failed to create match from sparring response: %s", e)
            _answer_callback(
                cq_id, "Ошибка при создании матча. Попробуйте позже.", show_alert=True
            )

        return True

    return False


def _handle_menu_callback(callback_query: dict, base_url: str = "") -> bool:
    """
    Обработка кнопок меню:
    menu_my_matches, menu_my_profile, menu_my_subscription, menu_private_chat, menu_go_to_site.
    Отправляет контент прямо в чат бота (матчи, профиль, подписка, ссылка на сайт).
    """
    callback_data = (callback_query.get("callback_data") or "").strip()
    if not (
        callback_data
        in {
            "menu_my_matches",
            "menu_my_profile",
            "menu_my_subscription",
            "menu_private_chat",
            "menu_go_to_site",
            "menu_sparring",
        }
        or callback_data.startswith("my_matches_tour_")
        or callback_data == "my_matches_sparring"
        or callback_data.startswith("subscription_tier_")
    ):
        return False

    cq_id = callback_query.get("id")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return False

    try:
        link = _get_link_by_chat_id(chat_id)
        if not link:
            _answer_callback(
                cq_id,
                "Сначала подключите бота с сайта (профиль → Telegram).",
                show_alert=True,
            )
            return True

        user = link.user
        if callback_data == "menu_go_to_site":
            _handle_menu_callback_action(
                chat_id, cq_id, callback_data, user, None, base_url=base_url
            )
        else:
            player = getattr(user, "player", None)
            if not player:
                try:
                    player = Player.objects.create(user=user)
                except Exception:
                    _answer_callback(cq_id, "Ошибка профиля игрока.", show_alert=True)
                    return True
            _handle_menu_callback_action(
                chat_id, cq_id, callback_data, user, player, base_url=base_url
            )
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

    if callback_data == "menu_sparring":
        from apps.sparring.utils import user_has_sparring_access

        if not user_has_sparring_access(user):
            text = (
                "❌ <b>Спарринг</b>\n\n"
                "Оформите подписку для доступа к разделу спаррингов."
            )
            reply_markup = None
            if base_url:
                pricing_url = f"{base_url.rstrip('/')}/subscriptions/pricing/"
                reply_markup = {
                    "inline_keyboard": [[{"text": "💳 Тарифы", "url": pricing_url}]]
                }
            bot.send_to_user(chat_id, text, reply_markup=reply_markup)
            _answer("Нет доступа")
            return
        text = "🎾 <b>Спарринг</b>\n\nВыберите раздел:"
        reply_markup = {
            "inline_keyboard": [
                [{"text": "📝 Мои заявки", "callback_data": "sparring_my_requests"}],
                [
                    {
                        "text": "🙋\u200d♂️ Мои отклики",
                        "callback_data": "sparring_my_responses",
                    }
                ],
                [
                    {
                        "text": "📬 Отклики на мои заявки",
                        "callback_data": "sparring_responses_to_me",
                    }
                ],
            ]
        }
        ok = bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer("Спарринг" if ok else "Ошибка")
        return

    if callback_data == "menu_my_matches":
        scheduled = (
            Match.objects.filter(
                Q(player1=player)
                | Q(player2=player)
                | Q(team1__player1=player)
                | Q(team1__player2=player)
                | Q(team2__player1=player)
                | Q(team2__player2=player),
                status=Match.MatchStatus.SCHEDULED,
            )
            .exclude(result_proposals__status=Match.ProposalStatus.PENDING)
            .distinct()
            .select_related("tournament", "player1", "player2", "team1", "team2")
            .order_by("deadline", "scheduled_datetime")[:50]
        )
        scheduled_list = list(scheduled)

        tournaments: dict[int, dict] = {}
        sparring_matches: list[Match] = []
        for m in scheduled_list:
            if m.tournament_id:
                group = tournaments.setdefault(
                    m.tournament_id,
                    {
                        "tournament": m.tournament,
                        "matches": [],
                        "nearest_deadline": m.deadline,
                    },
                )
                group["matches"].append(m)
                if m.deadline and (
                    group["nearest_deadline"] is None
                    or m.deadline < group["nearest_deadline"]
                ):
                    group["nearest_deadline"] = m.deadline
            elif m.match_type == Match.MatchType.SPARRING:
                sparring_matches.append(m)

        lines = [
            "🎾 <b>Мои матчи</b>",
            "<i>Сначала выберите турнир или спарринг. По сыгранным матчам — смотрите раздел «Мои матчи» на сайте.</i>",
            "",
        ]
        keyboard: list[list[dict]] = []

        if not tournaments and not sparring_matches:
            lines.append("Нет предстоящих матчей.")
        else:
            idx = 1
            for t_id, info in tournaments.items():
                t = info["tournament"]
                match_count = len(info["matches"])
                deadline_str = (
                    info["nearest_deadline"].strftime("%d.%m.%Y %H:%M")
                    if info["nearest_deadline"]
                    else "—"
                )
                lines.append(
                    f"<b>{idx}. Турнир:</b> {t.name} · матчей: {match_count} · ближайший дедлайн: {deadline_str}"
                )
                keyboard.append(
                    [
                        {
                            "text": f"🏆 Турнир {idx}: {t.name[:40]}",
                            "callback_data": f"my_matches_tour_{t_id}",
                        }
                    ]
                )
                idx += 1
            if sparring_matches:
                lines.append("")
                lines.append(
                    f"<b>{idx}. Спарринги:</b> личные встречи, предстоящих матчей: {len(sparring_matches)}"
                )
                keyboard.append(
                    [
                        {
                            "text": f"🎾 Спарринги ({len(sparring_matches)})",
                            "callback_data": "my_matches_sparring",
                        }
                    ]
                )

        text = "\n".join(lines)
        reply_markup = {"inline_keyboard": keyboard} if keyboard else None

        ok = bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer("Список турниров" if ok else "Сообщение не отправлено")
        if not ok:
            logger.warning(
                "menu_my_matches: send_message failed for chat_id=%s", chat_id
            )

    elif (
        callback_data.startswith("my_matches_tour_")
        or callback_data == "my_matches_sparring"
    ):
        # Показать матчи выбранного турнира или все спарринги
        from apps.tournaments.models import (
            Tournament,
        )

        try:
            selected_tournament_id: int | None
            is_sparring = callback_data == "my_matches_sparring"
            if is_sparring:
                selected_tournament_id = None
            else:
                selected_tournament_id = int(callback_data[len("my_matches_tour_") :])
        except (ValueError, TypeError):
            _answer("Неверные данные")
            return

        matches_qs = Match.objects.filter(
            Q(player1=player)
            | Q(player2=player)
            | Q(team1__player1=player)
            | Q(team1__player2=player)
            | Q(team2__player1=player)
            | Q(team2__player2=player),
            status=Match.MatchStatus.SCHEDULED,
        ).exclude(result_proposals__status=Match.ProposalStatus.PENDING)

        if is_sparring:
            matches_qs = matches_qs.filter(match_type=Match.MatchType.SPARRING)
            header = "🎾 <b>Мои спарринги</b>"
        else:
            matches_qs = matches_qs.filter(
                match_type=Match.MatchType.TOURNAMENT,
                tournament_id=selected_tournament_id,
            )
            t_obj = Tournament.objects.filter(pk=selected_tournament_id).first()
            t_name = t_obj.name if t_obj else "Турнир"
            header = f"🏆 <b>{t_name}</b>"

        matches = list(
            matches_qs.select_related(
                "tournament", "player1", "player2", "team1", "team2"
            ).order_by("deadline", "scheduled_datetime")[:20]
        )

        lines = [header, "", "<i>Выберите матч, чтобы внести результат.</i>", ""]
        sub_keyboard: list[list[dict]] = []

        if not matches:
            lines.append("Нет предстоящих матчей для этого выбора.")
        else:
            for i, m in enumerate(matches, 1):
                deadline_str = (
                    m.deadline.strftime("%d.%m.%Y %H:%M") if m.deadline else "—"
                )
                round_name = m.round_name or "—"
                p1 = m.get_player1_display()
                p2 = m.get_player2_display()
                lines.append("─────────────────")
                lines.append(f"<b>{i}.</b> {round_name}")
                lines.append(f"   {p1}\n   vs\n   {p2}")
                lines.append(f"   📅 Дедлайн: {deadline_str}")
                btn_label = f"📝 Матч {i}: {round_name}"
                if len(btn_label) > 64:
                    btn_label = (btn_label[:61]).rstrip() + "…"
                sub_keyboard.append(
                    [
                        {
                            "text": btn_label,
                            "callback_data": f"result_enter_{m.pk}",
                        }
                    ]
                )

        text = "\n".join(lines)
        reply_markup = {"inline_keyboard": sub_keyboard} if sub_keyboard else None
        ok = bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer("Матчи" if ok else "Сообщение не отправлено")
        return

    elif callback_data.startswith("subscription_tier_"):
        # Детальная карточка тарифа + кнопка оплаты
        if not base_url:
            _answer("Ссылка на оплату недоступна")
            return

        base = base_url.rstrip("/")
        try:
            tier_id = int(callback_data[len("subscription_tier_") :])
        except (ValueError, TypeError):
            _answer("Неверные данные тарифа")
            return

        tier_obj = SubscriptionTier.objects.filter(is_visible=True, id=tier_id).first()
        if not tier_obj:
            _answer("Тариф недоступен")
            return

        # Красивая карточка тарифа
        name = tier_obj.display_name or tier_obj.get_name_display()
        price_str = f"{tier_obj.price} ₽"

        # Акционная цена
        now = timezone.now()
        show_original = (
            tier_obj.original_price is not None
            and tier_obj.original_price > tier_obj.price
            and (
                tier_obj.original_price_ends_at is None
                or tier_obj.original_price_ends_at > now
            )
        )
        if show_original:
            price_block = (
                f"💰 <b>Стоимость:</b> <s>{tier_obj.original_price} ₽</s> → {price_str}"
            )
        else:
            price_block = f"💰 <b>Стоимость:</b> {price_str}"

        # Особые бейджи
        badges: list[str] = []
        if tier_obj.is_popular:
            badges.append("🔥 Популярный выбор")
        if tier_obj.first_subscription_one_ruble:
            badges.append("✨ Первая подписка за 1 ₽")

        # Регистрации на турниры
        if tier_obj.is_unlimited:
            registrations_text = "♾️ Неограниченный FAN-token"
        elif tier_obj.fancoin_per_purchase > 0:
            registrations_text = (
                f"🪙 <b>{tier_obj.fancoin_per_purchase}</b> FAN-token за покупку"
            )
        else:
            registrations_text = "🚫 FAN-token не включён"

        # Особенности тарифа (чек‑лист как на сайте)
        feature_rows: list[tuple[bool, str]] = [
            (tier_obj.can_see_stats, "Статистика и рейтинги"),
            (tier_obj.can_read_comments, "Чтение комментариев и отзывов"),
            (tier_obj.can_write_comments, "Возможность оставлять комментарии"),
            (tier_obj.can_rate_opponents, "Оценка соперников после матчей"),
            (tier_obj.has_private_chat, "Доступ в чат игроков"),
            (tier_obj.has_sparring, "Организация и участие в спаррингах"),
            (
                tier_obj.one_day_tournament_discount > 0,
                (
                    f"Скидка {tier_obj.one_day_tournament_discount}% на однодневные турниры"
                ),
            ),
            (tier_obj.has_admin_support, "Приоритетная поддержка администратора"),
            (tier_obj.has_badge, "Особый статус в профиле"),
        ]

        features_lines: list[str] = []
        for enabled, label in feature_rows:
            icon = "✅" if enabled else "❌"
            features_lines.append(f"{icon} {label}")

        features_text = "\n".join(features_lines)

        lines = [
            "💎 <b>Тариф подписки</b>",
            "",
            f"🔹 <b>{name}</b>",
            f"⏱ <b>Срок действия:</b> {tier_obj.duration_label}",
            price_block,
            f"🎯 <b>FAN-token:</b>\n{registrations_text}",
        ]
        if badges:
            lines.append("")
            lines.append("🏷 <b>Особенности тарифа:</b>")
            lines.append("\n".join(f"• {b}" for b in badges))

        lines.append("")
        lines.append("🚀 <b>Что даёт этот тариф:</b>")
        lines.append(features_text)
        lines.append("")
        lines.append(
            "После оплаты подписка привяжется к вашему аккаунту, "
            "а доступ к возможностям откроется автоматически."
        )

        text = "\n".join(lines)

        pay_url = f"{base}/payments/preview/?type=subscription&id={tier_obj.id}"
        reply_markup = {
            "inline_keyboard": [
                [{"text": "✅ Оплатить подписку", "url": pay_url}],
                [
                    {
                        "text": "⬅️ Назад к тарифам",
                        "callback_data": "menu_my_subscription",
                    }
                ],
            ]
        }

        ok = bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer("Тариф" if ok else "Сообщение не отправлено")
        return

    elif callback_data == "menu_my_profile":
        try:
            # Получаем сезонные очки
            from apps.tournaments.season_utils import get_current_season

            current_season = get_current_season()
            season_points = 0
            try:
                # Используем getattr для безопасного доступа к OneToOneField
                sp = getattr(player, "season_points", None)
                if sp and hasattr(sp, "season_name") and hasattr(sp, "season_year"):
                    if (
                        sp.season_name == current_season.name
                        and sp.season_year == current_season.year
                    ):
                        season_points = sp.current_season_points
            except Exception as e:
                logger.debug("Error getting season points: %s", e)
                season_points = 0

            # Получаем информацию о подписке
            try:
                sub = getattr(user, "subscription", None)
                if sub:
                    tier = sub.tier
                    tier_name = tier.get_name_display()
                    is_valid = sub.is_valid()
                    if is_valid:
                        sub_status = f"✅ {tier_name}"
                    else:
                        sub_status = f"❌ {tier_name} (истекла)"
                else:
                    sub_status = "❌ Нет подписки"
            except Exception as e:
                logger.debug("Error getting subscription: %s", e)
                sub_status = "❌ Нет подписки"

            # Формируем красивую таблицу
            lines = [
                "👤 <b>МОЙ ПРОФИЛЬ</b>",
                "",
                f"<b>{player}</b>",
                "",
                "📊 <b>ОСНОВНАЯ ИНФОРМАЦИЯ</b>",
                "━━━━━━━━━━━━━━━━━━",
                f"📍 Город: <b>{player.city or '—'}</b>",
                f"🎯 Уровень: <b>{player.get_skill_level_display()}</b>",
                f"📈 Сила: <b>{player.ntrp_level}</b>",
            ]

            # Добавляем дополнительную информацию, если есть
            try:
                if player.birth_date:
                    from datetime import date

                    today = date.today()
                    age = (
                        today.year
                        - player.birth_date.year
                        - (
                            (today.month, today.day)
                            < (player.birth_date.month, player.birth_date.day)
                        )
                    )
                    lines.append(f"🎂 Возраст: <b>{age} лет</b>")
            except Exception:
                pass

            try:
                if player.gender:
                    from apps.users.models import Gender

                    gender_display = dict(Gender.choices).get(
                        player.gender, player.gender
                    )
                    lines.append(f"⚧️ Пол: <b>{gender_display}</b>")
            except Exception:
                pass

            try:
                if player.forehand:
                    from apps.users.models import Forehand

                    forehand_display = dict(Forehand.choices).get(
                        player.forehand, player.forehand
                    )
                    lines.append(f"✋ Ведущая рука: <b>{forehand_display}</b>")
            except Exception:
                pass

            lines.extend(
                [
                    "",
                    "🏆 <b>РЕЙТИНГ И СТАТИСТИКА</b>",
                    "━━━━━━━━━━━━━━━━━━",
                    f"💎 Рейтинг: <b>{player.total_points:.1f}</b>",
                    f"🎖️ Очки сезона: <b>{season_points}</b>",
                    f"🎾 Матчей: <b>{player.matches_played}</b>",
                    f"✅ Побед: <b>{player.matches_won}</b>",
                    f"📊 Процент побед: <b>{player.win_rate}%</b>",
                    "",
                    "💳 <b>ПОДПИСКА</b>",
                    "━━━━━━━━━━━━━━━━━━",
                    f"{sub_status}",
                ]
            )

            text = "\n".join(lines)
            ok = bot.send_to_user(chat_id, text)
            _answer("Профиль" if ok else "Ошибка отправки")
            if not ok:
                logger.warning(
                    "menu_my_profile: send_message failed for chat_id=%s", chat_id
                )
        except Exception as e:
            logger.exception("menu_my_profile: error for chat_id=%s: %s", chat_id, e)
            _answer("Ошибка загрузки профиля")
            bot.send_to_user(
                chat_id, "❌ Произошла ошибка при загрузке профиля. Попробуйте позже."
            )

    elif callback_data == "menu_my_subscription":
        try:
            sub = getattr(user, "subscription", None)
        except Exception:
            sub = None

        reply_markup = None

        if not sub:
            text = (
                "📋 <b>Моя подписка</b>\n\n"
                "❌ <b>Нет активной подписки</b>\n\n"
                "Выберите тариф ниже, чтобы оформить доступ к полному функционалу TennisFan."
            )
        else:
            tier = sub.tier
            tier_name = tier.get_name_display()
            is_valid = sub.is_valid()
            is_cancelled = sub.is_cancelled

            # Статус подписки
            if is_cancelled and is_valid:
                status_emoji = "⚠️"
                status_text = (
                    f"Отменена (действует до {sub.end_date.strftime('%d.%m.%Y')})"
                )
            elif is_valid:
                status_emoji = "✅"
                status_text = "Активна"
            else:
                status_emoji = "❌"
                status_text = "Истекла"

            # Дата окончания
            now = timezone.now()
            end_str = sub.end_date.strftime("%d.%m.%Y") if sub.end_date else "—"
            days_left = None
            if sub.end_date and is_valid:
                delta = sub.end_date.date() - now.date()
                days_left = delta.days
                if days_left <= 3 and days_left >= 0:
                    end_str = f"{end_str} (через {days_left} дн.)"

            # Регистрации на турниры
            if sub.has_unlimited_fancoin():
                reg_text = "♾️ Безлимит FAN-token"
            elif tier.fancoin_per_purchase == 0 and sub.get_fancoin_balance() == 0:
                reg_text = "🚫 Недоступно"
            else:
                remaining = sub.get_fancoin_balance()
                reg_text = f"🪙 Баланс: {remaining}"

            # Список возможностей тарифа
            features = []
            if tier.can_see_stats:
                features.append("✅ Просмотр статистики и рейтингов")
            if tier.can_read_comments:
                features.append("✅ Чтение комментариев")
            if tier.can_write_comments:
                features.append("✅ Написание комментариев")
            if tier.can_rate_opponents:
                features.append("✅ Оценка соперников после матчей")
            if tier.has_private_chat:
                features.append("✅ Доступ в закрытый чат сообщества")
            if tier.has_sparring:
                features.append("✅ Организация спаррингов")
            if tier.one_day_tournament_discount > 0:
                features.append(
                    f"✅ Скидка {tier.one_day_tournament_discount}% на однодневные турниры"
                )
            if tier.has_admin_support:
                features.append("✅ Приоритетная поддержка администратора")
            if tier.has_badge:
                features.append("✅ Особый статус в профиле")

            features_text = "\n".join(features) if features else "• Базовые функции"

            # Формирование сообщения
            lines = [
                "📋 <b>Моя подписка</b>",
                "",
                f"💎 <b>Тариф:</b> {tier_name}",
                f"{status_emoji} <b>Статус:</b> {status_text}",
                f"📅 <b>Истекает:</b> {end_str}",
                "",
                "🎯 <b>Регистрации на турниры:</b>",
                reg_text,
                "",
                "✨ <b>Ваши возможности:</b>",
                features_text,
            ]

            # Предупреждение если истекает скоро
            if (
                days_left is not None
                and days_left <= 3
                and days_left >= 0
                and not is_cancelled
            ):
                lines.append("")
                if days_left == 0:
                    lines.append("🚨 <b>Подписка истекает сегодня!</b>")
                elif days_left == 1:
                    lines.append("⚠️ <b>Подписка истекает завтра!</b>")
                else:
                    lines.append(f"⚠️ <b>Подписка истекает через {days_left} дн.</b>")
                lines.append("Продлите сейчас, чтобы не потерять доступ!")

            text = "\n".join(lines)

        # Кнопки с тарифами: сначала показываем карточку тарифа, затем отдельная кнопка «Оплатить»
        if base_url:
            tiers = list(
                SubscriptionTier.objects.filter(is_visible=True).order_by(
                    "sort_order", "price", "id"
                )
            )
            buttons: list[list[dict]] = []
            for tier_obj in tiers:
                label = f"{tier_obj.display_name or tier_obj.get_name_display()} · {tier_obj.duration_label}"
                buttons.append(
                    [
                        {
                            "text": f"💳 {label}",
                            "callback_data": f"subscription_tier_{tier_obj.id}",
                        }
                    ]
                )
            if buttons:
                reply_markup = {"inline_keyboard": buttons}

        ok = bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer("Подписка" if ok else "Ошибка отправки")
        if not ok:
            logger.warning(
                "menu_my_subscription: send_message failed for chat_id=%s", chat_id
            )

    elif callback_data == "menu_private_chat":
        # Чат игроков сейчас открытый: не ограничиваем доступ подпиской,
        # просто отдаём ссылку из настроек или создаём инвайт.
        reply_markup = None

        if not bot.is_private_chat_configured():
            text = (
                "💬 <b>Чат игроков</b>\n\n"
                "Ссылка на чат временно недоступна. Попробуйте чуть позже."
            )
        else:
            # Та же ссылка на чат, что закреплена в футере сайта (TELEGRAM_PUBLIC_COMMUNITY_URL)
            community_url = (
                getattr(settings, "TELEGRAM_PUBLIC_COMMUNITY_URL", None) or ""
            ).strip()
            if community_url:
                text = (
                    "💬 <b>Закрытый канал сообщества</b>\n\n"
                    "✅ Доступ подтверждён.\n"
                    "Перейдите по ссылке ниже"
                )
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "➡️ Чат игроков", "url": community_url}]
                    ]
                }
            else:
                # Fallback: одноразовая ссылка через API, если URL не задан
                from apps.telegram_bot.services import _get_private_chat_channel_id

                channel_chat_id = _get_private_chat_channel_id()
                invite_link = bot.create_private_chat_invite_link(
                    expire_seconds=1800, member_limit=1, chat_id=channel_chat_id
                )
                if invite_link:
                    text = (
                        "💬 <b>Закрытый канал сообщества</b>\n\n"
                        "✅ Доступ подтверждён.\n"
                        "Ниже ваша персональная ссылка для входа в канал.\n\n"
                        "⚠️ Ссылка одноразовая и действует 30 минут."
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "➡️ Войти в закрытый канал", "url": invite_link}]
                        ]
                    }
                else:
                    text = (
                        "⚠️ <b>Не удалось создать приглашение в канал</b>\n\n"
                        "Попробуйте ещё раз через минуту. Если ошибка повторяется, обратитесь в поддержку."
                    )

        ok = bot.send_to_user(chat_id, text, reply_markup=reply_markup)
        _answer("Чат игроков" if ok else "Ошибка отправки")
        if not ok:
            logger.warning(
                "menu_private_chat: send_message failed for chat_id=%s", chat_id
            )

    elif callback_data == "menu_go_to_site" and base_url:
        site_url = base_url.rstrip("/")
        bot.send_to_user(
            chat_id,
            f'🌐 <b>Перейти на сайт</b>\n\n<a href="{site_url}">{site_url}</a>',
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
        logger.info(
            "user_bot callback_query: chat_id=%s data=%s",
            callback_query.get("message", {}).get("chat", {}).get("id"),
            callback_data,
        )
        handled = _handle_proposal_callback(callback_query, base_url)
        if not handled:
            handled = _handle_extension_request_callback(callback_query, base_url)
        if not handled:
            handled = _handle_sparring_callback(callback_query, base_url)
        if not handled:
            handled = _handle_result_type_callback(callback_query)
        if not handled:
            handled = _handle_result_enter_callback(callback_query)
        if not handled:
            handled = _handle_menu_callback(callback_query, base_url)
        cq_id = callback_query.get("id")
        if cq_id and not handled and is_telegram_api_enabled():
            token = bot._get_bot_token()
            if token:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                        json={"callback_query_id": str(cq_id)},
                        timeout=5,
                        proxies=telegram_requests_proxies(),
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
                link.binding_token = None
                link.token_created_at = None
                link.save(
                    update_fields=[
                        "user_bot_chat_id",
                        "binding_token",
                        "token_created_at",
                    ]
                )
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
                    .select_related(
                        "tournament", "player1", "player2", "team1", "team2"
                    )
                    .first()
                )
                if not match or match.status in (
                    Match.MatchStatus.COMPLETED,
                    Match.MatchStatus.WALKOVER,
                ):
                    cache.delete(cache_key)
                    bot.send_message(chat_id, "Матч не найден или уже завершён.")
                    return JsonResponse({"ok": True})
                participants = get_match_participants(match)
                if player not in participants:
                    cache.delete(cache_key)
                    bot.send_message(chat_id, "Вы не участвуете в этом матче.")
                    return JsonResponse({"ok": True})
                if match.result_proposals.filter(
                    status=Match.ProposalStatus.PENDING
                ).exists():
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
                result = (
                    Match.ResultChoice.WIN
                    if (is_p1 and sets_won_p1 > sets_won_p2)
                    or (not is_p1 and sets_won_p2 > sets_won_p1)
                    else Match.ResultChoice.LOSS
                )
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
                tour_name = match.tournament.name if match.tournament else "спарринг"
                for opp_user in get_match_opponent_users(match, player):
                    Notification.objects.create(
                        user=opp_user,
                        message=f"{player} предложил результат матча в турнире {tour_name}. У вас 3 часа на подтверждение.",
                        url=reverse("my_matches"),
                    )
                try:
                    tg_notify.notify_result_proposal(proposal)
                except Exception as e:
                    logger.exception("notify_result_proposal failed: %s", e)
                cache.delete(cache_key)
                bot.send_message(
                    chat_id,
                    "✅ Результат отправлен на подтверждение сопернику. Ожидайте подтверждения в боте.",
                )
                return JsonResponse({"ok": True})
        cache.delete(cache_key)

    # Нажатие кнопок подменю «Спарринг»
    if text in REPLY_SPARRING_BUTTONS:
        if text == REPLY_BTN_BACK_TO_MENU:
            bot.send_message(
                chat_id, "Главное меню:", reply_markup=_reply_menu_keyboard()
            )
            return JsonResponse({"ok": True})
        link = _get_link_by_chat_id(chat_id)
        if not link:
            bot.send_message(
                chat_id, "Сначала подключите бота с сайта (профиль → Telegram)."
            )
            return JsonResponse({"ok": True})
        user = link.user
        player = getattr(user, "player", None)
        if not player:
            try:
                player = Player.objects.create(user=user)
            except Exception:
                bot.send_message(chat_id, "Ошибка профиля игрока.")
                return JsonResponse({"ok": True})
        reply_to_sparring = {
            REPLY_BTN_SPARRING_MY_REQUESTS: "sparring_my_requests",
            REPLY_BTN_SPARRING_MY_RESPONSES: "sparring_my_responses",
            REPLY_BTN_SPARRING_RESPONSES_TO_ME: "sparring_responses_to_me",
        }.get(text)
        if reply_to_sparring:
            fake_callback = {
                "id": None,
                "callback_data": reply_to_sparring,
                "message": {"chat": {"id": chat_id}},
            }
            _handle_sparring_callback(fake_callback, base_url)
        return JsonResponse({"ok": True})

    # Нажатие реплай-кнопок главного меню
    if text in REPLY_MENU_BUTTONS:
        if text == REPLY_BTN_GO_TO_SITE:
            site_url = base_url.rstrip("/")
            bot.send_message(
                chat_id,
                f'🌐 <b>Перейти на сайт</b>\n\n<a href="{site_url}">{site_url}</a>',
                reply_markup=_reply_menu_keyboard(),
            )
            return JsonResponse({"ok": True})
        if text == REPLY_BTN_SPARRING:
            link = _get_link_by_chat_id(chat_id)
            if not link:
                bot.send_message(
                    chat_id, "Сначала подключите бота с сайта (профиль → Telegram)."
                )
                return JsonResponse({"ok": True})
            from apps.sparring.utils import user_has_sparring_access

            if not user_has_sparring_access(link.user):
                bot.send_message(
                    chat_id,
                    "❌ Оформите подписку для доступа к разделу спаррингов.",
                    reply_markup=_reply_menu_keyboard(),
                )
                return JsonResponse({"ok": True})
            bot.send_message(
                chat_id,
                "🎾 <b>Спарринг</b>\n\nВыберите раздел:",
                reply_markup=_reply_sparring_keyboard(),
            )
            return JsonResponse({"ok": True})
        link = _get_link_by_chat_id(chat_id)
        if not link:
            bot.send_message(
                chat_id, "Сначала подключите бота с сайта (профиль → Telegram)."
            )
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
                REPLY_BTN_PRIVATE_CHAT: "menu_private_chat",
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
@require_http_methods(["POST"])
def connect_redirect(request):
    """
    Редирект на t.me/BotUsername?start=TOKEN для привязки Telegram.
    Генерирует токен, сохраняет в UserTelegramLink, перенаправляет пользователя в бота.
    """
    consent_raw = str(request.POST.get("telegram_transfer_consent", "")).strip().lower()
    consent_ok = consent_raw in {"1", "true", "on", "yes"}
    if not consent_ok:
        messages.error(
            request,
            "Для подключения Telegram-бота необходимо подтвердить согласие на передачу данных в Telegram.",
        )
        return redirect(_get_redirect_url_after_bot_settings_change(request))

    # Фиксируем юридически значимое согласие на передачу данных в Telegram
    try:
        TelegramTransferConsentLog.objects.create(
            user=request.user,
            consent_version=TELEGRAM_TRANSFER_CONSENT_VERSION,
            ip_address=_get_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:1000],
        )
    except Exception as exc:
        logger.warning(
            "Failed to log Telegram transfer consent for user=%s: %s",
            request.user.pk,
            exc,
        )

    if not bot.is_configured():
        messages.error(
            request,
            "Telegram-бот временно недоступен. Проверьте TELEGRAM_USER_BOT_TOKEN в .env и перезапустите сервер.",
        )
        return redirect(_get_redirect_url_after_bot_settings_change(request))

    link, _ = UserTelegramLink.objects.get_or_create(
        user=request.user,
        defaults={
            "telegram_chat_id": None,
            "user_bot_chat_id": None,
            "binding_token": secrets.token_urlsafe(32),
        },
    )
    token = link.get_or_create_binding_token()
    username = bot.get_bot_username()
    if not username:
        messages.error(request, "Не удалось получить ссылку на бота.")
        return redirect(_get_redirect_url_after_bot_settings_change(request))
    url = f"https://t.me/{username}?start={token}"
    return redirect(url)


@login_required
@require_http_methods(["POST"])
def disconnect_user_bot(request):
    """
    Отключить пользовательского Telegram-бота в ЛК:
    - очищает chat_id пользовательского бота
    - сбрасывает одноразовый токен привязки
    """
    link = UserTelegramLink.objects.filter(user=request.user).first()
    if not link or not link.user_bot_chat_id:
        messages.info(request, "Telegram-бот уже отключён.")
        return redirect(_get_redirect_url_after_bot_settings_change(request))

    link.user_bot_chat_id = None
    link.binding_token = None
    link.token_created_at = None
    link.save(update_fields=["user_bot_chat_id", "binding_token", "token_created_at"])

    messages.success(
        request,
        "Telegram-бот отключён. Уведомления в Telegram больше не будут приходить.",
    )
    return redirect(_get_redirect_url_after_bot_settings_change(request))
