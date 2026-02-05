"""
Core views - main pages.
"""

import json
import logging
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_safe
from django.utils.html import linebreaks

from apps.content.models import News, RulesSection
from apps.tournaments.models import Match, Tournament, TournamentDuration, TournamentGender, TournamentStatus
from apps.users.models import Player, SkillLevel

from .forms import FeedbackForm
from .models import Feedback, FeedbackReply, SupportMessage, UserTelegramLink
from .telegram_notify import send_feedback_to_telegram
from . import telegram_support as tg_support

logger = logging.getLogger(__name__)


def home(request):
    """Home page view. Формирование сеток по дедлайну выполняется по cron (generate_brackets_past_deadlines)."""
    tournaments = Tournament.objects.filter(
        status__in=[TournamentStatus.UPCOMING, TournamentStatus.ACTIVE]
    ).prefetch_related("participants__user", "allowed_categories")

    upcoming_tournaments = (
        Tournament.objects.filter(status=TournamentStatus.UPCOMING)
        .prefetch_related("allowed_categories")
        .order_by("start_date")[:6]
    )

    city = request.GET.get('city', '')
    category = request.GET.get('category', '')
    gender = request.GET.get('gender', '')
    duration = request.GET.get('duration', '')

    if city:
        tournaments = tournaments.filter(city__icontains=city)
    if category:
        tournaments = tournaments.filter(allowed_categories__category=category).distinct()
    if gender:
        tournaments = tournaments.filter(gender=gender)
    if duration:
        tournaments = tournaments.filter(duration=duration)

    tournaments = tournaments.order_by('start_date')

    context = {
        'filtered_tournaments': tournaments,
        'upcoming_tournaments': upcoming_tournaments,
        'top_players': Player.objects.filter(is_verified=True)
        .select_related('user', 'user__subscription', 'user__subscription__tier')
        .order_by('-total_points')[:10],
        'latest_news': News.objects.filter(is_published=True)[:4],
        'current_filters': {
            'city': city,
            'category': category,
            'gender': gender,
            'duration': duration,
        },
        'category_choices': SkillLevel.choices,
        'gender_choices': TournamentGender.choices,
        'duration_choices': TournamentDuration.choices,
    }
    return render(request, 'core/home.html', context)


def rating(request):
    """Player rating page."""
    city = request.GET.get('city', '')
    skill_level = request.GET.get('skill_level', '') or request.GET.get('category', '')
    search = request.GET.get('q', '')

    players = Player.objects.select_related(
        'user', 'user__subscription', 'user__subscription__tier'
    )

    if city:
        players = players.filter(city__icontains=city)
    if skill_level:
        players = players.filter(skill_level=skill_level)
    if search:
        players = players.filter(
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search)
        )

    players = players.order_by('-total_points')

    context = {
        'players': players,
        'current_city': city,
        'current_skill_level': skill_level,
        'search_query': search,
        'skill_level_choices': SkillLevel.choices,
    }
    return render(request, 'core/rating.html', context)


def results(request):
    """Match results page."""
    matches = Match.objects.filter(
        status=Match.MatchStatus.COMPLETED
    ).select_related(
        'player1__user', 'player2__user', 'winner__user', 'tournament'
    ).order_by('-completed_datetime')[:50]
    return render(request, 'core/results.html', {'matches': matches})


def legends(request):
    """Hall of fame page."""
    legends = Player.objects.filter(is_legend=True).select_related('user')
    return render(request, 'core/legends.html', {'legends': legends})


def _is_html_content(text: str) -> bool:
    """Проверяет, похож ли текст на HTML (есть теги), чтобы не применять linebreaks."""
    if not text or "<" not in text:
        return False
    return bool(re.search(r"<\s*[a-zA-Z]", text))


def rules(request):
    """Rules page: tournament formats (FAN, etc.) with detailed descriptions. Content is editable via admin (RulesSection)."""
    rules_content = {}
    for s in RulesSection.objects.all():
        body = s.body or ""
        if body and not _is_html_content(body):
            body = linebreaks(body)
        rules_content[s.slug] = body
    return render(request, "core/rules.html", {"rules_content": rules_content})


# ---------------------------------------------------------------------------
# Обратная связь: новая система через Telegram (SupportMessage + UserTelegramLink)
# ---------------------------------------------------------------------------


