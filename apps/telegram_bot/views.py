"""
Webhook пользовательского Telegram-бота и редирект привязки с сайта.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from html import escape
from typing import Any, TypedDict, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from loguru import logger

from apps.core.models import TelegramTransferConsentLog, UserTelegramLink
from apps.subscriptions.models import SubscriptionTier
from apps.tournaments.deadline_extensions import request_deadline_extension
from apps.tournaments.models import Match, MatchResultProposal, Tournament
from apps.tournaments.proposal_service import (
    ProposalValidationError,
    resolve_result_proposal,
    submit_match_result,
)
from apps.tournaments.utils import (
    get_match_opponent_users,
    get_match_participants,
    get_match_player_side,
    match_participant_filter,
)
from apps.users.models import Player, User

from . import services as bot
from .binding import consume_binding_token, lock_user_bot_link
from .models import TelegramUpdateReceipt
from .pagination import PAGE_SIZE, navigation_rows, normalize_offset
from .private_chat import get_private_chat_access_status
from .telegram_http import is_telegram_api_enabled
from .updates import user_bot_update_namespace, validate_user_update


class _TournamentMatchGroup(TypedDict):
    """Турнир, его ближайший дедлайн и матчи в меню пользователя."""

    tournament: Tournament
    matches: list[Match]
    nearest_deadline: datetime | None


def _get_site_base_url() -> str:
    """Получить базовый URL сайта для ссылок."""
    from django.conf import settings

    base = str(getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", None) or "")
    if base:
        return base.rstrip("/") + "/"
    return "http://localhost:8000/" if settings.DEBUG else "https://tennisfan.ru/"


CACHE_KEY_RESULT_ENTRY = "tg_result_entry:%s"
CACHE_RESULT_ENTRY_TIMEOUT = 300  # 5 min
TELEGRAM_TRANSFER_CONSENT_VERSION = "v1-2026-02-10"


def _clear_result_entry_on_commit(cache_key: str, match_id: int) -> None:
    """Не терять ввод счёта при откате и не очищать выбор другого матча."""

    def clear() -> None:
        """Очистить только прежнее состояние после успешной фиксации update."""
        if cache.get(cache_key) == match_id:
            cache.delete(cache_key)

    transaction.on_commit(clear, robust=True)


def _set_result_entry_on_commit(cache_key: str, match_id: int) -> None:
    """Менять выбранный матч только после успешной фиксации update."""

    def save() -> None:
        """Сохранить выбор после commit, не оставляя его от неуспешного callback."""
        cache.set(cache_key, match_id, CACHE_RESULT_ENTRY_TIMEOUT)

    transaction.on_commit(save, robust=True)


def _get_redirect_url_after_bot_settings_change(request: HttpRequest) -> str:
    """Вернуться в ЛК только по локальному адресу после изменения настроек бота."""
    next_url = str(request.POST.get("next", "")).strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    profile_url: str = reverse(
        "profile", kwargs={"pk": cast(User, request.user).player.pk}
    )
    return profile_url


def _parse_score_input(text: str) -> tuple[list[tuple[int, int]] | None, str | None]:
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
        if ga < 0 or gb < 0 or ga > 32767 or gb > 32767:
            return None, "Геймы должны быть неотрицательными числами до 32767"
        sets_list.append((ga, gb))
    return sets_list, None


def _walkover_choice_keyboard(match_pk: int) -> dict[str, Any]:
    """Клавиатура выбора стороны неявки для матча, который не состоялся.

    Args:
        match_pk: Идентификатор матча.

    Returns:
        Inline-клавиатура Telegram с двумя вариантами неявки.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Соперник не явился",
                    "callback_data": f"result_type_{match_pk}_walkover_win",
                },
                {
                    "text": "❌ Я не явился (−40)",
                    "callback_data": f"result_type_{match_pk}_walkover_loss",
                },
            ],
        ]
    }


def _proposer_is_side1(match: Match, player: Player) -> bool:
    """Играет ли участник за первую сторону (player1 / team1)."""
    return get_match_player_side(match, player) == 1


def _result_match_context(match: Match) -> str:
    """Безопасный HTML-контекст турнирного или спаррингового матча."""
    if match.tournament:
        context = match.tournament.name
    elif match.is_sparring():
        context = "Спарринг"
    else:
        context = "Матч"
    return f"{escape(context)}, {escape(match.round_name or '—')}"


def _webhook_secret_ok(request: HttpRequest) -> bool:
    """Проверка секрета webhook (X-Telegram-Bot-Api-Secret-Token)."""
    secret = getattr(settings, "TELEGRAM_USER_BOT_WEBHOOK_SECRET", None) or ""
    if not secret:
        return False
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return secrets.compare_digest(provided.encode(), secret.encode())


def _get_client_ip(request: HttpRequest) -> str | None:
    """Определить IP клиента с учётом reverse proxy."""
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded:
        # Берём первый IP в цепочке X-Forwarded-For
        return forwarded.split(",")[0].strip()
    real_ip = str(request.META.get("HTTP_X_REAL_IP") or "").strip()
    if real_ip:
        return real_ip
    remote_addr = str(request.META.get("REMOTE_ADDR") or "").strip()
    return remote_addr or None


# Тексты кнопок реплай-меню (должны совпадать при обработке сообщения)
REPLY_BTN_MY_PROFILE = "👤 Мой профиль"
REPLY_BTN_MY_MATCHES = "🎾 Мои матчи"
REPLY_BTN_TOURNAMENTS = "🏆 Турниры"
REPLY_BTN_HELP = "❓ Помощь"
REPLY_BTN_MY_SUBSCRIPTIONS = "📋 Мои подписки"
REPLY_BTN_PRIVATE_CHAT = "💬 Чат игроков"
REPLY_BTN_PUBLIC_CHAT = "💬 Открытый чат"
REPLY_BTN_SUBSCRIBER_CHAT = "🔒 Чат подписчиков"
REPLY_BTN_GO_TO_SITE = "🌐 Перейти на сайт"
REPLY_BTN_SPARRING = "🎾 Спарринг"
REPLY_BTN_SPARRING_MY_REQUESTS = "📝 Мои заявки"
REPLY_BTN_SPARRING_MY_RESPONSES = "🙋 Мои отклики"
REPLY_BTN_SPARRING_RESPONSES_TO_ME = "📬 Отклики на мои заявки"
REPLY_BTN_BACK_TO_MENU = "В меню"

