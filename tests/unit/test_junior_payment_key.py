"""Проверка устойчивого ключа в клиенте шлюза без внешних запросов."""

from unittest.mock import Mock, patch

from apps.payments.yookassa_client import create_payment


def test_explicit_payment_key_is_passed_to_existing_client() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {
        "id": "payment-test",
        "confirmation": {"confirmation_url": "https://yookassa.ru/test"},
    }
    with (
        patch(
            "apps.payments.yookassa_client._create_auth",
            return_value=("offline", "offline"),
        ),
        patch(
            "apps.payments.yookassa_client._post_yookassa_payment",
            return_value=response,
        ) as post,
    ):
        assert create_payment(
            "500.00",
            "https://tennisfan.ru/kids/payments/",
            "Тест",
            idempotency_key="stable-test-key",
        ) == ("payment-test", "https://yookassa.ru/test")
    assert post.call_args.kwargs["headers"]["Idempotence-Key"] == "stable-test-key"