def _create_support_message_and_send_to_admin(request, subject: str, message: str):
    """
    Создать SupportMessage, отправить админу в Telegram, сохранить admin_telegram_message_id.
    Возвращает (support_message, telegram_binding_url или None).
    """
    support_msg = SupportMessage.objects.create(
        user=request.user,
        subject=(subject or "")[:200],
        text=message,
        is_from_admin=False,
    )
    user_display = request.user.get_full_name() or request.user.email or "—"
    user_email = request.user.email or ""
    text_for_admin = tg_support.format_support_message_to_admin(
        support_message_id=support_msg.pk,
        user_display=user_display,
        user_email=user_email,
        subject=subject,
        text=message,
        source="сайт",
    )
    msg_id, ok = tg_support.send_to_admin(text_for_admin)
    if ok and msg_id is not None:
        support_msg.admin_telegram_message_id = msg_id
        support_msg.admin_telegram_text = text_for_admin
        support_msg.save(update_fields=["admin_telegram_message_id", "admin_telegram_text"])

    binding_url = None
    if tg_support.is_telegram_configured():
        link, _ = UserTelegramLink.objects.get_or_create(
            user=request.user,
            defaults={"telegram_chat_id": None},
        )
        if link.telegram_chat_id is None:
            token = link.get_or_create_binding_token()
            bot_username = tg_support.get_bot_username()
            if bot_username:
                binding_url = f"https://t.me/{bot_username}?start={token}"

    return support_msg, binding_url


@login_required
@require_http_methods(["GET", "POST"])
def support_feedback(request):
    """
    Форма обратной связи. POST: сохранить в БД, отправить админу в Telegram,
    показать «Ваше сообщение принято. Ответ придёт в Telegram» и ссылку на привязку при необходимости.
    """
    if request.method == "GET":
        form = FeedbackForm()
        return render(request, "core/support_feedback.html", {"form": form})

    form = FeedbackForm(request.POST)
    if not form.is_valid():
        return render(request, "core/support_feedback.html", {"form": form})

    subject = (form.cleaned_data.get("subject") or "").strip()
    message = (form.cleaned_data.get("message") or "").strip()
    _, binding_url = _create_support_message_and_send_to_admin(request, subject, message)

    return render(
        request,
        "core/support_feedback_success.html",
        {"telegram_binding_url": binding_url},
    )


@login_required
@require_http_methods(["POST"])
def support_feedback_submit(request):
    """
    API для виджета (JSON): создать SupportMessage, отправить админу.
    Возвращает success и при необходимости telegram_binding_url.
    """
    try:
        if request.content_type and "application/json" in request.content_type:
            data = json.loads(request.body or "{}")
        else:
            data = request.POST
        message = (data.get("message") or "").strip()
        subject = (data.get("subject") or "").strip()
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"success": False, "error": "Неверный формат запроса"}, status=400)

    if not message:
        return JsonResponse({"success": False, "error": "Введите сообщение."}, status=400)

    _, binding_url = _create_support_message_and_send_to_admin(request, subject, message)

    payload = {"success": True}
    if binding_url:
        payload["telegram_binding_url"] = binding_url
        payload["message"] = "Ваше сообщение принято. Ответ придёт в Telegram. Привяжите аккаунт по ссылке, чтобы получать ответы."
    else:
        payload["message"] = "Ваше сообщение принято. Ответ придёт в Telegram."
    return JsonResponse(payload)


# ---------------------------------------------------------------------------
# Telegram Webhook: /start, сообщения пользователя, ответы админа
# ---------------------------------------------------------------------------

def _support_webhook_secret_ok(request) -> bool:
    """Проверка секрета webhook бота поддержки (X-Telegram-Bot-Api-Secret-Token)."""
    secret = getattr(settings, "TELEGRAM_SUPPORT_WEBHOOK_SECRET", None) or ""
    if not secret:
        return True
    return request.headers.get("X-Telegram-Bot-Api-Secret-Token") == secret


