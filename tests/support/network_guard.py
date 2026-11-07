"""Явная фикстура запрета внешней сети с поддержкой локальных серверов."""

from __future__ import annotations

import socket
from ipaddress import ip_address
from typing import TypeAlias

import pytest
from pytest_mock import MockerFixture

SocketAddress: TypeAlias = tuple[str, int] | tuple[str, int, int, int] | str | bytes
AddressInfo: TypeAlias = tuple[
    socket.AddressFamily,
    socket.SocketKind,
    int,
    str,
    tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes],
]


class ExternalNetworkDeniedError(OSError):
    """Попытка внешнего соединения из изолированного теста.

    Attributes:
        args: Безопасное описание запрета без адреса назначения.
    """


def _is_local(host: str | bytes | None) -> bool:
    """Распознать локальный адрес без обращения к DNS."""
    if host is None:
        return True
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture
def deny_external_network(mocker: MockerFixture) -> None:
    """Запретить внешний DNS и TCP/UDP-соединения на время запросившего теста.

    Args:
        mocker: Подмены, автоматически отменяемые после завершения теста.

    Raises:
        ExternalNetworkDeniedError: При обращении к нелокальному адресу.
    """
    lookup = socket.getaddrinfo
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def guarded_lookup(
        host: str | bytes | None,
        port: str | bytes | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[AddressInfo]:
        """Проверить имя до вызова системного резолвера."""
        if not _is_local(host):
            raise ExternalNetworkDeniedError("Внешняя сеть запрещена")
        return lookup(host, port, family, type, proto, flags)

    def check_address(connection: socket.socket, address: SocketAddress) -> None:
        """Не позволить обойти DNS-защиту прямым IPv4/IPv6-адресом."""
        if (
            connection.family in (socket.AF_INET, socket.AF_INET6)
            and isinstance(address, tuple)
            and not _is_local(address[0])
        ):
            raise ExternalNetworkDeniedError("Внешняя сеть запрещена")

    def guarded_connect(connection: socket.socket, address: SocketAddress) -> None:
        """Проверить адрес до установления соединения."""
        check_address(connection, address)
        connect(connection, address)

    def guarded_connect_ex(connection: socket.socket, address: SocketAddress) -> int:
        """Защитить альтернативный интерфейс установки соединения."""
        check_address(connection, address)
        return connect_ex(connection, address)

    mocker.patch("socket.getaddrinfo", new=guarded_lookup)
    mocker.patch.object(socket.socket, "connect", new=guarded_connect)
    mocker.patch.object(socket.socket, "connect_ex", new=guarded_connect_ex)
