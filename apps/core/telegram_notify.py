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
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", None) or ""
    chat_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None) or ""
    if not token.strip() or not chat_id.strip():
        logger.debug("Telegram notify skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_CHAT_ID not set")
        return False

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
        return True
    except Exception as e:
        logger.warning("Telegram notify failed: %s", e)
        return False


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


def notify_feedback(user, subject: str, message: str) -> bool:
    """Уведомление об обратной связи от пользователя."""
    name = _escape(user.get_full_name() or "—")
    email = _escape(user.email or "—")
    subj = _escape(subject or "—")
    msg = _escape(message or "")

    text = (
        "📩 <b>Обратная связь</b>\n\n"
        f"От: {name}\n"
        f"Email: {email}\n"
        f"Тема: {subj}\n\n"
        f"Сообщение:\n{msg}"
    )
    return send_admin_message(text)


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
