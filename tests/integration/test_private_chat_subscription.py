"""Реальные подписки сохраняют правила доступа и возврата на Free."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.subscriptions.models import SubscriptionTier
from apps.telegram_bot.private_chat import get_private_chat_access_status
from tests.support.factories import make_user


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("active", "expired", "includes_chat", "allowed"),
    [
        (True, False, True, True),
        (True, False, False, False),
        (False, False, True, False),
        (True, True, True, False),
    ],
)
def test_private_chat_access_when_real_subscription_checked_should_preserve_security_and_balance(
    active: bool,
    expired: bool,
    includes_chat: bool,
    allowed: bool,
) -> None:
    """Истечение возвращает на Free, а баланс и запрет доступа сохраняются."""
    free, _ = SubscriptionTier.objects.update_or_create(
        name="free", defaults={"display_name": "Free", "has_private_chat": False}
    )
    user = make_user()
    tier = SubscriptionTier.objects.create(
        name="paid-chat-test", display_name="Paid", has_private_chat=includes_chat
    )
    # Сигнал регистрации уже назначил пользователю Free: настраиваем именно
    # существующую подписку, как делает повышение тарифа, не создавая дубль.
    subscription = user.subscription
    subscription.tier = tier
    subscription.end_date = timezone.now() + timedelta(days=-1 if expired else 30)
    subscription.is_active = active
    subscription.fancoin_balance = 17
    subscription.save()
    assert get_private_chat_access_status(user)[0] is allowed
    subscription.refresh_from_db()
    assert subscription.fancoin_balance == 17
    assert subscription.tier_id == (free.pk if expired else tier.pk)
