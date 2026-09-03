"""
Отправка уведомлений админу в Telegram.
Настройка: TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID в settings / env.
"""

import logging
from typing import cast

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils.html import strip_tags

from apps.core.contact_utils import get_max_display_contact
from apps.courts.surfaces import format_surface_labels
from apps.telegram_bot.telegram_http import (
    is_telegram_api_enabled,
    telegram_requests_proxies,
)

logger = logging.getLogger(__name__)

# Ключ кэша: приветствие при старте отправлено (не слать при каждом рестарте воркера)
CACHE_KEY_NOTIFY_GREETING_SENT = "telegram_notify_startup_greeting_sent"
CACHE_GREETING_TIMEOUT = 60 * 60 * 24 * 7  # 7 дней

# Первое приветствие админам при старте бота уведомлений
NOTIFY_STARTUP_GREETING = (
    "👋 <b>Бот уведомлений TennisFan</b>\n\n"
    "Запущен. Вы будете получать сюда уведомления с сайта (регистрации, заявки, обратная связь и др.)."
)


def _is_shared_cache() -> bool:
    """Проверка, что кэш общий между процессами (Redis и т.п.), а не локальная память."""
    backend = getattr(settings, "CACHES", {}).get("default", {}).get("BACKEND", "")
    return (
        "redis" in backend.lower()
        or "memcached" in backend.lower()
        or "database" in backend.lower()
    )


def send_startup_greeting_to_admins() -> None:
    """
    Отправить приветственное сообщение всем админам один раз после старта.
    Работает только при общем кэше (Redis/Memcached): иначе каждый воркер шлёт отдельно.
    Используется cache.add() (атомарно «установить, если нет»), повтор не чаще чем раз в 7 дней.
    """
    if not _is_shared_cache():
        return
    try:
        added = cache.add(
            CACHE_KEY_NOTIFY_GREETING_SENT,
            True,
            timeout=CACHE_GREETING_TIMEOUT,
        )
        if not added:
            return
    except Exception:
        return
    send_admin_message(NOTIFY_STARTUP_GREETING)


