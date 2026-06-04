"""Фабрики тестовых данных для автотестов."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.clubs.models import Club
from apps.payments.models import PaymentRecord
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.tournaments.models import Tournament
from apps.users.models import Player, User

UserModel = get_user_model()


def make_user(
    *,
    email: str = "user@test.local",
    password: str = "testpass123",
    first_name: str = "",
    last_name: str = "",
    **kwargs: Any,
) -> User:
    """Создать пользователя с профилем по умолчанию.

    Args:
        email: Адрес электронной почты.
        password: Пароль для аутентификации.
        first_name: Имя.
        last_name: Фамилия.
        **kwargs: Дополнительные поля модели User.

    Returns:
        Созданный экземпляр User.
    """
    return cast(
        User,
        UserModel.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            **kwargs,
        ),
    )


def make_player(
    *,
    email_suffix: str = "player",
    password: str = "testpass123",
    points: float = 1000.0,
    **user_kwargs: Any,
) -> Player:
    """Создать игрока с привязанным пользователем.

    Args:
        email_suffix: Уникальный суффикс в email.
        password: Пароль пользователя.
        points: Начальные рейтинговые очки.
        **user_kwargs: Дополнительные поля User.

    Returns:
        Созданный экземпляр Player.
    """
    user = make_user(
        email=f"p_{email_suffix}@test.local",
        password=password,
        **user_kwargs,
    )
    return cast(
        Player,
        Player.objects.create(
            user=user,
            total_points=points,
            hidden_rating=points,
        ),
    )


def make_club(
    *,
    name: str = "Тестовый клуб",
    slug: str = "test-club",
    city: str = "Москва",
    **kwargs: Any,
) -> Club:
    """Создать клуб с обязательными полями по умолчанию.

    Args:
        name: Название клуба.
        slug: URL-slug.
        city: Город.
        **kwargs: Дополнительные поля Club.

    Returns:
        Созданный экземпляр Club.
    """
    defaults: dict[str, object] = {
        "address": "Тестовая улица, 1",
        "email": "club@test.local",
        "admin_name": "Админ Клуба",
    }
    defaults.update(kwargs)
    return cast(
        Club,
        Club.objects.create(name=name, slug=slug, city=city, **defaults),
    )


def make_tournament(
    *,
    name: str = "Тестовый турнир",
    slug: str = "test-tournament",
    city: str = "Москва",
    start_date: date | None = None,
    **kwargs: Any,
) -> Tournament:
    """Создать турнир с базовыми полями.

    Args:
        name: Название турнира.
        slug: URL-slug.
        city: Город проведения.
        start_date: Дата начала (по умолчанию — сегодня).
        **kwargs: Дополнительные поля Tournament.

    Returns:
        Созданный экземпляр Tournament.
    """
    if start_date is None:
        start_date = date.today()
    defaults: dict[str, object] = {
        "format": "single_elimination",
    }
    defaults.update(kwargs)
    return cast(
        Tournament,
        Tournament.objects.create(
            name=name,
            slug=slug,
            city=city,
            start_date=start_date,
            **defaults,
        ),
    )


def make_subscription(
    user: User,
    *,
    tier_name: str = "test",
    fancoin_balance: int = 0,
    duration_days: int = 30,
    tier_kwargs: dict[str, Any] | None = None,
) -> UserSubscription:
    """Создать активную подписку пользователя.

    Args:
        user: Владелец подписки.
        tier_name: Системное имя тарифа.
        fancoin_balance: Баланс FANcoin.
        duration_days: Длительность подписки в днях.
        tier_kwargs: Дополнительные поля тарифа при первом создании.

    Returns:
        Созданный экземпляр UserSubscription.
    """
    tier_defaults: dict[str, object] = {
        "display_name": tier_name.title(),
        "fancoin_per_purchase": 15,
        "duration_days": duration_days,
        "is_visible": True,
    }
    if tier_kwargs:
        tier_defaults.update(tier_kwargs)
    tier = cast(
        SubscriptionTier,
        SubscriptionTier.objects.get_or_create(
            name=tier_name,
            defaults=tier_defaults,
        )[0],
    )
    now = timezone.now()
    return cast(
        UserSubscription,
        UserSubscription.objects.create(
            user=user,
            tier=tier,
            start_date=now,
            end_date=now + timezone.timedelta(days=duration_days),
            fancoin_balance=fancoin_balance,
            is_active=True,
        ),
    )


def make_payment_record(
    user: User,
    *,
    yookassa_payment_id: str = "test-payment-001",
    payment_type: str = PaymentRecord.PaymentType.SUBSCRIPTION,
    item_id: str = "",
    amount: object = "1000.00",
    status: str = "succeeded",
    metadata: dict[str, object] | None = None,
    **kwargs: Any,
) -> PaymentRecord:
    """Создать запись журнала платежей для интеграционных тестов.

    Args:
        user: Плательщик.
        yookassa_payment_id: Идентификатор платежа в YooKassa.
        payment_type: Тип оплаты из ``PaymentRecord.PaymentType``.
        item_id: ID связанного объекта (тариф, турнир и т.д.).
        amount: Сумма платежа.
        status: Статус записи (``pending`` / ``succeeded``).
        metadata: Дополнительные метаданные платежа.
        **kwargs: Прочие поля ``PaymentRecord``.

    Returns:
        Созданный экземпляр PaymentRecord.
    """
    defaults: dict[str, object] = {
        "payment_type": payment_type,
        "item_id": item_id,
        "item_label": "Тестовый платёж",
        "amount": amount,
        "status": status,
        "metadata": metadata or {},
    }
    defaults.update(kwargs)
    return cast(
        PaymentRecord,
        PaymentRecord.objects.create(
            user=user,
            yookassa_payment_id=yookassa_payment_id,
            **defaults,
        ),
    )
