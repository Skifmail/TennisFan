"""Вспомогательные функции для подписок и тарифов."""

from decimal import Decimal
from typing import Any, cast

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import RegionalTierPrice, UserSubscription


def get_subscription_renew_amount(subscription: UserSubscription) -> Decimal:
    """Рассчитать стоимость продления подписки с учётом города покупки.

    Args:
        subscription (UserSubscription): Подписка пользователя.

    Returns:
        Decimal: Сумма к списанию в рублях.
    """
    tier = subscription.tier
    purchase_city = normalize_city_for_pricing(subscription.purchase_city or "")
    amount: Decimal = cast(Decimal, tier.price)
    if purchase_city and purchase_city != "moscow":
        regional = RegionalTierPrice.objects.filter(tier=tier).first()
        if regional is not None:
            amount = cast(Decimal, regional.price)
    return amount


def normalize_city_for_pricing(city_str: str) -> str:
    """Нормализовать город для определения тарифа (Москва vs регионы).

    Args:
        city_str (str): Название города в произвольном формате.

    Returns:
        str: Нормализованный код города: ``"moscow"`` для Москвы, иначе
        приведённая к нижнему регистру строка города (по умолчанию ``"moscow"``).
    """
    city = (city_str or "").lower().strip()
    if city in ("moscow", "moskva", "москва"):
        return "moscow"
    return city or "moscow"


def _get_valid_subscription_tier(user: Any) -> Any | None:
    """Вернуть тариф активной подписки пользователя.

    Args:
        user (Any): Пользователь Django.

    Returns:
        Any | None: Тариф активной подписки или ``None``, если подписка
        отсутствует либо не действует.
    """
    if not getattr(user, "is_authenticated", False):
        return None

    subscription = getattr(user, "subscription", None)
    if subscription is None or not subscription.is_valid():
        return None

    return getattr(subscription, "tier", None)


def user_can_read_comments(user: Any) -> bool:
    """Проверить доступ пользователя к чтению комментариев.

    Args:
        user (Any): Пользователь Django, для которого проверяется доступ.

    Returns:
        bool: ``True``, если у пользователя есть активная подписка и тариф
        разрешает чтение комментариев.
    """
    tier = _get_valid_subscription_tier(user)
    return bool(tier is not None and getattr(tier, "can_read_comments", False))


def user_can_write_comments(user: Any) -> bool:
    """Проверить доступ пользователя к написанию комментариев.

    Args:
        user (Any): Пользователь Django, для которого проверяется доступ.

    Returns:
        bool: ``True``, если у пользователя есть активная подписка и тариф
        разрешает написание комментариев.
    """
    tier = _get_valid_subscription_tier(user)
    return bool(tier is not None and getattr(tier, "can_write_comments", False))


def user_can_rate_opponents(user: Any) -> bool:
    """Проверить доступ пользователя к оценке соперников.

    Args:
        user (Any): Пользователь Django, для которого проверяется доступ.

    Returns:
        bool: ``True``, если у пользователя есть активная подписка и тариф
        разрешает оценку соперников после матчей.
    """
    tier = _get_valid_subscription_tier(user)
    return bool(tier is not None and getattr(tier, "can_rate_opponents", False))


def send_subscription_purchase_email(
    user: Any,
    subscription: UserSubscription,
    amount_paid: str | None = None,
) -> None:
    """Отправить пользователю письмо с деталями оплаченной подписки.

    Args:
        user (Any): Пользователь, оформивший подписку.
        subscription (UserSubscription): Обновлённая подписка пользователя.
        amount_paid (str | None): Фактически оплаченная сумма в рублях
            (строка формата ``"1000.00"``). Если не указана, используется
            базовая цена тарифа.

    Returns:
        None: Письмо отправляется через сконфигурированный почтовый бэкенд.
    """
    email = getattr(user, "email", None)
    if not email or "@" not in str(email):
        return

    tier = subscription.tier
    now = timezone.now()

    amount_text = (amount_paid or "").strip()
    if not amount_text:
        try:
            amount_text = f"{Decimal(str(tier.price)):.2f}"
        except Exception:
            amount_text = ""

    subject = f"Оплата подписки «{tier.get_name_display()}» на TennisFan"

    start_str = subscription.start_date.strftime("%d.%m.%Y")
    end_str = subscription.end_date.strftime("%d.%m.%Y")

    if tier.is_unlimited:
        registrations_text = "Безлимитный баланс FAN-token."
    else:
        remaining = subscription.get_fancoin_balance()
        registrations_text = (
            "FAN-token недоступен." if remaining == 0 else f"Баланс FT: {remaining}."
        )

    features: list[str] = []
    if getattr(tier, "has_sparring", False):
        features.append("доступ к разделу спаррингов")
    if getattr(tier, "can_see_stats", False):
        features.append("доступ к расширенной статистике")
    if getattr(tier, "can_write_comments", False):
        features.append("возможность писать комментарии")
    if getattr(tier, "can_rate_opponents", False):
        features.append("возможность оценивать соперников")
    if getattr(tier, "has_private_chat", False):
        features.append("доступ в закрытый чат")
    if getattr(tier, "has_admin_support", False):
        features.append("поддержка администратора")
    if getattr(tier, "has_badge", False):
        features.append("особый значок в профиле")

    features_block = (
        "- " + "\n- ".join(features) if features else "— Дополнительных опций нет."
    )

    amount_line = f"Сумма оплаты: {amount_text} ₽." if amount_text else ""

    message_lines = [
        f"Здравствуйте, {user.get_full_name() or user.username}!",
        "",
        "Ваша подписка успешно оплачена.",
        "",
        f"Тариф: {tier.get_name_display()}",
        f"Срок действия тарифа: {tier.duration_label}",
        f"Дата начала: {start_str}",
        f"Дата окончания: {end_str}",
        registrations_text,
        amount_line,
        "",
        "Что даёт этот тариф:",
        features_block,
        "",
        "Если вы не совершали эту оплату или заметили неточность в данных, "
        "пожалуйста, свяжитесь с нашей поддержкой.",
        "",
        f"Дата операции: {now.strftime('%d.%m.%Y %H:%M:%S')}",
        "",
        "С уважением, команда TennisFan.",
    ]

    # Фильтруем пустые строки, чтобы избежать двойных переносов из-за пустых полей.
    message = "\n".join(line for line in message_lines if line is not None)

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "tennis@tennisfan.ru"
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[str(email)],
            fail_silently=True,
        )
    except Exception:
        # Ошибки отправки письма не должны ломать пользовательский флоу оплаты.
        return