def send_admin_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Отправить сообщение в Telegram админу.
    Возвращает True при успехе, False при отключённом боте или ошибке.
    """
    _, ok = _send_admin_message_raw(text, parse_mode)
    return bool(ok)


def _send_admin_message_raw(text: str, parse_mode: str = "HTML"):
    """
    Отправить сообщение в Telegram всем админам (список TELEGRAM_ADMIN_CHAT_IDS).
    Возвращает (message_id последней отправки или None, success: bool — хотя бы одна успешна).
    """
    token = (getattr(settings, "TELEGRAM_BOT_TOKEN", None) or "").strip()
    chat_ids = getattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", None) or []
    if not chat_ids:
        single = (getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None) or "").strip()
        if single:
            chat_ids = [single]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    last_msg_id = None
    telegram_ok = False
    if token and chat_ids:
        if not is_telegram_api_enabled():
            logger.debug("Telegram notify skipped: TELEGRAM_ENABLED is off")
        else:
            for chat_id in chat_ids:
                cid = str(chat_id).strip()
                if not cid:
                    continue
                payload = {
                    "chat_id": cid,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                }
                try:
                    r = requests.post(
                        url,
                        json=payload,
                        timeout=10,
                        proxies=telegram_requests_proxies(),
                    )
                    r.raise_for_status()
                    data = r.json()
                    result = data.get("result", {})
                    last_msg_id = result.get("message_id")
                    telegram_ok = True
                except Exception as e:
                    logger.warning("Telegram notify failed for chat_id=%s: %s", cid, e)
    else:
        logger.debug(
            "Telegram notify skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_CHAT_ID(s) not set"
        )

    email_ok = _send_admin_email(text=text, parse_mode=parse_mode)
    any_ok = telegram_ok or email_ok
    return last_msg_id, any_ok


def _send_admin_email(text: str, parse_mode: str = "HTML") -> bool:
    """Отправить дубликат админ-уведомления на email.

    Args:
        text (str): Текст уведомления из Telegram-канала.
        parse_mode (str): Режим форматирования Telegram (используется для очистки HTML).

    Returns:
        bool: ``True``, если письмо успешно отправлено, иначе ``False``.
    """
    recipient = (
        getattr(settings, "ADMIN_NOTIFICATIONS_EMAIL", None)
        or getattr(settings, "COURT_APPLICATION_NOTIFICATION_EMAIL", None)
        or "admin@tennisfan.ru"
    )
    recipient = str(recipient).strip()
    if not recipient:
        return False

    body = strip_tags(text) if parse_mode.upper() == "HTML" else text
    subject = _build_admin_email_subject(body)

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru"),
            recipient_list=[recipient],
            fail_silently=False,
        )
        return True
    except Exception as exc:
        logger.warning("Admin email notify failed for %s: %s", recipient, exc)
        return False


def _build_admin_email_subject(body: str) -> str:
    """Собрать тему email по первой информативной строке уведомления.

    Args:
        body (str): Текст уведомления без HTML-разметки.

    Returns:
        str: Тема письма в формате ``TennisFan: <тип уведомления>``.
    """
    default_subject = "TennisFan: Уведомление администратору"
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

    # Убираем ведущие emoji/служебные маркеры, чтобы тема была читаемой.
    normalized = first_line.lstrip(" \t-•:|")
    while normalized and not (normalized[0].isalnum() or normalized[0] in ("№", "#")):
        normalized = normalized[1:].lstrip(" \t-•:|")

    if not normalized:
        return default_subject
    if len(normalized) > 120:
        normalized = normalized[:117].rstrip() + "..."

    return f"TennisFan: {normalized}"


def _escape(s: str) -> str:
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notify_new_registration(user, player) -> bool:
    """Уведомление о регистрации нового пользователя."""
    name = _escape(user.get_full_name() or user.email or "—")
    email = _escape(user.email or "—")
    phone = _escape(getattr(user, "phone", None) or "—")
    city = _escape(getattr(player, "city", None) or "—")
    ntrp = getattr(player, "ntrp_level", None)
    ntrp_s = str(ntrp) if ntrp is not None else "—"

    text = (
        "🆕 <b>Новая регистрация</b>\n\n"
        f"Имя: {name}\n"
        f"Email: {email}\n"
        f"Телефон: {phone}\n"
        f"Населённый пункт: {city}\n"
        f"Сила: {ntrp_s}"
    )
    return send_admin_message(text)


def notify_club_registered(club, creator) -> bool:
    """Уведомить админа о регистрации нового клуба.

    Args:
        club: Объект клуба.
        creator: Пользователь, создавший клуб.

    Returns:
        bool: ``True``, если уведомление отправлено хотя бы в один канал.
    """
    club_name = _escape(getattr(club, "name", "") or "—")
    club_slug = _escape(getattr(club, "slug", "") or "—")
    club_city = _escape(getattr(club, "city", "") or "—")
    club_email = _escape(getattr(club, "email", "") or "—")
    admin_name = _escape(getattr(club, "admin_name", "") or "—")
    creator_name = _escape(creator.get_full_name() or creator.email or "—")
    creator_email = _escape(getattr(creator, "email", "") or "—")

    text = (
        "🏢 <b>Регистрация нового клуба</b>\n\n"
        f"Клуб: {club_name}\n"
        f"Slug: {club_slug}\n"
        f"Населённый пункт: {club_city}\n"
        f"Email клуба: {club_email}\n"
        f"Ответственный: {admin_name}\n\n"
        f"Создатель: {creator_name}\n"
        f"Email создателя: {creator_email}"
    )
    return send_admin_message(text)


def notify_club_subscription_purchase(
    club,
    *,
    plan: str,
    period: str,
    amount: str,
) -> bool:
    """Уведомить админа об оплате подписки клуба платформе.

    Args:
        club: Объект клуба.
        plan (str): Код тарифа клуба.
        period (str): Период оплаты (``monthly``/``yearly``).
        amount (str): Сумма оплаты в рублях.

    Returns:
        bool: ``True``, если уведомление отправлено хотя бы в один канал.
    """
    club_name = _escape(getattr(club, "name", "") or "—")
    club_slug = _escape(getattr(club, "slug", "") or "—")
    plan_value = (plan or "").strip().lower()
    if plan_value == "start":
        plan_label = "START"
    elif plan_value == "basic":
        plan_label = "BASIC"
    elif plan_value == "pro":
        plan_label = "PRO"
    else:
        plan_label = (plan or "—").upper()

    period_value = (period or "").strip().lower()
    period_label = "ежемесячно" if period_value == "monthly" else "ежегодно"
    amount_s = _escape(str(amount or "—"))

    text = (
        "💼 <b>Оплата тарифа клубом</b>\n\n"
        f"Клуб: {club_name}\n"
        f"Slug: {club_slug}\n"
        f"Тариф: {plan_label}\n"
        f"Период: {period_label}\n"
        f"Сумма: {amount_s} ₽"
    )
    return send_admin_message(text)


def notify_tournament_registration(user, tournament) -> bool:
    """Уведомить админа о бесплатной регистрации игрока на турнир.

    Args:
        user: Пользователь, выполнивший регистрацию.
        tournament: Объект турнира.

    Returns:
        bool: ``True``, если уведомление отправлено хотя бы в один канал.
    """
    user_name = _escape(user.get_full_name() or user.email or "—")
    user_email = _escape(getattr(user, "email", "") or "—")
    tournament_name = _escape(getattr(tournament, "name", "") or "—")
    tournament_city = _escape(getattr(tournament, "city", "") or "—")
    is_doubles = bool(getattr(tournament, "is_doubles", lambda: False)())
    format_label = "парный" if is_doubles else "одиночный"

    text = (
        "📝 <b>Регистрация на турнир</b>\n\n"
        f"Турнир: {tournament_name}\n"
        f"Населённый пункт: {tournament_city}\n"
        f"Формат: {format_label}\n\n"
        f"Игрок: {user_name}\n"
        f"Email: {user_email}\n"
        "Тип регистрации: без оплаты взноса"
    )
    return send_admin_message(text)


def notify_coach_application(app) -> bool:
    """Уведомление о заявке «Стать тренером» с полными данными."""
    lines = [
        "👤 <b>Заявка «Стать тренером»</b>",
        "",
        "<b>Заявитель:</b>",
        f"  • {_escape(app.applicant_name)}",
        f"  • Email: {_escape(app.applicant_email)}",
        f"  • Телефон: {_escape(app.applicant_phone) or '—'}",
        "",
        "<b>О тренере:</b>",
        f"  • Имя: {_escape(app.name)}",
        f"  • Населённый пункт: {_escape(app.city)}",
        f"  • Опыт: {app.experience_years} лет",
        f"  • Специализация: {_escape(app.specialization) or '—'}",
        "",
        "<b>Контакты:</b>",
        f"  • Телефон: {_escape(app.phone) or '—'}",
        f"  • Telegram: {_escape(app.telegram) or '—'}",
        f"  • WhatsApp: {_escape(app.whatsapp) or '—'}",
        f"  • MAX: {_escape(get_max_display_contact(app.max_contact) or '') or '—'}",
        "",
        f"Биография: {_escape((app.bio or '')[:300])}{'…' if (app.bio or '') and len(app.bio or '') > 300 else ''}",
    ]
    return send_admin_message("\n".join(lines))


def notify_court_application(app) -> bool:
    """Уведомление о заявке на добавление корта с полными данными."""
    lines = [
        "🏟 <b>Заявка на добавление корта</b>",
        "",
        "<b>Заявитель:</b>",
        f"  • {_escape(app.applicant_name)}",
        f"  • Email: {_escape(app.applicant_email)}",
        f"  • Телефон: {_escape(app.applicant_phone) or '—'}",
        "",
        "<b>Корт:</b>",
        f"  • Название: {_escape(app.name)}",
        f"  • Населённый пункт: {_escape(app.city)}",
        f"  • Адрес: {_escape(app.address)}",
        f"  • Покрытие (крытые): {_escape(format_surface_labels(getattr(app, 'indoor_surfaces', None)) or '—')}",
        f"  • Покрытие (открытые): {_escape(format_surface_labels(getattr(app, 'outdoor_surfaces', None)) or '—')}",
        f"  • Кортов: {app.courts_count}",
        (
            f"  • Освещение: {'да' if app.has_lighting else 'нет'}, "
            f"Крытый: {'да' if app.is_indoor else 'нет'}, "
            f"Открытый: {'да' if getattr(app, 'is_outdoor', False) else 'нет'}"
        ),
    ]
    if app.price_per_hour:
        lines.append(f"  • Цена/час: {app.price_per_hour} ₽")
    lines.extend(
        [
            "",
            "<b>Контакты:</b>",
            f"  • Телефон: {_escape(app.phone) or '—'}",
            f"  • WhatsApp: {_escape(app.whatsapp) or '—'}",
            f"  • Сайт: {_escape(app.website) or '—'}",
            "",
            f"Описание: {_escape((app.description or '')[:200])}{'…' if (app.description or '') and len(app.description or '') > 200 else ''}",
        ]
    )
    return send_admin_message("\n".join(lines))


def notify_feedback(
    user, subject: str, message: str, feedback_id: int | None = None
) -> bool:
    """Уведомление об обратной связи от пользователя."""
    name = _escape(user.get_full_name() or "—")
    email = _escape(user.email or "—")
    subj = _escape(subject or "—")
    msg = _escape(message or "")

    header = "📩 <b>Обратная связь</b>"
    if feedback_id is not None:
        header += f" #{feedback_id}"
    header += "\n\n"
    text = (
        header + f"От: {name}\n"
        f"Email: {email}\n"
        f"Тема: {subj}\n\n"
        f"Сообщение:\n{msg}\n\n"
        "<i>Ответьте на это сообщение в Telegram — ответ придёт пользователю на сайт.</i>"
    )
    return send_admin_message(text)


def send_feedback_to_telegram(
    user, feedback_id: int, subject: str, message: str
) -> int | None:
    """
    Отправить обратную связь в Telegram админу с номером #feedback_id.
    Возвращает message_id из Telegram для сохранения в Feedback.telegram_message_id.
    """
    name = _escape(user.get_full_name() or "—")
    email = _escape(user.email or "—")
    subj = _escape(subject or "—")
    msg = _escape(message or "")

    text = (
        f"📩 <b>Обратная связь #{feedback_id}</b>\n\n"
        f"От: {name}\n"
        f"Email: {email}\n"
        f"Тема: {subj}\n\n"
        f"Сообщение:\n{msg}\n\n"
        "<i>Ответьте на это сообщение — ответ придёт пользователю на сайт.</i>"
    )
    message_id, _ = _send_admin_message_raw(text)
    return cast(int | None, message_id)


def notify_court_comment(comment, court, score: int | None = None) -> bool:
    """Уведомление админу о новом комментарии и оценке к корту."""
    author = getattr(comment, "author", None)
    author_name = _escape(str(author) if author else "—")
    author_email = "—"
    if author:
        try:
            author_email = _escape(getattr(author.user, "email", None) or "—")
        except Exception:
            pass
    text_preview = _escape((comment.text or "")[:300])
    if (comment.text or "") and len(comment.text or "") > 300:
        text_preview += "…"
    court_name = _escape(getattr(court, "name", "") or "—")
    rating_line = f"\nОценка: {score}/5 ★" if score is not None else ""

    msg = (
        "🏟 <b>Комментарий к корту</b>\n\n"
        f"Корт: {court_name}\n"
        f"Автор: {author_name}\n"
        f"Email: {author_email}\n"
        f"{rating_line}\n\n"
        f"Текст:\n{text_preview}"
    )
    return send_admin_message(msg)


def notify_news_comment(comment, news) -> bool:
    """Уведомление админу о новом комментарии к новости."""
    author = getattr(comment, "author", None)
    author_name = _escape(str(author) if author else "—")
    author_email = "—"
    if author:
        try:
            author_email = _escape(getattr(author.user, "email", None) or "—")
        except Exception:
            pass
    text_preview = _escape((comment.text or "")[:300])
    if (comment.text or "") and len(comment.text or "") > 300:
        text_preview += "…"
    news_title = _escape(getattr(news, "title", "") or "—")

    msg = (
        "📰 <b>Новый комментарий к новости</b>\n\n"
        f"Новость: {news_title}\n"
        f"Автор: {author_name}\n"
        f"Email: {author_email}\n\n"
        f"Текст:\n{text_preview}"
    )
    return send_admin_message(msg)


def notify_purchase_request(pr) -> bool:
    """Уведомление админу о заявке на покупку товара."""
    product_name = _escape(pr.product.name if pr.product else "—")
    first_name = _escape(pr.first_name or "—")
    last_name = _escape(pr.last_name or "—")
    phone = _escape(pr.contact_phone or "—")
    comment = _escape((pr.comment or "")[:200])
    if (pr.comment or "") and len(pr.comment or "") > 200:
        comment += "…"
    email = "—"
    if pr.user:
        email = _escape(pr.user.email or "—")

    msg = (
        "🛒 <b>Заявка на покупку</b>\n\n"
        f"Товар: {product_name}\n"
        f"Имя: {first_name} {last_name}\n"
        f"Телефон: {phone}\n"
        f"Email: {email}\n\n"
        f"Комментарий: {comment}"
    )
    return send_admin_message(msg)


def notify_tournament_insufficient_participants(tournament) -> bool:
    """Уведомление админу: недостаточно участников/команд к дедлайну, турнир отменят через 3 ч без продления."""
    from django.conf import settings

    name = _escape(getattr(tournament, "name", "") or "—")
    slug = _escape(getattr(tournament, "slug", "") or "—")
    if getattr(tournament, "is_doubles", lambda: False)():
        current = getattr(tournament, "full_teams_count", lambda: 0)()
        if callable(current):
            current = current()
        min_required = getattr(tournament, "min_teams", None) or 0
        label = "команд"
    else:
        current = getattr(tournament, "participants", None)
        current = current.count() if current is not None else 0
        min_required = getattr(tournament, "min_participants", None) or 0
        label = "участников"
    deadline = getattr(tournament, "registration_deadline", None)
    deadline_str = deadline.strftime("%d.%m.%Y %H:%M") if deadline else "—"
    admin_url = ""
    if hasattr(settings, "ADMIN_URL") and settings.ADMIN_URL:
        admin_url = f"\nПродлить дедлайн: {settings.ADMIN_URL}/tournaments/tournament/{getattr(tournament, 'pk', '')}/change/"
    msg = (
        "⚠️ <b>Турнир: недостаточно участников</b>\n\n"
        f"Турнир: {name}\n"
        f"Slug: {slug}\n"
        f"Зарегистрировано: {current} {label} (минимум: {min_required})\n"
        f"Дедлайн регистрации: {deadline_str}\n\n"
        "Если в течение <b>3 часов</b> не продлить дедлайн регистрации, турнир будет автоматически отменён, участникам вернутся лимиты регистраций."
        f"{admin_url}"
    )
    return send_admin_message(msg)


def notify_donation(amount: str, name_or_email: str = "", comment: str = "") -> bool:
    """Уведомление админу о донате (после успешной оплаты)."""
    amount_s = _escape(str(amount))
    name_s = _escape((name_or_email or "").strip() or "—")
    comment_s = _escape((comment or "").strip() or "—")
    text = (
        "🎁 <b>Донат</b>\n\n"
        f"Сумма: {amount_s} ₽\n"
        f"От: {name_s}\n"
        f"Комментарий: {comment_s}"
    )
    return send_admin_message(text)


def notify_tournament_entry_payment(
    tournament, user, amount: str | None = None
) -> bool:
    """Уведомление админу об оплате взноса за регистрацию на турнир."""
    name = _escape(user.get_full_name() or user.email or "—")
    email = _escape(user.email or "—")
    tournament_name = _escape(getattr(tournament, "name", "") or "—")
    amount_s = amount or str(getattr(tournament, "entry_fee", "") or "—")
    text = (
        "🎾 <b>Оплата взноса за турнир</b>\n\n"
        f"Турнир: {tournament_name}\n"
        f"Участник: {name}\n"
        f"Email: {email}\n"
        f"Сумма: {amount_s} ₽"
    )
    return send_admin_message(text)


def notify_tournament_postpayment_window_opened(tournament, pending_count: int) -> bool:
    """Уведомление администратору о запуске окна постоплаты.

    Args:
        tournament: Турнир, для которого запущено окно постоплаты.
        pending_count (int): Количество участников, которым отправлены инвойсы.

    Returns:
        bool: ``True``, если уведомление отправлено хотя бы в один канал.
    """
    tournament_name = _escape(getattr(tournament, "name", "") or "—")
    deadline_hours = int(getattr(tournament, "postpayment_deadline_hours", 12) or 12)
    text = (
        "💬 <b>Запущена постоплата турнира</b>\n\n"
        f"Турнир: {tournament_name}\n"
        f"Ожидают оплату: {pending_count}\n"
        f"Срок оплаты: {deadline_hours} ч"
    )
    return send_admin_message(text)


def notify_subscription_purchase(user, tier, amount_paid: str | None = None) -> bool:
    """Уведомление о покупке подписки. amount_paid — фактически уплаченная сумма (регион, акция 1 ₽)."""
    name = _escape(user.get_full_name() or user.email or "—")
    email = _escape(user.email or "—")
    phone = _escape(getattr(user, "phone", None) or "—")
    tier_name = _escape(tier.get_name_display())
    amount_s = (amount_paid or "").strip() if amount_paid else None
    if amount_s is None:
        amount_s = str(tier.price)

    text = (
        "💳 <b>Покупка подписки</b>\n\n"
        f"Пользователь: {name}\n"
        f"Email: {email}\n"
        f"Телефон: {phone}\n\n"
        f"Тариф: {tier_name}\n"
        f"Сумма: {amount_s} ₽"
    )
    return send_admin_message(text)


def notify_stringer_rating(rating, created: bool = True) -> bool:
    """Уведомление админу о новой или обновлённой оценке/комментарии компании стрингеров."""
    user = getattr(rating, "user", None)
    company = getattr(rating, "company", None)
    if not company:
        return False

    user_name = _escape(user.get_full_name() or user.email or "—") if user else "—"
    user_email = _escape(user.email or "—") if user else "—"
    company_name = _escape(getattr(company, "name", "") or "—")
    score = getattr(rating, "score", None)
    comment_text = _escape((getattr(rating, "comment", "") or "")[:500])
    if (getattr(rating, "comment", "") or "") and len(
        getattr(rating, "comment", "") or ""
    ) > 500:
        comment_text += "…"

    action = "Новая оценка" if created else "Обновлена оценка"
    msg = (
        "🎾 <b>Стрингеры: {}</b>\n\n"
        "Компания: {}\n"
        "Автор: {}\n"
        "Email: {}\n"
        "Оценка: {}/5 ★\n\n"
        "Комментарий:\n{}"
    ).format(
        action,
        company_name,
        user_name,
        user_email,
        score or "—",
        comment_text or "—",
    )
    return send_admin_message(msg)
