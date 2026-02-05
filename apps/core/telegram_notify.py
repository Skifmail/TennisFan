"""
Отправка уведомлений админу в Telegram.
Настройка: TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID в settings / env.
"""

import logging
import requests

from django.conf import settings

logger = logging.getLogger(__name__)


def send_admin_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Отправить сообщение в Telegram админу.
    Возвращает True при успехе, False при отключённом боте или ошибке.
    """
    _, ok = _send_admin_message_raw(text, parse_mode)
    return ok


def _send_admin_message_raw(text: str, parse_mode: str = "HTML"):
    """
    Отправить сообщение в Telegram админу.
    Возвращает (message_id или None, success: bool).
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", None) or ""
    chat_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None) or ""
    if not token.strip() or not chat_id.strip():
        logger.debug("Telegram notify skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_CHAT_ID not set")
        return None, False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id.strip(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        result = data.get("result", {})
        msg_id = result.get("message_id")
        return msg_id, True
    except Exception as e:
        logger.warning("Telegram notify failed: %s", e)
        return None, False


def _escape(s: str) -> str:
    if not s:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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
        f"Город: {city}\n"
        f"NTRP: {ntrp_s}"
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
        f"  • Город: {_escape(app.city)}",
        f"  • Опыт: {app.experience_years} лет",
        f"  • Специализация: {_escape(app.specialization) or '—'}",
        "",
        "<b>Контакты:</b>",
        f"  • Телефон: {_escape(app.phone) or '—'}",
        f"  • Telegram: {_escape(app.telegram) or '—'}",
        f"  • WhatsApp: {_escape(app.whatsapp) or '—'}",
        f"  • MAX: {_escape(app.max_contact) or '—'}",
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
        f"  • Город: {_escape(app.city)}",
        f"  • Адрес: {_escape(app.address)}",
        f"  • Покрытие: {_escape(app.get_surface_display())}",
        f"  • Кортов: {app.courts_count}",
        f"  • Освещение: {'да' if app.has_lighting else 'нет'}, Крытый: {'да' if app.is_indoor else 'нет'}",
    ]
    if app.price_per_hour:
        lines.append(f"  • Цена/час: {app.price_per_hour} ₽")
    lines.extend([
        "",
        "<b>Контакты:</b>",
        f"  • Телефон: {_escape(app.phone) or '—'}",
        f"  • WhatsApp: {_escape(app.whatsapp) or '—'}",
        f"  • Сайт: {_escape(app.website) or '—'}",
        "",
        f"Описание: {_escape((app.description or '')[:200])}{'…' if (app.description or '') and len(app.description or '') > 200 else ''}",
    ])
    return send_admin_message("\n".join(lines))


def notify_feedback(user, subject: str, message: str, feedback_id: int | None = None) -> bool:
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
        header
        + f"От: {name}\n"
        f"Email: {email}\n"
        f"Тема: {subj}\n\n"
        f"Сообщение:\n{msg}\n\n"
        "<i>Ответьте на это сообщение в Telegram — ответ придёт пользователю на сайт.</i>"
    )
    return send_admin_message(text)


def send_feedback_to_telegram(user, feedback_id: int, subject: str, message: str) -> int | None:
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
    return message_id


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


def notify_about_us_comment(comment) -> bool:
    """Уведомление админу о новом комментарии на странице «О нас»."""
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

    msg = (
        "💬 <b>Новый комментарий на странице «О нас»</b>\n\n"
        f"Автор: {author_name}\n"
        f"Email: {author_email}\n\n"
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
    from django.utils import timezone
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


def notify_subscription_purchase(user, tier) -> bool:
    """Уведомление о покупке подписки."""
    name = _escape(user.get_full_name() or user.email or "—")
    email = _escape(user.email or "—")
    phone = _escape(getattr(user, "phone", None) or "—")
    tier_name = _escape(tier.get_name_display())
    price = tier.price

    text = (
        "💳 <b>Покупка подписки</b>\n\n"
        f"Пользователь: {name}\n"
        f"Email: {email}\n"
        f"Телефон: {phone}\n\n"
        f"Тариф: {tier_name}\n"
        f"Сумма: {price} ₽"
    )
    return send_admin_message(text)