@csrf_exempt
@require_http_methods(["POST"])
def telegram_support_webhook(request):
    """
    Webhook бота поддержки (TELEGRAM_SUPPORT_BOT_TOKEN).
    - /start с токеном: привязка telegram_chat_id к пользователю.
    - Сообщение от пользователя (личный чат): сохранить, переслать админу.
    - Ответ админа (Reply на сообщение): отправить пользователю, пометить «Ответ отправлен».
    - Сообщение админа без Reply: отправить подсказку «выберите сообщение (Reply)».
    """
    if not _support_webhook_secret_ok(request):
        return JsonResponse({"ok": False}, status=403)

    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": True})

    admin_chat_id = tg_support.get_admin_chat_id_value()
    if not admin_chat_id:
        return JsonResponse({"ok": True})

    message = data.get("message") or {}
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    reply_to = message.get("reply_to_message") or {}

    # ----- Ответ администратора (reply на наше сообщение админу) -----
    if reply_to and chat_id == admin_chat_id and text:
        original_message_id = reply_to.get("message_id")
        if not original_message_id:
            return JsonResponse({"ok": True})

        support_msg = SupportMessage.objects.filter(
            admin_telegram_message_id=original_message_id,
        ).select_related("user").first()
        if not support_msg:
            logger.debug("Webhook: no SupportMessage for message_id=%s", original_message_id)
            return JsonResponse({"ok": True})

        user = support_msg.user
        link = getattr(user, "telegram_link", None)
        if link and link.telegram_chat_id:
            safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            tg_support.send_to_user(link.telegram_chat_id, f"📩 <b>Ответ поддержки:</b>\n\n{safe_text}")
        SupportMessage.objects.create(
            user=user,
            text=text,
            is_from_admin=True,
        )
        if support_msg.admin_telegram_text and support_msg.admin_telegram_message_id:
            new_text = support_msg.admin_telegram_text + "\n\n✅ Ответ отправлен"
            tg_support.edit_message(admin_chat_id, original_message_id, new_text)
        return JsonResponse({"ok": True})

    # Админ написал сообщение без Reply — подсказка
    if chat_id == admin_chat_id and text and not reply_to:
        tg_support.send_to_admin(
            "⚠️ Чтобы ответить пользователю, выберите его сообщение (Reply) и введите ответ."
        )
        return JsonResponse({"ok": True})

    # ----- /start: привязка по токену или сообщение «уже привязан» -----
    if text.startswith("/start") and message.get("chat", {}).get("type") == "private":
        try:
            chat_id_int = int(chat_id)
        except (ValueError, TypeError):
            return JsonResponse({"ok": True})

        token = ""
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            token = (parts[1] or "").strip()

        if token:
            link = UserTelegramLink.objects.filter(binding_token=token).first()
            if link:
                link.telegram_chat_id = chat_id_int
                link.binding_token = ""
                link.token_created_at = None
                link.save(update_fields=["telegram_chat_id", "binding_token", "token_created_at"])
                tg_support.send_message(chat_id_int, "✅ Ваш аккаунт успешно привязан.")
            else:
                tg_support.send_message(chat_id_int, "Токен не найден или устарел. Отправьте форму на сайте заново и перейдите по новой ссылке.")
        else:
            # /start без токена — проверяем, привязан ли уже этот чат
            existing = UserTelegramLink.objects.filter(telegram_chat_id=chat_id_int).first()
            if existing:
                tg_support.send_message(chat_id_int, "✅ Ваш аккаунт уже привязан.")
            else:
                tg_support.send_message(
                    chat_id_int,
                    "Отправьте форму обратной связи на сайте и перейдите по ссылке из уведомления, чтобы привязать аккаунт и получать ответы здесь.",
                )
        return JsonResponse({"ok": True})

    # ----- Обычное сообщение от пользователя (личный чат, уже привязан) -----
    if message.get("chat", {}).get("type") == "private" and text:
        try:
            chat_id_int = int(chat_id)
        except (ValueError, TypeError):
            return JsonResponse({"ok": True})

        link = UserTelegramLink.objects.filter(telegram_chat_id=chat_id_int).first()
        if not link:
            tg_support.send_message(
                chat_id_int,
                "Сначала отправьте форму на сайте и перейдите по ссылке из уведомления, чтобы привязать аккаунт.",
            )
            return JsonResponse({"ok": True})

        support_msg = SupportMessage.objects.create(
            user=link.user,
            text=text,
            is_from_admin=False,
        )
        user_display = link.user.get_full_name() or link.user.email or "—"
        user_email = link.user.email or ""
        text_for_admin = tg_support.format_support_message_to_admin(
            support_message_id=support_msg.pk,
            user_display=user_display,
            user_email=user_email,
            subject="",
            text=text,
            source="Telegram",
        )
        msg_id, ok = tg_support.send_to_admin(text_for_admin)
        if ok and msg_id is not None:
            support_msg.admin_telegram_message_id = msg_id
            support_msg.admin_telegram_text = text_for_admin
            support_msg.save(update_fields=["admin_telegram_message_id", "admin_telegram_text"])

        return JsonResponse({"ok": True})

    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Старые эндпоинты (виджет на сайте — можно переключить на support_feedback_submit)
# ---------------------------------------------------------------------------

@require_http_methods(["GET"])
def feedback(request):
    """Редирект на форму обратной связи (новая система)."""
    return redirect("support_feedback")


@login_required
@require_http_methods(["POST"])
def feedback_submit(request):
    """
    API виджета: использует новую систему SupportMessage и возвращает telegram_binding_url при необходимости.
    """
    return support_feedback_submit(request)


@login_required
@require_safe
def feedback_threads(request):
    """API: список обращений пользователя (SupportMessage) для виджета."""
    threads = []
    messages = (
        SupportMessage.objects.filter(user=request.user)
        .order_by("created_at")[:50]
    )
    current_thread = []
    for m in messages:
        current_thread.append({
            "id": m.pk,
            "text": m.text,
            "is_from_admin": m.is_from_admin,
            "created_at": m.created_at.isoformat(),
        })
    if current_thread:
        threads.append({"messages": current_thread})
    return JsonResponse({"threads": threads})