REPLY_MENU_BUTTONS = (
    REPLY_BTN_MY_PROFILE,
    REPLY_BTN_MY_MATCHES,
    REPLY_BTN_TOURNAMENTS,
    REPLY_BTN_HELP,
    REPLY_BTN_MY_SUBSCRIPTIONS,
    REPLY_BTN_PRIVATE_CHAT,
    REPLY_BTN_PUBLIC_CHAT,
    REPLY_BTN_SUBSCRIBER_CHAT,
    REPLY_BTN_GO_TO_SITE,
    REPLY_BTN_SPARRING,
)
REPLY_SPARRING_BUTTONS = (
    REPLY_BTN_SPARRING_MY_REQUESTS,
    REPLY_BTN_SPARRING_MY_RESPONSES,
    REPLY_BTN_SPARRING_RESPONSES_TO_ME,
    REPLY_BTN_BACK_TO_MENU,
)


def _reply_menu_keyboard() -> dict[str, Any]:
    """Показать открытый чат отдельно от явно включённого подписного сообщества."""
    rows = [
        [{"text": REPLY_BTN_MY_PROFILE}, {"text": REPLY_BTN_MY_MATCHES}],
        [{"text": REPLY_BTN_TOURNAMENTS}, {"text": REPLY_BTN_HELP}],
        [{"text": REPLY_BTN_MY_SUBSCRIPTIONS}, {"text": REPLY_BTN_PUBLIC_CHAT}],
    ]
    if bot.is_private_chat_configured():
        rows.append([{"text": REPLY_BTN_SUBSCRIBER_CHAT}])
    rows.append([{"text": REPLY_BTN_SPARRING}, {"text": REPLY_BTN_GO_TO_SITE}])
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _reply_sparring_keyboard() -> dict[str, Any]:
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


def _main_menu_keyboard(site_base_url: str) -> dict[str, Any]:
    """Inline-клавиатура (дублирует меню для старых клиентов)."""
    rows = [
        [
            {"text": "🎾 Мои матчи", "callback_data": "menu_my_matches"},
            {"text": "👤 Мой профиль", "callback_data": "menu_my_profile"},
        ],
        [
            {"text": "📋 Моя подписка", "callback_data": "menu_my_subscription"},
            {"text": REPLY_BTN_PUBLIC_CHAT, "callback_data": "menu_public_chat"},
        ],
        [
            {"text": REPLY_BTN_TOURNAMENTS, "callback_data": "menu_tournaments"},
            {"text": "🎾 Спарринг", "callback_data": "menu_sparring"},
        ],
    ]
    if bot.is_private_chat_configured():
        rows.append(
            [
                {
                    "text": REPLY_BTN_SUBSCRIBER_CHAT,
                    "callback_data": "menu_subscriber_chat",
                }
            ]
        )
    rows.append([{"text": "🌐 На сайт", "url": site_base_url.rstrip("/")}])
    return {"inline_keyboard": rows}


def _get_link_by_chat_id(chat_id: int | None) -> UserTelegramLink | None:
    """Заблокировать свежую явную привязку на время авторизованного действия."""
    if chat_id is None or isinstance(chat_id, bool) or chat_id <= 0:
        return None
    return lock_user_bot_link(chat_id)


