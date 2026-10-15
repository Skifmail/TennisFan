"""Контракт доступа к закрытому чату после нормализации подписки."""

from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from apps.telegram_bot.private_chat import get_private_chat_access_status


@pytest.mark.parametrize(
    ("has_subscription", "valid", "includes_chat", "expected"),
    [
        (False, False, False, (False, "Нет активной подписки.")),
        (True, False, True, (False, "Подписка неактивна или истекла.")),
        (True, True, False, (False, "Ваш тариф не включает доступ в закрытый чат.")),
        (True, True, True, (True, "")),
    ],
)
def test_private_chat_access_when_subscription_normalized_should_obey_tier_and_validity(
    mocker: MockerFixture,
    has_subscription: bool,
    valid: bool,
    includes_chat: bool,
    expected: tuple[bool, str],
) -> None:
    """Отсутствующая, неактивная и неподдерживаемая подписки не дают доступ."""
    user = SimpleNamespace()
    subscription = (
        SimpleNamespace(
            tier=SimpleNamespace(has_private_chat=includes_chat),
            is_valid=lambda: valid,
        )
        if has_subscription
        else None
    )
    normalize = mocker.patch(
        "apps.subscriptions.services.ensure_current_subscription",
        return_value=subscription,
    )
    assert get_private_chat_access_status(user) == expected
    normalize.assert_called_once_with(user)
