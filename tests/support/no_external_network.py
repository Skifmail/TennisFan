"""Плагин pytest: запрет внешнего DNS и сокетов при локальных проверках."""

import socket

import pytest

LOCAL_HOSTS = {None, "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1"}


@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch):
    """Разрешает локальный стенд; внешний запрос завершается до отправки данных."""
    original_lookup = socket.getaddrinfo
    original_connect = socket.socket.connect

    def lookup(host, *args, **kwargs):
        if host not in LOCAL_HOSTS:
            raise OSError("Внешняя сеть запрещена при тестировании")
        return original_lookup(host, *args, **kwargs)

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in LOCAL_HOSTS:
            raise OSError("Внешняя сеть запрещена при тестировании")
        return original_connect(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket.socket, "connect", connect)