def _send_message_required(
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    """Не подтверждать webhook при неуспешной отправке ответа пользователю."""
    _, sent = bot.send_message(chat_id, text, reply_markup=reply_markup)
    if not sent:
        raise RuntimeError("telegram_response_not_sent")


def _send_to_user_required(
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Возвращать успех только после отправки, иначе откатить текущий update."""
    if not bot.send_to_user(chat_id, text, reply_markup=reply_markup):
        raise RuntimeError("telegram_response_not_sent")
    return True


def _answer_callback(
    callback_query_id: str | int | None,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    """Ответить на callback_query в Telegram (убрать «часики», опционально показать текст)."""
    if not is_telegram_api_enabled():
        return
    if callback_query_id is None:
        return
    payload: dict[str, Any] = {"callback_query_id": str(callback_query_id)}
    if text:
        payload["text"] = text[:200]
    if show_alert:
        payload["show_alert"] = True
    bot._api_post("answerCallbackQuery", payload, timeout=5)


def _edit_message_remove_reply_markup(chat_id: int, message_id: int) -> None:
    """Убрать inline-кнопки у сообщения (после подтверждения/отклонения)."""
    if not is_telegram_api_enabled():
        return
    bot._api_post(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        },
        timeout=5,
    )


def _handle_proposal_callback(callback_query: dict[str, Any], base_url: str) -> bool:
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

    accept = callback_data.startswith("proposal_confirm_")
    try:
        proposal = resolve_result_proposal(pk, player, accept=accept)
    except ProposalValidationError as exc:
        _answer_callback(cq_id, str(exc), show_alert=True)
        return True
    # Техническая ошибка проходит в webhook: receipt откатывается и update можно повторить.
    if message_id:
        _edit_message_remove_reply_markup(chat_id, message_id)
    if accept:
        _answer_callback(cq_id, "✅ Результат применён.")
    else:
        match = proposal.match
        _send_message_required(
            chat_id,
            "❌ <b>Результат отклонён.</b>\n\n"
            f"Матч: {_result_match_context(match)}\n"
            f"{escape(match.get_player1_display())} — {escape(match.get_player2_display())}\n\n"
            f"Вы отклонили предложенный счёт. {escape(str(proposal.proposer))} уведомлён(а).",
        )
        _answer_callback(cq_id, "❌ Результат отклонён.")

    return True


def _handle_extension_request_callback(
    callback_query: dict[str, Any], base_url: str
) -> bool:
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
            status__in=(Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS),
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

    try:
        _, created = request_deadline_extension(match_pk, player)
    except ValidationError as exc:
        _answer_callback(cq_id, "; ".join(exc.messages), show_alert=True)
        return True
    if not created:
        _answer_callback(cq_id, "Запрос на продление уже отправлен.", show_alert=True)
        return True

    _answer_callback(
        cq_id, "Запрос отправлен. Администратор рассмотрит его в ближайшее время."
    )
    return True


def _handle_result_enter_callback(callback_query: dict[str, Any]) -> bool:
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
                    "text": "📝 Матч сыгран (ввести счёт)",
                    "callback_data": f"result_type_{match_pk}_normal",
                }
            ],
            *_walkover_choice_keyboard(match_pk)["inline_keyboard"],
        ]
    }

    _send_message_required(
        chat_id,
        f"📝 <b>Внести результат</b>\n\n"
        f"Матч: {_result_match_context(match)}\n"
        f"{escape(match.get_player1_display())} — {escape(match.get_player2_display())}\n\n"
        f"Вы играете за {side_text}\n\n"
        f"Выберите тип результата:",
        reply_markup=keyboard,
    )
    _answer_callback(cq_id, "Выберите тип результата")
    return True


def _handle_result_type_callback(callback_query: dict[str, Any]) -> bool:
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
        _set_result_entry_on_commit(CACHE_KEY_RESULT_ENTRY % chat_id, match_pk)
        side_text = (
            f"первую ({match.get_player1_display()})."
            if _proposer_is_side1(match, player)
            else f"вторую ({match.get_player2_display()})."
        )
        _send_message_required(
            chat_id,
            f"📝 <b>Внести результат</b>\n\n"
            f"Матч: {_result_match_context(match)}\n"
            f"{escape(match.get_player1_display())} — {escape(match.get_player2_display())}\n\n"
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
        try:
            submit_match_result(match.pk, player, result=result_choice)
        except ProposalValidationError as exc:
            _send_message_required(chat_id, f"❌ {escape(str(exc))}")
            _answer_callback(cq_id, "Результат не сохранён", show_alert=True)
            return True
        result_text = (
            "неявку соперника — вам засчитана победа, сопернику −40 очков"
            if result_type == "walkover_win"
            else "свою неявку — вам засчитано поражение и −40 очков"
        )
        _send_message_required(
            chat_id,
            f"✅ Результат сохранён. Матч не состоялся (Walkover).\n\n"
            f"Вы указали: <b>{result_text}</b>",
        )
        _answer_callback(cq_id, "Результат сохранён")
        return True

    _answer_callback(cq_id, "Неизвестный тип результата.", show_alert=True)
    return True


def _format_sparring_player_card(player: Player) -> str:
    """Форматирует карточку игрока для бота (моноширинный блок)."""
    from apps.users.models import SkillLevel

    name = str(player)
    age = player.age or "—"
    ntrp = f"{player.ntrp_level:.2f}" if player.ntrp_level else "—"
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
    body = escape("\n".join(lines))
    return f"👤 <b>Профиль игрока</b>\n\n<pre>{body}</pre>"


def _handle_sparring_callback(
    callback_query: dict[str, Any], base_url: str = ""
) -> bool:
    """
    Обработка callback для спаррингов:
    - sparring_my_requests, sparring_my_responses, sparring_responses_to_me
    - sparring_del_req_<id>, sparring_cancel_resp_<id>
    - sparring_req_<id>, sparring_cand_<id>, sparring_profile_<id>
    - contact_{response_id}, confirm_match_{response_id}
    - team_sparring_generate_{request_id}
    """
    from apps.sparring.doubles_services import confirm_team_sparring_series
    from apps.sparring.models import (
        DoublesMatchKind,
        DoublesMatchRequest,
        DoublesMatchRequestStatus,
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
        from apps.sparring.services import accept_sparring_invitation
        from apps.sparring.utils import user_has_sparring_access

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
            accept_sparring_invitation(inv_id, user.id)
        except ValueError as e:
            _answer_callback(cq_id, str(e), show_alert=True)
            return True

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

        team_request = (
            DoublesMatchRequest.objects.select_related("created_by")
            .prefetch_related("teams__members")
            .filter(pk=req_id)
            .first()
        )
        if not team_request:
            _answer_callback(cq_id, "Заявка не найдена.", show_alert=True)
            return True
        if team_request.created_by_id != player.id:
            _answer_callback(
                cq_id,
                "Только автор заявки может формировать матчи.",
                show_alert=True,
            )
            return True
        if team_request.kind != DoublesMatchKind.TEAM:
            _answer_callback(
                cq_id, "Эта заявка не является командным спаррингом.", show_alert=True
            )
            return True
        if team_request.status != DoublesMatchRequestStatus.READY:
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
        except (ValueError, PermissionError, DoublesMatchRequest.DoesNotExist) as exc:
            _answer_callback(cq_id, str(exc), show_alert=True)
            return True

        total = len(matches)
        singles_count = max(0, total - 1)
        _answer_callback(cq_id, "Матчи созданы!")
        _send_to_user_required(
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
            _send_to_user_required(
                chat_id, "📝 <b>Мои заявки</b>\n\nНет активных заявок."
            )
            _answer_callback(cq_id, "Мои заявки")
            return True
        lines = ["📝 <b>Мои заявки</b>", ""]
        keyboard = []
        for i, request_item in enumerate(requests_list, 1):
            short = (
                (request_item.description[:50] + "…")
                if len(request_item.description or "") > 50
                else (request_item.description or "—")
            )
            lines.append(
                f"<b>{i}.</b> {escape(request_item.city or '—')} · {escape(short)}"
            )
            lines.append("")
            keyboard.append(
                [
                    {
                        "text": f"🗑 Удалить заявку {i}",
                        "callback_data": f"sparring_del_req_{request_item.pk}",
                    }
                ]
            )
        text = "\n".join(lines)
        reply_markup = {"inline_keyboard": keyboard}
        _send_to_user_required(chat_id, text, reply_markup=reply_markup)
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
        from apps.sparring.services import cancel_sparring_request

        try:
            cancelled = cancel_sparring_request(req.pk, user.pk)
        except ValueError as exc:
            _answer_callback(cq_id, str(exc), show_alert=True)
            return True
        if not cancelled:
            _answer_callback(cq_id, "Заявка уже закрыта.", show_alert=True)
            return True
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
            _send_to_user_required(
                chat_id, "🙋 <b>Мои отклики</b>\n\nВы ещё не откликались на заявки."
            )
            _answer_callback(cq_id, "Мои отклики")
            return True
        status_labels: dict[str, str] = {
            str(SparringResponse.ResponseStatus.PENDING): "На рассмотрении",
            str(SparringResponse.ResponseStatus.ACCEPTED): "Принят",
            str(SparringResponse.ResponseStatus.REJECTED): "Отклонен",
        }
        lines = ["🙋 <b>Мои отклики</b>", ""]
        keyboard = []
        for i, response_item in enumerate(responses_list, 1):
            author = response_item.sparring_request.player
            st = status_labels.get(response_item.status, response_item.status)
            lines.append(
                f"<b>{i}.</b> Заявка: {escape(str(author))} "
                f"({escape(response_item.sparring_request.city or '—')})"
            )
            lines.append(f"   Статус: <b>{st}</b>")
            lines.append("")
            if response_item.status == SparringResponse.ResponseStatus.PENDING:
                keyboard.append(
                    [
                        {
                            "text": f"❌ Отменить отклик {i}",
                            "callback_data": f"sparring_cancel_resp_{response_item.pk}",
                        }
                    ]
                )
        text = "\n".join(lines)
        markup = {"inline_keyboard": keyboard} if keyboard else None
        _send_to_user_required(chat_id, text, reply_markup=markup)
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
        from apps.sparring.services import cancel_sparring_response

        try:
            cancel_sparring_response(resp.pk, user.pk)
        except ValueError as exc:
            _answer_callback(cq_id, str(exc), show_alert=True)
            return True
        _answer_callback(cq_id, "Отклик отменён")
        _send_to_user_required(chat_id, "❌ Отклик отменён.")
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
            _send_to_user_required(
                chat_id, "📬 <b>Отклики на мои заявки</b>\n\nНет заявок с откликами."
            )
            _answer_callback(cq_id, "Отклики на мои заявки")
            return True
        lines = ["📬 <b>Отклики на мои заявки</b>", ""]
        keyboard = []
        for i, request_item in enumerate(my_requests_with_responses, 1):
            cnt = request_item.responses.count()
            lines.append(
                f"<b>{i}.</b> {escape(request_item.city or '—')} — откликов: {cnt}"
            )
            lines.append("")
            keyboard.append(
                [
                    {
                        "text": f"📋 Заявка {i}",
                        "callback_data": f"sparring_req_{request_item.pk}",
                    }
                ]
            )
        text = "\n".join(lines)
        reply_markup = {"inline_keyboard": keyboard}
        _send_to_user_required(chat_id, text, reply_markup=reply_markup)
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
        lines = ["📬 <b>Кандидаты</b>", f"Заявка: {escape(req.city or '—')}", ""]
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
        _send_to_user_required(chat_id, text, reply_markup=reply_markup)
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
        _send_to_user_required(chat_id, text, reply_markup=reply_markup)
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
            _send_to_user_required(chat_id, text, reply_markup=reply_markup)
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
            _send_to_user_required(
                chat_id,
                f"📱 <b>Контакт игрока {escape(str(respondent))}:</b>\n\n"
                f"{escape(contact_info)}",
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

        from apps.sparring.services import confirm_sparring_response

        try:
            match = confirm_sparring_response(response.pk, user.pk)
        except ValueError as exc:
            _answer_callback(cq_id, str(exc), show_alert=True)
            return True
        # Не подтверждаем update при технической ошибке: webhook откатит все изменения.
        if message_id:
            _edit_message_remove_reply_markup(chat_id, message_id)
        deadline_str = (
            timezone.localtime(match.deadline).strftime("%d.%m.%Y")
            if match.deadline
            else "не установлен"
        )
        _send_to_user_required(
            chat_id,
            "✅ <b>Игра подтверждена!</b>\n\n"
            f"Матч создан: {escape(match.get_player1_display())} — {escape(match.get_player2_display())}\n"
            f"Дедлайн: {deadline_str}",
        )
        _answer_callback(cq_id, "Игра подтверждена, матч создан!")

        return True

    return False


def _absolute_site_url(base_url: str, path: str) -> str:
    """Собрать абсолютную ссылку на сайт из базового адреса и пути."""
    base = (base_url or _get_site_base_url()).rstrip("/")
    return f"{base}{path}"


def _score_for_player(match: Match, player: Player) -> str:
    """Показать счёт со стороны игрока, не переворачивая пару и вторую сторону."""
    if match.status == Match.MatchStatus.WALKOVER and not match.has_set_scores():
        return "без счёта"
    side = get_match_player_side(match, player)
    parts: list[str] = []
    for index in (1, 2, 3):
        first = getattr(match, f"player1_set{index}")
        second = getattr(match, f"player2_set{index}")
        if first is None or second is None:
            continue
        if side == 2:
            first, second = second, first
        parts.append(f"{first}:{second}")
    return " ".join(parts) if parts else "—"


def _history_match_lines(match: Match, player: Player, number: int) -> list[str]:
    """Собрать строки одной завершённой встречи для истории матчей."""
    from apps.tournaments.proposal_service import result_summary_for_player

    if match.tournament_id and match.tournament is not None:
        title = f"🏆 {escape(match.tournament.name)}"
    else:
        title = "🎾 Спарринг"
    if match.round_name:
        title += f" · {escape(match.round_name)}"
    return [
        f"<b>{number}.</b> {title}",
        f"   {escape(match.get_player1_display())} — {escape(match.get_player2_display())}",
        f"   Счёт: <b>{_score_for_player(match, player)}</b>",
        f"   {escape(result_summary_for_player(match, player))}",
        "",
    ]


def _tournament_registration_url(tournament: Tournament, base_url: str) -> str | None:
    """Вернуть ссылку на регистрацию, если сайт её сейчас примет."""
    from apps.tournaments.models import TournamentStatus

    if tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED):
        return None
    if getattr(tournament, "bracket_generated", False):
        return None
    if getattr(tournament, "postpayment_window_started_at", None):
        return None
    if tournament.is_full():
        return None
    path = reverse("tournament_register", args=[tournament.slug])
    return _absolute_site_url(base_url, path)


def _tournament_card_lines(
    tournament: Tournament, number: int, participants_count: int
) -> list[str]:
    """Собрать строки карточки турнира для списка в боте."""
    date_text = (
        tournament.start_date.strftime("%d.%m.%Y") if tournament.start_date else "—"
    )
    fee = float(tournament.entry_fee or 0)
    fee_text = f"{fee:.0f} ₽" if fee > 0 else "бесплатно"
    limit_text = ""
    if tournament.max_participants:
        limit_text = (
            f" · участников: {participants_count}/{tournament.max_participants}"
        )
    elif participants_count:
        limit_text = f" · участников: {participants_count}"
    lines = [
        f"<b>{number}. {escape(tournament.name)}</b>",
        f"   📍 {escape(tournament.city or '—')} · {escape(tournament.get_format_display())}",
        f"   📅 {date_text} · взнос: {fee_text}{limit_text}",
    ]
    if tournament.registration_deadline:
        lines.append(
            "   ⏰ Регистрация до "
            + tournament.registration_deadline.strftime("%d.%m.%Y %H:%M")
        )
    return lines


def _handle_menu_callback(callback_query: dict[str, Any], base_url: str = "") -> bool:
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
            "menu_tournaments",
            "menu_help",
            "menu_private_chat",
            "menu_public_chat",
            "menu_subscriber_chat",
            "menu_go_to_site",
            "menu_sparring",
        }
        or callback_data.startswith("my_matches_tour_")
        or callback_data.startswith("my_matches_done_")
        or callback_data.startswith("tournaments_page_")
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
                player, _ = Player.objects.get_or_create(user=user)
            _handle_menu_callback_action(
                chat_id, cq_id, callback_data, user, player, base_url=base_url
            )
    except Exception:
        # Технический отказ должен откатить receipt и получить повтор от Telegram.
        raise
    return True


def _handle_menu_callback_action(
    chat_id: int,
    cq_id: str | None,
    callback_data: str,
    user: User,
    player: Player | None,
    base_url: str = "",
) -> None:
    """Отправка контента по выбранному пункту меню. cq_id=None при нажатии реплай-кнопки."""

    def _answer(caption: str) -> None:
        """Подтвердить callback, если меню вызвано inline-кнопкой."""
        if cq_id:
            _answer_callback(cq_id, caption)

    if callback_data == "menu_help":
        text = (
            "❓ <b>Помощь</b>\n\n"
            "Кнопки главного меню:\n"
            "🎾 <b>Мои матчи</b> — предстоящие матчи и история\n"
            "🏆 <b>Турниры</b> — список, карточки и запись\n"
            "👤 <b>Мой профиль</b> — статистика и подписка\n"
            "📋 <b>Моя подписка</b> — тарифы и возможности\n"
            "🎾 <b>Спарринг</b> — заявки и отклики\n"
            "💬 <b>Чаты</b> — открытый и подписной чаты\n"
            "🌐 <b>На сайт</b> — переход на платформу\n\n"
            "Во время ввода счёта отправьте /cancel для отмены.\n"
            "Оспорить результат можно кнопкой в уведомлении — откроется поддержка."
        )
        _send_to_user_required(chat_id, text, reply_markup=_reply_menu_keyboard())
        _answer("Помощь")
        return

    if callback_data == "menu_go_to_site":
        if base_url:
            site_url = base_url.rstrip("/")
            _send_to_user_required(
                chat_id,
                f'🌐 <b>Перейти на сайт</b>\n\n<a href="{site_url}">{site_url}</a>',
                reply_markup=_reply_menu_keyboard(),
            )
            _answer("Ссылка отправлена")
        return
    if player is None:
        _answer("Создайте профиль игрока на сайте.")
        return

    if callback_data == "menu_tournaments" or callback_data.startswith(
        "tournaments_page_"
    ):
        from django.db.models import Count

        from apps.tournaments.models import TournamentStatus

        offset_raw = 0
        if callback_data.startswith("tournaments_page_"):
            try:
                offset_raw = int(callback_data[len("tournaments_page_") :])
            except (TypeError, ValueError):
                offset_raw = 0
        tournaments_qs = (
            Tournament.objects.filter(
                status__in=(
                    TournamentStatus.UPCOMING,
                    TournamentStatus.ACTIVE,
                    TournamentStatus.GROUP_STAGE,
                    TournamentStatus.PLAYOFFS,
                )
            )
            .annotate(participants_count=Count("participants", distinct=True))
            .select_related("club")
            .order_by("start_date", "pk")
        )
        total = tournaments_qs.count()
        offset = normalize_offset(offset_raw, total)
        tournaments_page = list(
            tournaments_qs[offset : offset + PAGE_SIZE],
        )
        lines = [
            "🏆 <b>Турниры</b>",
            "<i>Предстоящие и идущие. Запись открывается на сайте.</i>",
            "",
        ]
        keyboard: list[list[dict[str, str]]] = []
        if not tournaments_page:
            lines.append("Сейчас нет турниров с открытой записью.")
        for number, tournament in enumerate(tournaments_page, start=offset + 1):
            lines.extend(
                _tournament_card_lines(
                    tournament,
                    number,
                    int(getattr(tournament, "participants_count", 0) or 0),
                )
            )
            lines.append("")
            detail_url = _absolute_site_url(
                base_url, reverse("tournament_detail", args=[tournament.slug])
            )
            row: list[dict[str, str]] = [
                {"text": f"🏆 {number}. Открыть", "url": detail_url}
            ]
            registration_url = _tournament_registration_url(tournament, base_url)
            if registration_url:
                row.append({"text": "✍️ Запись", "url": registration_url})
            keyboard.append(row)
        keyboard.extend(navigation_rows("tournaments_page_", offset, total))
        keyboard.append(
            [
                {
                    "text": "🗂 Все турниры на сайте",
                    "url": _absolute_site_url(base_url, reverse("tournament_list")),
                }
            ]
        )
        text = "\n".join(lines).rstrip()
        ok = _send_to_user_required(
            chat_id, text, reply_markup={"inline_keyboard": keyboard}
        )
        _answer("Турниры" if ok else "Ошибка отправки")
        return

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
            _send_to_user_required(chat_id, text, reply_markup=reply_markup)
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
        ok = _send_to_user_required(chat_id, text, reply_markup=reply_markup)
        _answer("Спарринг" if ok else "Ошибка")
        return

    if callback_data == "menu_my_matches":
        scheduled = (
            Match.objects.filter(
                match_participant_filter(player),
                status=Match.MatchStatus.SCHEDULED,
            )
            .exclude(result_proposals__status=Match.ProposalStatus.PENDING)
            .distinct()
            .select_related("tournament", "player1", "player2", "team1", "team2")
            .order_by("deadline", "scheduled_datetime")[:50]
        )
        scheduled_list = list(scheduled)

        tournaments: dict[int, _TournamentMatchGroup] = {}
        sparring_matches: list[Match] = []
        for m in scheduled_list:
            match_tournament = m.tournament
            if match_tournament is not None:
                group = tournaments.setdefault(
                    match_tournament.pk,
                    {
                        "tournament": match_tournament,
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
            "<i>Сначала выберите турнир или спарринг. История и счёт доступны кнопкой ниже.</i>",
            "",
        ]
        matches_keyboard: list[list[dict[str, str]]] = []

        if not tournaments and not sparring_matches:
            lines.append("Нет предстоящих матчей.")
            lines.append("Завершённые матчи доступны в истории ниже.")
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
                matches_keyboard.append(
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
                matches_keyboard.append(
                    [
                        {
                            "text": f"🎾 Спарринги ({len(sparring_matches)})",
                            "callback_data": "my_matches_sparring",
                        }
                    ]
                )

        text = "\n".join(lines)
        history_count = Match.objects.filter(
            match_participant_filter(player),
            status__in=(Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER),
        ).count()
        if history_count:
            matches_keyboard.append(
                [
                    {
                        "text": f"🕘 История матчей ({history_count})",
                        "callback_data": "my_matches_done_0",
                    }
                ]
            )
        reply_markup = (
            {"inline_keyboard": matches_keyboard} if matches_keyboard else None
        )

        ok = _send_to_user_required(chat_id, text, reply_markup=reply_markup)
        _answer("Матчи" if ok else "Сообщение не отправлено")
        if not ok:
            logger.warning("Ответ со списком матчей не отправлен: chat_id={}", chat_id)

    elif (
        callback_data.startswith("my_matches_tour_")
        or callback_data == "my_matches_sparring"
    ):
        # Показать матчи выбранного турнира или все спарринги
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
            match_participant_filter(player),
            status__in=(Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS),
        ).exclude(result_proposals__status=Match.ProposalStatus.PENDING)

        if selected_tournament_id is None:
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
        sub_keyboard: list[list[dict[str, str]]] = []

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
        ok = _send_to_user_required(chat_id, text, reply_markup=reply_markup)
        _answer("Матчи" if ok else "Сообщение не отправлено")
        return

    elif callback_data.startswith("my_matches_done_"):
        try:
            offset_raw = int(callback_data[len("my_matches_done_") :])
        except (ValueError, TypeError):
            _answer("Неверные данные")
            return
        history_qs = (
            Match.objects.filter(
                match_participant_filter(player),
                status__in=(
                    Match.MatchStatus.COMPLETED,
                    Match.MatchStatus.WALKOVER,
                ),
            )
            .select_related("tournament", "player1", "player2", "team1", "team2")
            .order_by("-pk")
        )
        total = history_qs.count()
        offset = normalize_offset(offset_raw, total)
        history_page = list(history_qs[offset : offset + PAGE_SIZE])
        lines = [
            "🕘 <b>История матчей</b>",
            "<i>Завершённые и технические матчи, новые сверху.</i>",
            "",
        ]
        if not history_page:
            lines.append("Завершённых матчей пока нет.")
            lines.append("Как только матч завершится, он появится здесь.")
        for number, history_match in enumerate(history_page, start=offset + 1):
            lines.extend(_history_match_lines(history_match, player, number))
        keyboard = navigation_rows("my_matches_done_", offset, total)
        keyboard.append(
            [
                {
                    "text": "🔗 Все матчи на сайте",
                    "url": _absolute_site_url(base_url, reverse("my_matches")),
                }
            ]
        )
        text = "\n".join(lines).rstrip()
        ok = _send_to_user_required(
            chat_id, text, reply_markup={"inline_keyboard": keyboard}
        )
        _answer("История матчей" if ok else "Сообщение не отправлено")
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

        ok = _send_to_user_required(chat_id, text, reply_markup=reply_markup)
        _answer("Тариф" if ok else "Сообщение не отправлено")
        return

    elif callback_data == "menu_my_profile":
        from apps.subscriptions.services import ensure_current_subscription
        from apps.tournaments.season_utils import get_current_season

        current_season = get_current_season()
        season_points = 0
        sp = getattr(player, "season_points", None)
        if (
            sp
            and sp.season_name == current_season.name
            and sp.season_year == current_season.year
        ):
            season_points = sp.current_season_points

        sub = ensure_current_subscription(user)
        sub_status = "❌ Нет подписки"
        if sub:
            tier_name = escape(sub.tier.get_name_display())
            sub_status = (
                f"✅ {tier_name}" if sub.is_valid() else f"❌ {tier_name} (истекла)"
            )

        lines = [
            "👤 <b>МОЙ ПРОФИЛЬ</b>",
            "",
            f"<b>{escape(str(player))}</b>",
            "",
            "📊 <b>ОСНОВНАЯ ИНФОРМАЦИЯ</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"📍 Населённый пункт: <b>{escape(player.city or '—')}</b>",
            f"🎯 Уровень: <b>{escape(player.get_skill_level_display())}</b>",
            f"📈 Сила: <b>{player.ntrp_level:.2f}</b>",
        ]
        if player.age is not None:
            lines.append(f"🎂 Возраст: <b>{player.age} лет</b>")
        if player.gender:
            lines.append(f"⚧️ Пол: <b>{escape(player.get_gender_display())}</b>")
        if player.forehand:
            lines.append(
                f"✋ Ведущая рука: <b>{escape(player.get_forehand_display())}</b>"
            )
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
                sub_status,
            ]
        )
        _send_to_user_required(chat_id, "\n".join(lines))
        _answer("Профиль")

    elif callback_data == "menu_my_subscription":
        from apps.subscriptions.services import ensure_current_subscription

        sub = ensure_current_subscription(user)

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
            end_str = sub.end_date.strftime("%d.%m.%Y") if sub.end_date else "—"

            # Статус подписки
            if is_cancelled and is_valid:
                status_emoji = "⚠️"
                status_text = f"Отменена (действует до {end_str})"
            elif is_valid:
                status_emoji = "✅"
                status_text = "Активна"
            else:
                status_emoji = "❌"
                status_text = "Истекла"

            # Дата окончания
            now = timezone.now()
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
            buttons: list[list[dict[str, str]]] = []
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

        ok = _send_to_user_required(chat_id, text, reply_markup=reply_markup)
        _answer("Подписка" if ok else "Ошибка отправки")
        if not ok:
            logger.warning("Ответ с подпиской не отправлен: chat_id={}", chat_id)

    elif callback_data in {"menu_public_chat", "menu_private_chat"}:
        # Старые кнопки menu_private_chat по-прежнему ведут только в открытый чат.
        reply_markup = None
        community_url = (
            getattr(settings, "TELEGRAM_PUBLIC_COMMUNITY_URL", "") or ""
        ).strip()
        if not community_url:
            text = (
                "💬 <b>Открытый чат игроков</b>\n\n"
                "Ссылка на чат временно недоступна. Попробуйте чуть позже."
            )
        else:
            text = "💬 <b>Открытый чат игроков</b>\n\nОбщение доступно без платной подписки."
            reply_markup = {
                "inline_keyboard": [[{"text": "➡️ Открытый чат", "url": community_url}]]
            }
        ok = _send_to_user_required(chat_id, text, reply_markup=reply_markup)
        _answer("Открытый чат" if ok else "Ошибка отправки")

    elif callback_data == "menu_subscriber_chat":
        reply_markup = None
        link = _get_link_by_chat_id(chat_id)
        allowed, reason = get_private_chat_access_status(
            link.user if link is not None and link.user_id == user.pk else None,
            lock=True,
        )
        if not bot.is_private_chat_configured():
            text = "🔒 <b>Чат подписчиков</b>\n\nЗакрытое сообщество сейчас недоступно."
        elif not allowed or link is None or link.user_id != user.pk:
            text = (
                "🔒 <b>Чат подписчиков</b>\n\n"
                "Нужна действующая подписка на тариф с доступом в закрытый чат.\n"
                f"{escape(reason)}"
            )
            pricing_url = (
                f"{(base_url or _get_site_base_url()).rstrip('/')}{reverse('pricing')}"
            )
            reply_markup = {
                "inline_keyboard": [[{"text": "💳 Тарифы", "url": pricing_url}]]
            }
        else:
            invite_link = bot.create_private_chat_invite_link(expire_seconds=1800)
            if invite_link:
                text = (
                    "🔒 <b>Чат подписчиков</b>\n\n"
                    "Подайте заявку на вступление по ссылке ниже. Ссылка действует 30 минут.\n"
                    "При вступлении бот ещё раз проверит вашу привязку и подписку."
                )
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "➡️ Подать заявку", "url": invite_link}]
                    ]
                }
            else:
                text = "⚠️ Не удалось создать приглашение. Попробуйте позже или обратитесь в поддержку."
        ok = _send_to_user_required(chat_id, text, reply_markup=reply_markup)
        _answer("Чат подписчиков" if ok else "Ошибка отправки")


def _handle_private_chat_join_request(join_request: dict[str, Any]) -> bool:
    """Проверить свежий тариф заявителя; пересланная ссылка сама по себе не даёт доступ."""
    chat = join_request.get("chat")
    sender = join_request.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return True
    if (
        not bot.is_private_chat_configured()
        or chat.get("id") != bot._get_private_chat_id()
    ):
        return True
    user_id = sender.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return True
    link = _get_link_by_chat_id(user_id)
    allowed = (
        link is not None and get_private_chat_access_status(link.user, lock=True)[0]
    )
    return bot.resolve_private_chat_join_request(user_id, approve=allowed)


@csrf_exempt
@require_http_methods(["POST"])
def user_bot_webhook(request: HttpRequest) -> JsonResponse:
    """Проверить секрет и структуру, затем обработать update однократно вместе с БД."""
    if not _webhook_secret_ok(request):
        return JsonResponse({"ok": False}, status=403)

    try:
        data = validate_user_update(json.loads(request.body or "{}"))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, RecursionError):
        return JsonResponse({"ok": True})
    if data is None:
        return JsonResponse({"ok": True})
    try:
        with transaction.atomic():
            _, created = TelegramUpdateReceipt.objects.get_or_create(
                bot=user_bot_update_namespace(bot._get_bot_token()),
                update_id=data["update_id"],
            )
            if not created:
                return JsonResponse({"ok": True})
            response = _dispatch_user_bot_update(data)
            if response.status_code >= 500:
                transaction.set_rollback(True)
            return response
    except Exception as exc:
        # Не сохраняем payload и не раскрываем токены/тексты из исключений.
        logger.error(
            "Telegram update не обработан: update_id={}, error={}",
            data["update_id"],
            type(exc).__name__,
        )
        return JsonResponse({"ok": False}, status=503)


def _dispatch_user_bot_update(data: dict[str, Any]) -> JsonResponse:
    """Диспетчер только проверенных обновлений; ошибка не подтверждает update Telegram."""
    base_url = _get_site_base_url()

    join_request = data.get("chat_join_request")
    if isinstance(join_request, dict):
        resolved = _handle_private_chat_join_request(join_request)
        return JsonResponse({"ok": resolved}, status=200 if resolved else 503)

    # Callback от inline-кнопок: подтверждение/отклонение результата или просто снять «часики»
    callback_query = data.get("callback_query") or {}
    if callback_query:
        if not isinstance(callback_query, dict):
            return JsonResponse({"ok": True})
        callback_message = callback_query.get("message") or {}
        callback_chat = callback_message.get("chat") or {}
        sender = callback_query.get("from") or {}
        if callback_chat.get("type") != "private" or callback_chat.get(
            "id"
        ) != sender.get("id"):
            return JsonResponse({"ok": True})
        # Telegram API присылает данные кнопки в поле "data", а не "callback_data"
        if "data" in callback_query:
            callback_query.setdefault("callback_data", callback_query["data"])
        logger.debug("Получен Telegram callback: update_id={}", data["update_id"])
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
        if cq_id and not handled:
            _answer_callback(cq_id)
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
            connected, error = consume_binding_token(token, chat_id)
            if connected:
                welcome = (
                    "✅ <b>Бот подключён</b>\n\n"
                    "Теперь вы будете получать уведомления о регистрациях на турниры, "
                    "о матчах и дедлайнах. Выберите действие кнопками ниже:"
                )
                _send_message_required(
                    chat_id, welcome, reply_markup=_reply_menu_keyboard()
                )
            else:
                _send_message_required(
                    chat_id,
                    error,
                )
        else:
            link = _get_link_by_chat_id(chat_id)
            if link:
                if not link.user_bot_chat_id:
                    link.user_bot_chat_id = chat_id
                    link.save(update_fields=["user_bot_chat_id"])
                _send_message_required(
                    chat_id,
                    "Снова привет! Выберите действие:",
                    reply_markup=_reply_menu_keyboard(),
                )
            else:
                _send_message_required(
                    chat_id,
                    "Чтобы получать уведомления о турнирах и матчах, подключите бота с сайта:\n"
                    "Профиль → блок «Telegram-бот» → «Подключить Telegram-бот».",
                )
        return JsonResponse({"ok": True})

    # Команды бота: быстрый доступ к разделам и справка.
    command = text.split(maxsplit=1)[0].lower() if text.startswith("/") else ""
    command_actions = {
        "/matches": "menu_my_matches",
        "/tournaments": "menu_tournaments",
        "/sparring": "menu_sparring",
        "/profile": "menu_my_profile",
        "/subscriptions": "menu_my_subscription",
        "/help": "menu_help",
    }
    if command == "/cancel":
        _send_message_required(
            chat_id,
            "Нет активного ввода результата.",
            reply_markup=_reply_menu_keyboard(),
        )
        return JsonResponse({"ok": True})
    if command == "/menu":
        link = _get_link_by_chat_id(chat_id)
        if link:
            _send_message_required(
                chat_id, "Выберите действие:", reply_markup=_reply_menu_keyboard()
            )
        else:
            _send_message_required(
                chat_id,
                "Подключите бота с сайта (профиль → Telegram), чтобы пользоваться меню.",
            )
        return JsonResponse({"ok": True})
    if command in command_actions:
        if command == "/help":
            _send_message_required(
                chat_id,
                "❓ <b>Помощь</b>\n\n"
                "Команды: /menu, /matches, /tournaments, /sparring, /profile, "
                "/subscriptions, /help.\n"
                "Во время ввода счёта отправьте /cancel.",
                reply_markup=_reply_menu_keyboard(),
            )
            return JsonResponse({"ok": True})
        link = _get_link_by_chat_id(chat_id)
        if not link:
            _send_message_required(
                chat_id,
                "Сначала подключите бота с сайта (профиль → Telegram).",
                reply_markup=_reply_menu_keyboard(),
            )
            return JsonResponse({"ok": True})
        user = link.user
        player = getattr(user, "player", None)
        if not player:
            player, _ = Player.objects.get_or_create(user=user)
        _handle_menu_callback_action(
            chat_id,
            None,
            command_actions[command],
            user,
            player,
            base_url=base_url,
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
                    _clear_result_entry_on_commit(cache_key, match_pk)
                    _send_message_required(chat_id, "Отменено.")
                    return JsonResponse({"ok": True})
                sets_list, err = _parse_score_input(text)
                if err or sets_list is None:
                    _send_message_required(
                        chat_id, f"❌ {err or 'Не удалось распознать счёт.'}"
                    )
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
                    _clear_result_entry_on_commit(cache_key, match_pk)
                    _send_message_required(chat_id, "Матч не найден или уже завершён.")
                    return JsonResponse({"ok": True})
                participants = get_match_participants(match)
                if player not in participants:
                    _clear_result_entry_on_commit(cache_key, match_pk)
                    _send_message_required(chat_id, "Вы не участвуете в этом матче.")
                    return JsonResponse({"ok": True})
                if match.result_proposals.filter(
                    status=Match.ProposalStatus.PENDING
                ).exists():
                    _clear_result_entry_on_commit(cache_key, match_pk)
                    _send_message_required(
                        chat_id,
                        "⏳ По этому матчу уже отправлен результат и он ожидает подтверждения соперником. "
                        "Если соперник отклонит результат и не отправит свой — вы сможете отправить результат снова.",
                    )
                    return JsonResponse({"ok": True})
                if all(games1 == 0 and games2 == 0 for games1, games2 in sets_list):
                    _clear_result_entry_on_commit(cache_key, match_pk)
                    _send_message_required(
                        chat_id,
                        "❌ Счёт 0:0 не сохраняется как результат игры.\n\n"
                        "Если матч не состоялся — отметьте неявку. "
                        "Неявившейся стороне спишется <b>40 очков</b> рейтинга.",
                        reply_markup=_walkover_choice_keyboard(match.pk),
                    )
                    return JsonResponse({"ok": True})
                p1_s1 = p1_s2 = p1_s3 = p2_s1 = p2_s2 = p2_s3 = None
                for i, (a, b) in enumerate(sets_list):
                    if i == 0:
                        p1_s1, p2_s1 = a, b
                    elif i == 1:
                        p1_s2, p2_s2 = a, b
                    else:
                        p1_s3, p2_s3 = a, b
                try:
                    submit_match_result(
                        match.pk,
                        player,
                        result=Match.ResultChoice.WIN,
                        player1_set1=p1_s1,
                        player2_set1=p2_s1,
                        player1_set2=p1_s2,
                        player2_set2=p2_s2,
                        player1_set3=p1_s3,
                        player2_set3=p2_s3,
                        score_from_proposer=True,
                    )
                except ProposalValidationError as exc:
                    _send_message_required(chat_id, f"❌ {escape(str(exc))}")
                    return JsonResponse({"ok": True})
                _clear_result_entry_on_commit(cache_key, match.pk)
                _send_message_required(
                    chat_id,
                    "✅ Результат сохранён. Матч завершён.",
                )
                return JsonResponse({"ok": True})
        _clear_result_entry_on_commit(cache_key, match_pk)

    # Нажатие кнопок подменю «Спарринг»
    if text in REPLY_SPARRING_BUTTONS:
        if text == REPLY_BTN_BACK_TO_MENU:
            _send_message_required(
                chat_id, "Главное меню:", reply_markup=_reply_menu_keyboard()
            )
            return JsonResponse({"ok": True})
        link = _get_link_by_chat_id(chat_id)
        if not link:
            _send_message_required(
                chat_id, "Сначала подключите бота с сайта (профиль → Telegram)."
            )
            return JsonResponse({"ok": True})
        user = link.user
        player = getattr(user, "player", None)
        if not player:
            player, _ = Player.objects.get_or_create(user=user)
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
            _send_message_required(
                chat_id,
                f'🌐 <b>Перейти на сайт</b>\n\n<a href="{site_url}">{site_url}</a>',
                reply_markup=_reply_menu_keyboard(),
            )
            return JsonResponse({"ok": True})
        if text == REPLY_BTN_SPARRING:
            link = _get_link_by_chat_id(chat_id)
            if not link:
                _send_message_required(
                    chat_id, "Сначала подключите бота с сайта (профиль → Telegram)."
                )
                return JsonResponse({"ok": True})
            from apps.sparring.utils import user_has_sparring_access

            if not user_has_sparring_access(link.user):
                _send_message_required(
                    chat_id,
                    "❌ Оформите подписку для доступа к разделу спаррингов.",
                    reply_markup=_reply_menu_keyboard(),
                )
                return JsonResponse({"ok": True})
            _send_message_required(
                chat_id,
                "🎾 <b>Спарринг</b>\n\nВыберите раздел:",
                reply_markup=_reply_sparring_keyboard(),
            )
            return JsonResponse({"ok": True})
        link = _get_link_by_chat_id(chat_id)
        if not link:
            _send_message_required(
                chat_id, "Сначала подключите бота с сайта (профиль → Telegram)."
            )
        else:
            user = link.user
            player = getattr(user, "player", None)
            if not player:
                player, _ = Player.objects.get_or_create(user=user)
            reply_to_callback = {
                REPLY_BTN_MY_PROFILE: "menu_my_profile",
                REPLY_BTN_MY_MATCHES: "menu_my_matches",
                REPLY_BTN_TOURNAMENTS: "menu_tournaments",
                REPLY_BTN_HELP: "menu_help",
                REPLY_BTN_MY_SUBSCRIPTIONS: "menu_my_subscription",
                REPLY_BTN_PRIVATE_CHAT: "menu_private_chat",
                REPLY_BTN_PUBLIC_CHAT: "menu_public_chat",
                REPLY_BTN_SUBSCRIBER_CHAT: "menu_subscriber_chat",
            }[text]
            _handle_menu_callback_action(
                chat_id, None, reply_to_callback, user, player, base_url=base_url
            )
        return JsonResponse({"ok": True})

    # Любое другое сообщение — показываем меню
    link = _get_link_by_chat_id(chat_id)
    if link:
        _send_message_required(
            chat_id,
            "Выберите действие:",
            reply_markup=_reply_menu_keyboard(),
        )
    else:
        _send_message_required(
            chat_id,
            "Подключите бота с сайта (профиль → Telegram), чтобы пользоваться меню.",
        )

    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def connect_redirect(request: HttpRequest) -> HttpResponse:
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
            user=cast(User, request.user),
            consent_version=TELEGRAM_TRANSFER_CONSENT_VERSION,
            ip_address=_get_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:1000],
        )
    except Exception as exc:
        logger.warning(
            "Согласие на передачу данных в Telegram не сохранено: user_id={}, error={}",
            request.user.pk,
            type(exc).__name__,
        )

    if not bot.is_configured():
        if not is_telegram_api_enabled():
            messages.warning(
                request,
                "Подключение Telegram сейчас недоступно на стороне сайта. "
                "Уведомления приходят по email и в личном кабинете.",
            )
        else:
            messages.error(
                request,
                "Telegram-бот временно недоступен. Проверьте TELEGRAM_USER_BOT_TOKEN в .env и перезапустите сервер.",
            )
        return redirect(_get_redirect_url_after_bot_settings_change(request))

    link, _ = UserTelegramLink.objects.get_or_create(
        user=cast(User, request.user),
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
@transaction.atomic
def disconnect_user_bot(request: HttpRequest) -> HttpResponse:
    """
    Отключить пользовательского Telegram-бота в ЛК:
    - очищает chat_id пользовательского бота
    - сбрасывает одноразовый токен привязки
    """
    link = (
        UserTelegramLink.objects.select_for_update()
        .filter(user=cast(User, request.user))
        .first()
    )
    if not link:
        messages.info(request, "Telegram-бот уже отключён.")
        return redirect(_get_redirect_url_after_bot_settings_change(request))

    link.user_bot_chat_id = None
    link.binding_token = None
    link.token_created_at = None
    link.save(
        update_fields=[
            "user_bot_chat_id",
            "binding_token",
            "token_created_at",
            "updated_at",
        ]
    )

    messages.success(
        request,
        "Telegram-бот отключён. Уведомления в Telegram больше не будут приходить.",
    )
    return redirect(_get_redirect_url_after_bot_settings_change(request))
