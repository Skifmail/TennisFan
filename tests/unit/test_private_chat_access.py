"""Контракт свежей проверки закрытого чата без изменения пользовательской подписки."""

from datetime import timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.telegram_bot.private_chat import get_private_chat_access_status
from apps.users.models import User


@pytest.mark.parametrize(
    ("has_subscription", "valid", "includes_chat", "expected"),
    [
        (False, False, False, (False, "Нет активной подписки.")),
        (True, False, True, (False, "Подписка неактивна или истекла.")),
        (True, True, False, (False, "Ваш тариф не включает доступ в закрытый чат.")),
        (True, True, True, (True, "")),
    ],
)
def test_private_chat_access_when_subscription_read_should_obey_tier_and_validity(
    mocker: MockerFixture,
    has_subscription: bool,
    valid: bool,
    includes_chat: bool,
    expected: tuple[bool, str],
) -> None:
    """Отсутствующая, неактивная и неподдерживаемая подписки не дают доступ."""
    user = cast(User, SimpleNamespace(pk=123))
    subscription = (
        SimpleNamespace(
            tier=SimpleNamespace(has_private_chat=includes_chat),
            is_valid=lambda: valid,
            start_date=timezone.now() - timedelta(days=1),
        )
        if has_subscription
        else None
    )
    normalize = mocker.patch(
        "apps.subscriptions.services.ensure_current_subscription",
    )
    query = mocker.patch(
        "apps.telegram_bot.private_chat.UserSubscription.objects.filter"
    )
    query.return_value.filter.return_value.select_related.return_value.first.return_value = (
        subscription
    )
    assert get_private_chat_access_status(user) == expected
    query.assert_called_once_with(user_id=user.pk)
    query.return_value.filter.assert_called_once_with(user__is_active=True)
    normalize.assert_not_called()
