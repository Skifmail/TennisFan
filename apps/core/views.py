"""
Core views - main pages.
"""

import json
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.html import linebreaks
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_safe

from apps.content.models import News, RulesSection
from apps.courts.models import Court
from apps.tournaments.models import (
    Match,
    SeasonArchive,
    Tournament,
    TournamentDuration,
    TournamentGender,
    TournamentStatus,
)
from apps.training.models import Coach
from apps.users.models import Player, SkillLevel
from apps.users.rating_utils import rating_to_ntrp_level

from . import telegram_support as tg_support
from .forms import FeedbackForm
from .models import SupportMessage, UserTelegramLink

logger = logging.getLogger(__name__)


def _build_recent_matches(limit: int = 10, days: int = 5):
    """Последние завершённые матчи за N дней для виджета на главной."""
    since = timezone.now() - timedelta(days=days)
    matches = (
        Match.objects.filter(
            status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER],
            completed_datetime__gte=since,
        )
        .select_related(
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team2__player1__user",
        )
        .order_by("-completed_datetime", "-pk")[:limit]
    )
    result = []
    for m in matches:
        p1 = m.get_side1_player()
        p2 = m.get_side2_player()
        if not p1 or not p2:
            continue
        if getattr(p1, "is_bye", False) or getattr(p2, "is_bye", False):
            continue
        delta1 = m.rating_delta_player1 or 0.0
        delta2 = m.rating_delta_player2 or 0.0
        r1_after = float(p1.total_points)
        r2_after = float(p2.total_points)
        r1_before = r1_after - delta1
        r2_before = r2_after - delta2
        n1_after = float(p1.ntrp_level or 0)
        n2_after = float(p2.ntrp_level or 0)
        n1_before = float(rating_to_ntrp_level(r1_before))
        n2_before = float(rating_to_ntrp_level(r2_before))
        n1_delta = round(n1_after - n1_before, 1)
        n2_delta = round(n2_after - n2_before, 1)
        avatar1 = p1.avatar.url if (hasattr(p1, "avatar") and p1.avatar) else None
        avatar2 = p2.avatar.url if (hasattr(p2, "avatar") and p2.avatar) else None
        result.append(
            {
                "id": m.pk,
                "player1": m.get_player1_display(),
                "player2": m.get_player2_display(),
                "p1_avatar": avatar1,
                "p2_avatar": avatar2,
                "score": m.score_display,
                "score_column": (m.score_display or "—").replace(" ", "\n"),
                "p1_ntrp": n1_after,
                "p1_ntrp_delta": n1_delta,
                "p1_rating": r1_after,
                "p1_rating_delta": round(delta1, 1),
                "p2_ntrp": n2_after,
                "p2_ntrp_delta": n2_delta,
                "p2_rating": r2_after,
                "p2_rating_delta": round(delta2, 1),
                "date": (
                    m.completed_datetime.strftime("%d.%m.%Y")
                    if m.completed_datetime
                    else ""
                ),
                "match_url": f"/tournaments/match/{m.pk}/",
            }
        )
    return result


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

    city = request.GET.get("city", "")
    category = request.GET.get("category", "")
    gender = request.GET.get("gender", "")
    duration = request.GET.get("duration", "")

    if city:
        tournaments = tournaments.filter(city__icontains=city)
    if category:
        tournaments = tournaments.filter(
            allowed_categories__category=category
        ).distinct()
    if gender:
        tournaments = tournaments.filter(gender=gender)
    if duration:
        tournaments = tournaments.filter(duration=duration)

    tournaments = tournaments.order_by("start_date")

    # Получаем топ игроков по сезонным очкам
    from django.db.models import Case, F, IntegerField, Value, When

    from apps.tournaments.season_utils import get_current_season

    current_season = get_current_season()
    top_players = (
        Player.objects.filter(is_verified=True, is_bye=False)
        .select_related(
            "user", "user__subscription", "user__subscription__tier", "season_points"
        )
        .annotate(
            season_pts=Case(
                When(
                    season_points__season_name=current_season.name,
                    season_points__season_year=current_season.year,
                    then=F("season_points__current_season_points"),
                ),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("-season_pts", "-total_points")[:10]
    )

    recent_matches = _build_recent_matches(limit=10, days=5)

    # Метрики для Hero-блока
    def format_number(num):
        """Форматирует число с пробелами для тысяч."""
        return f"{num:,}".replace(",", " ")

    hero_stats = {
        "players_count": format_number(
            Player.objects.filter(is_bye=False, is_verified=True).count()
        ),
        "tournaments_count": format_number(
            Tournament.objects.filter(
                status__in=[TournamentStatus.UPCOMING, TournamentStatus.ACTIVE]
            ).count()
        ),
        "matches_count": format_number(
            Match.objects.filter(
                status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
            ).count()
        ),
        "courts_count": format_number(Court.objects.count()),
        "cities_count": format_number(
            Player.objects.exclude(city="").values("city").distinct().count()
        ),
        "coaches_count": format_number(Coach.objects.count()),
    }

    context = {
        "filtered_tournaments": tournaments,
        "upcoming_tournaments": upcoming_tournaments,
        "top_players": top_players,
        "recent_matches": recent_matches,
        "recent_matches_json": json.dumps(recent_matches, default=str),
        "latest_news": News.objects.filter(is_published=True)[:4],
        "hero_stats": hero_stats,
        "current_filters": {
            "city": city,
            "category": category,
            "gender": gender,
            "duration": duration,
        },
        "category_choices": SkillLevel.choices,
        "gender_choices": TournamentGender.choices,
        "duration_choices": TournamentDuration.choices,
    }
    return render(request, "core/home.html", context)


@require_safe
def api_recent_matches(request):
    """API: последние матчи за 5 дней для live-тикера."""
    matches = _build_recent_matches(limit=10, days=5)
    return JsonResponse({"matches": matches})


def rating(request):
    """Player rating page - сортировка по сезонным очкам."""
    from apps.tournaments.season_utils import get_current_season

    city = request.GET.get("city", "")
    skill_level = request.GET.get("skill_level", "") or request.GET.get("category", "")
    search = request.GET.get("q", "")

    current_season = get_current_season()

    # Получаем игроков с сезонными очками (исключаем служебного игрока "Свободный круг")
    players = (
        Player.objects.filter(is_bye=False)
        .select_related(
            "user", "user__subscription", "user__subscription__tier", "season_points"
        )
        .prefetch_related("season_points")
    )

    if city:
        players = players.filter(city__icontains=city)
    if skill_level:
        players = players.filter(skill_level=skill_level)
    if search:
        players = players.filter(
            Q(user__first_name__icontains=search) | Q(user__last_name__icontains=search)
        )

    # Сортируем по сезонным очкам текущего сезона
    # Используем аннотацию для получения сезонных очков
    from django.db.models import Case, F, IntegerField, Value, When

    players = players.annotate(
        season_pts=Case(
            When(
                season_points__season_name=current_season.name,
                season_points__season_year=current_season.year,
                then=F("season_points__current_season_points"),
            ),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by("-season_pts", "-total_points")

    context = {
        "players": players,
        "current_city": city,
        "current_skill_level": skill_level,
        "search_query": search,
        "skill_level_choices": SkillLevel.choices,
        "current_season_display": f"{current_season.name} {current_season.year}",
    }
    return render(request, "core/rating.html", context)


def hall_of_fame(request):
    """Зал Славы - архив результатов сезонов."""
    season_filter = request.GET.get("season", "")

    # Получаем все уникальные сезоны из архива
    seasons = (
        SeasonArchive.objects.values("season_name", "season_year")
        .distinct()
        .order_by("-season_year", "-season_name")
    )

    # Если выбран конкретный сезон, фильтруем
    if season_filter:
        parts = season_filter.split("_")
        if len(parts) == 2:
            try:
                season_name = "Зима" if parts[0] == "winter" else "Лето"
                season_year = int(parts[1])
                archives = (
                    SeasonArchive.objects.filter(
                        season_name=season_name,
                        season_year=season_year,
                    )
                    .select_related("player", "player__user")
                    .order_by("final_rank", "-final_points")
                )
            except (ValueError, KeyError):
                archives = SeasonArchive.objects.none()
        else:
            archives = SeasonArchive.objects.none()
    else:
        # Показываем последний сезон по умолчанию
        if seasons:
            last_season = seasons[0]
            archives = (
                SeasonArchive.objects.filter(
                    season_name=last_season["season_name"],
                    season_year=last_season["season_year"],
                )
                .select_related("player", "player__user")
                .order_by("final_rank", "-final_points")
            )
        else:
            archives = SeasonArchive.objects.none()

    # Формируем список сезонов для выпадающего списка
    season_list = []
    for s in seasons:
        season_key = (
            f"{'winter' if s['season_name'] == 'Зима' else 'summer'}_{s['season_year']}"
        )
        season_display = f"{s['season_name']} {s['season_year']}"
        season_list.append(
            {
                "key": season_key,
                "display": season_display,
                "name": s["season_name"],
                "year": s["season_year"],
            }
        )

    context = {
        "archives": archives,
        "seasons": season_list,
        "current_season": season_filter,
    }
    return render(request, "core/hall_of_fame.html", context)


def results(request):
    """Match results page."""
    matches = (
        Match.objects.filter(status=Match.MatchStatus.COMPLETED)
        .select_related("player1__user", "player2__user", "winner__user", "tournament")
        .order_by("-completed_datetime")[:50]
    )
    return render(request, "core/results.html", {"matches": matches})


def _is_html_content(text: str) -> bool:
    """Проверяет, похож ли текст на HTML (есть теги), чтобы не применять linebreaks."""
    if not text or "<" not in text:
        return False
    return bool(re.search(r"<\s*[a-zA-Z]", text))


def rules(request):
    """Rules page: tournament formats (одноэтапная сетка, олимпийская, круговой) with detailed descriptions. Content is editable via admin (RulesSection)."""
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


def _create_support_message_and_send_to_admin(
    request,
    subject: str,
    message: str,
    guest_name: str | None = None,
    guest_contact: str | None = None,
    guest_telegram_username: str | None = None,
):
    """
    Создать SupportMessage, отправить админу в Telegram, сохранить admin_telegram_message_id.
    Возвращает (support_message, telegram_binding_url или None).
    Поддерживает как зарегистрированных пользователей, так и гостей.
    """
    if request.user.is_authenticated:
        # Уникальный токен для каждого сообщения (поле unique=True); для привязки бота используется UserTelegramLink
        support_msg = SupportMessage.objects.create(
            user=request.user,
            guest_binding_token=secrets.token_urlsafe(32),
            subject=(subject or "")[:200],
            text=message,
            is_from_admin=False,
        )
        user_display = request.user.get_full_name() or request.user.email or "—"
        user_email = request.user.email or ""
        is_guest = False
        guest_contact_val = ""
        guest_telegram_val = ""
    else:
        # Незарегистрированный пользователь (гость)
        guest_tg_username = (guest_telegram_username or "").strip().lstrip("@")
        binding_token = None

        # Если гость указал Telegram username, создаем токен привязки
        if guest_tg_username and tg_support.is_telegram_configured():
            binding_token = secrets.token_urlsafe(32)

        support_msg = SupportMessage.objects.create(
            user=None,
            guest_name=(guest_name or "")[:200].strip(),
            guest_contact=(guest_contact or "")[:200].strip(),
            guest_telegram_username=guest_tg_username,
            guest_binding_token=binding_token or "",
            subject=(subject or "")[:200],
            text=message,
            is_from_admin=False,
        )
        user_display = guest_name or "Гость (незарегистрированный пользователь)"
        user_email = guest_contact or ""
        is_guest = True
        guest_contact_val = guest_contact or ""
        guest_telegram_val = guest_tg_username

    text_for_admin = tg_support.format_support_message_to_admin(
        support_message_id=support_msg.pk,
        user_display=user_display,
        user_email=user_email,
        subject=subject,
        text=message,
        source="сайт",
        is_guest=is_guest,
        guest_contact=guest_contact_val,
        guest_telegram_username=guest_telegram_val,
    )
    msg_id, ok = tg_support.send_to_admin(text_for_admin)
    if ok and msg_id is not None:
        support_msg.admin_telegram_message_id = msg_id
        support_msg.admin_telegram_text = text_for_admin
        support_msg.save(
            update_fields=["admin_telegram_message_id", "admin_telegram_text"]
        )

    binding_url = None
    if request.user.is_authenticated and tg_support.is_telegram_configured():
        link, _ = UserTelegramLink.objects.get_or_create(
            user=request.user,
            defaults={
                "telegram_chat_id": None,
                "binding_token": secrets.token_urlsafe(32),
            },
        )
        # Если пользователь уже привязал бота, проверяем гостевые сообщения
        if link.telegram_chat_id:
            link.migrate_guest_messages()
        elif link.telegram_chat_id is None:
            token = link.get_or_create_binding_token()
            bot_username = tg_support.get_bot_username()
            if bot_username:
                binding_url = f"https://t.me/{bot_username}?start={token}"
    elif (
        is_guest
        and support_msg.guest_binding_token
        and tg_support.is_telegram_configured()
    ):
        # Для гостя создаем ссылку привязки, если указан Telegram username
        bot_username = tg_support.get_bot_username()
        if bot_username:
            binding_url = (
                f"https://t.me/{bot_username}?start={support_msg.guest_binding_token}"
            )

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
    _, binding_url = _create_support_message_and_send_to_admin(
        request, subject, message
    )

    return render(
        request,
        "core/support_feedback_success.html",
        {"telegram_binding_url": binding_url},
    )


@require_http_methods(["POST"])
def support_feedback_submit(request):
    """
    API для виджета (JSON): создать SupportMessage, отправить админу.
    Возвращает success и при необходимости telegram_binding_url.
    Поддерживает как зарегистрированных пользователей, так и гостей.
    """
    try:
        if request.content_type and "application/json" in request.content_type:
            data = json.loads(request.body or "{}")
        else:
            data = request.POST
        message = (data.get("message") or "").strip()
        subject = (data.get("subject") or "").strip()
        guest_name = (data.get("guest_name") or "").strip()
        guest_contact = (data.get("guest_contact") or "").strip()
        guest_telegram_username = (data.get("guest_telegram_username") or "").strip()
    except (json.JSONDecodeError, TypeError):
        return JsonResponse(
            {"success": False, "error": "Неверный формат запроса"}, status=400
        )

    if not message:
        return JsonResponse(
            {"success": False, "error": "Введите сообщение."}, status=400
        )

    # Для незарегистрированных пользователей имя обязательно
    if not request.user.is_authenticated:
        if not guest_name:
            return JsonResponse(
                {"success": False, "error": "Введите ваше имя."}, status=400
            )

    try:
        support_msg, binding_url = _create_support_message_and_send_to_admin(
            request,
            subject,
            message,
            guest_name=guest_name,
            guest_contact=guest_contact,
            guest_telegram_username=guest_telegram_username,
        )
    except Exception as e:
        logger.exception(
            "support_feedback_submit failed for user=%s",
            getattr(request.user, "pk", None),
        )
        return JsonResponse(
            {"success": False, "error": f"Ошибка отправки: {e!s}"},
            status=500,
        )

    # Для гостей сохраняем message_id в session для получения истории
    if not request.user.is_authenticated:
        guest_message_ids = request.session.get("feedback_guest_message_ids", [])
        if support_msg.pk not in guest_message_ids:
            guest_message_ids.append(support_msg.pk)
            request.session["feedback_guest_message_ids"] = guest_message_ids
            request.session.modified = True

    payload = {
        "success": True,
        "message_id": support_msg.pk,
    }
    if binding_url:
        payload["telegram_binding_url"] = binding_url
    return JsonResponse(payload)


# ---------------------------------------------------------------------------
# Telegram Webhook: /start, сообщения пользователя, ответы админа
# ---------------------------------------------------------------------------


def _support_webhook_secret_ok(request) -> bool:
    """Проверка секрета webhook бота поддержки (X-Telegram-Bot-Api-Secret-Token)."""
    secret = getattr(settings, "TELEGRAM_SUPPORT_WEBHOOK_SECRET", None) or ""
    if not secret:
        return True
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    return bool(token == secret)


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

        support_msg = (
            SupportMessage.objects.filter(
                admin_telegram_message_id=original_message_id,
            )
            .select_related("user")
            .first()
        )
        if not support_msg:
            logger.debug(
                "Webhook: no SupportMessage for message_id=%s", original_message_id
            )
            return JsonResponse({"ok": True})

        user = support_msg.user
        is_guest = user is None
        sent_via_telegram = False

        # Если это зарегистрированный пользователь с привязанным Telegram, отправляем ответ
        if user:
            link = getattr(user, "telegram_link", None)
            if link and link.telegram_chat_id:
                safe_text = (
                    text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                )
                tg_support.send_to_user(
                    link.telegram_chat_id, f"📩 <b>Ответ поддержки:</b>\n\n{safe_text}"
                )
                sent_via_telegram = True
        elif is_guest and support_msg.guest_telegram_chat_id:
            # Если гость привязал Telegram, отправляем ответ через бот
            safe_text = (
                text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            tg_support.send_to_user(
                support_msg.guest_telegram_chat_id,
                f"📩 <b>Ответ поддержки:</b>\n\n{safe_text}",
            )
            sent_via_telegram = True

        # Сохраняем ответ админа в БД (guest_binding_token уникален в модели)
        SupportMessage.objects.create(
            user=user,
            guest_binding_token=secrets.token_urlsafe(32),
            guest_name=support_msg.guest_name if is_guest else "",
            guest_contact=support_msg.guest_contact if is_guest else "",
            guest_telegram_username=(
                support_msg.guest_telegram_username if is_guest else ""
            ),
            guest_telegram_chat_id=(
                support_msg.guest_telegram_chat_id if is_guest else None
            ),
            text=text,
            is_from_admin=True,
        )

        # Обновляем сообщение админу с пометкой об отправке ответа
        if support_msg.admin_telegram_text and support_msg.admin_telegram_message_id:
            if is_guest:
                if sent_via_telegram:
                    new_text = (
                        support_msg.admin_telegram_text
                        + "\n\n✅ Ответ отправлен в Telegram"
                    )
                else:
                    new_text = (
                        support_msg.admin_telegram_text
                        + "\n\n✅ Ответ сохранён. Свяжитесь с пользователем по указанным контактам (Telegram не привязан)."
                    )
            else:
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
            # Сначала проверяем токен для зарегистрированных пользователей
            link = UserTelegramLink.objects.filter(binding_token=token).first()
            if link:
                link.telegram_chat_id = chat_id_int
                link.binding_token = ""
                link.token_created_at = None
                link.save(
                    update_fields=[
                        "telegram_chat_id",
                        "binding_token",
                        "token_created_at",
                    ]
                )
                # Переносим гостевые сообщения к этому пользователю, если они есть
                migrated_count = link.migrate_guest_messages()
                if migrated_count > 0:
                    tg_support.send_message(
                        chat_id_int,
                        f"✅ Ваш аккаунт успешно привязан.\n"
                        f"📨 Найдено и привязано {migrated_count} обращений, отправленных до регистрации.",
                    )
                else:
                    tg_support.send_message(
                        chat_id_int, "✅ Ваш аккаунт успешно привязан."
                    )
            else:
                # Проверяем токен для гостей
                guest_msg = SupportMessage.objects.filter(
                    guest_binding_token=token, user__isnull=True
                ).first()
                if guest_msg:
                    guest_msg.guest_telegram_chat_id = chat_id_int
                    guest_msg.guest_binding_token = ""
                    guest_msg.save(
                        update_fields=["guest_telegram_chat_id", "guest_binding_token"]
                    )
                    tg_support.send_message(
                        chat_id_int,
                        f"✅ Ваш Telegram успешно привязан для получения ответов на обращение #{guest_msg.pk}.\n"
                        f"Администратор сможет ответить вам здесь в Telegram.",
                    )
                else:
                    tg_support.send_message(
                        chat_id_int,
                        "Токен не найден или устарел. Отправьте форму на сайте заново и перейдите по новой ссылке.",
                    )
        else:
            # /start без токена — проверяем, привязан ли уже этот чат
            existing = UserTelegramLink.objects.filter(
                telegram_chat_id=chat_id_int
            ).first()
            if existing:
                tg_support.send_message(chat_id_int, "✅ Ваш аккаунт уже привязан.")
            else:
                # Проверяем, есть ли гостевые сообщения с таким chat_id
                guest_messages = SupportMessage.objects.filter(
                    user__isnull=True, guest_telegram_chat_id=chat_id_int
                ).first()
                if guest_messages:
                    tg_support.send_message(
                        chat_id_int,
                        "Вы отправили обращение как незарегистрированный пользователь. "
                        "Зарегистрируйтесь на сайте и привяжите аккаунт по ссылке из профиля, "
                        "чтобы ваши обращения были привязаны к вашему аккаунту.",
                    )
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
            guest_binding_token=secrets.token_urlsafe(32),
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
            support_msg.save(
                update_fields=["admin_telegram_message_id", "admin_telegram_text"]
            )

        return JsonResponse({"ok": True})

    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Старые эндпоинты (виджет на сайте — можно переключить на support_feedback_submit)
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def feedback(request):
    """Редирект на форму обратной связи (новая система)."""
    return redirect("support_feedback")


@require_http_methods(["POST"])
def feedback_submit(request):
    """
    API виджета: использует новую систему SupportMessage и возвращает telegram_binding_url при необходимости.
    Поддерживает как зарегистрированных пользователей, так и гостей.
    """
    return support_feedback_submit(request)


@require_safe
def feedback_threads(request):
    """
    API: список обращений пользователя (SupportMessage) для виджета.
    Поддерживает как авторизованных пользователей, так и гостей (через session).
    Для гостей возвращаются и их сообщения, и ответы админа (по совпадению guest_*).
    """
    threads = []
    messages = []

    if request.user.is_authenticated:
        # Для авторизованных пользователей - получаем все их сообщения и ответы админа
        messages = SupportMessage.objects.filter(user=request.user).order_by(
            "created_at"
        )[:50]
    else:
        # Для гостей - сообщения из session + ответы админа с тем же guest_*
        guest_message_ids = request.session.get("feedback_guest_message_ids", [])
        if guest_message_ids:
            guest_msgs = list(
                SupportMessage.objects.filter(
                    pk__in=guest_message_ids,
                    user__isnull=True,
                ).values_list("guest_name", "guest_contact", "guest_telegram_username")
            )
            triplets = set(guest_msgs)
            q = Q(pk__in=guest_message_ids)
            for gn, gc, gt in triplets:
                q = q | Q(
                    user__isnull=True,
                    guest_name=gn,
                    guest_contact=gc,
                    guest_telegram_username=gt,
                )
            messages = SupportMessage.objects.filter(q).order_by("created_at")[:50]

    current_thread = []
    for m in messages:
        current_thread.append(
            {
                "id": m.pk,
                "text": m.text,
                "is_from_admin": m.is_from_admin,
                "created_at": m.created_at.isoformat(),
            }
        )
    if current_thread:
        threads.append({"messages": current_thread})
    return JsonResponse({"threads": threads})
