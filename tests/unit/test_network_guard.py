"""Подтверждение блокировки сетевых запросов до DNS и передачи данных."""

import socket

import pytest


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_network_guard_when_loopback_lookup_should_allow(
    deny_external_network: None, host: str
) -> None:
    """Не блокирует адреса локальных тестовых серверов.

    Args:
        deny_external_network: Активная защита от внешней сети.
        host: Имя или адрес локального сервера.
    """
    assert socket.getaddrinfo(host, 443)


def test_external_lookup_is_denied(deny_external_network: None) -> None:
    """Блокирует внешнее имя до выполнения DNS-запроса."""
    with pytest.raises(OSError, match="Внешняя сеть запрещена"):
        socket.getaddrinfo("api.yookassa.ru", 443)


def test_external_ip_connection_is_denied(deny_external_network: None) -> None:
    """Блокирует прямое внешнее соединение без обхода через IP-адрес."""
    with (
        socket.socket() as connection,
        pytest.raises(OSError, match="Внешняя сеть запрещена"),
    ):
        connection.connect(("8.8.8.8", 443))
