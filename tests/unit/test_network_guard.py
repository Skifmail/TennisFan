"""Подтверждение блокировки сетевых запросов до DNS и передачи данных."""

import socket

import pytest


def test_external_lookup_is_denied(deny_external_network) -> None:
    with pytest.raises(OSError, match="Внешняя сеть запрещена"):
        socket.getaddrinfo("api.yookassa.ru", 443)


def test_external_ip_connection_is_denied(deny_external_network) -> None:
    with (
        socket.socket() as connection,
        pytest.raises(OSError, match="Внешняя сеть запрещена"),
    ):
        connection.connect(("8.8.8.8", 443))
