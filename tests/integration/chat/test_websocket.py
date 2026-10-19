"""Реальные TCP/WebSocket-проверки Uvicorn без тестовых зависимостей Daphne."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from socket import AF_INET, SOCK_STREAM, socket
from typing import Final, cast
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import uvicorn
from asgiref.sync import async_to_sync
from asgiref.typing import ASGIApplication, ASGIReceiveCallable, ASGISendCallable, Scope
from channels.db import database_sync_to_async
from django.test import Client
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus
from websockets.frames import Close, CloseCode, Opcode
from websockets.typing import Origin

from apps.chat import services
from apps.chat.consumers import INVALID_CLOSE, MIN_PING_INTERVAL, POLICY_CLOSE
from apps.chat.domain import MAX_FRAME_LENGTH
from apps.core.models import City
from config.asgi import create_application
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db(transaction=True)

HOST: Final = "127.0.0.1"
EPHEMERAL_PORT: Final = 0
IO_TIMEOUT: Final = 5
STARTUP_POLL_INTERVAL: Final = 0.01
SCHEDULING_MARGIN: Final = 0.1
PING_INTERVAL: Final = 20.0
PING_TIMEOUT: Final = 30.0
FAST_PING_INTERVAL: Final = 0.05
FAST_PING_TIMEOUT: Final = 0.1
UNKNOWN_ROOM_ID: Final = 999
FORWARDED_CLIENT: Final = "198.51.100.24"
UNTRUSTED_PROXY: Final = "192.0.2.1"
FRAME_HEADER_SIZE: Final = 2
FRAME_OPCODE_MASK: Final = 0x0F
FRAME_MASK_BIT: Final = 0x80
FRAME_LENGTH_MASK: Final = 0x7F
FRAME_MEDIUM_LENGTH: Final = 126
FRAME_LARGE_LENGTH: Final = 127
FRAME_MEDIUM_BYTES: Final = 2
FRAME_LARGE_BYTES: Final = 8
HANDSHAKE_KEY: Final = "dGhlIHNhbXBsZSBub25jZQ=="


@asynccontextmanager
async def _serve_app(
    application: ASGIApplication | None = None,
    *,
    forwarded_allow_ips: str = HOST,
    ping_interval: float = PING_INTERVAL,
    ping_timeout: float = PING_TIMEOUT,
) -> AsyncIterator[str]:
    """Запускает настоящий ASGI-сервер только на loopback и свободном порту."""
    with socket(AF_INET, SOCK_STREAM) as listener:
        listener.bind((HOST, EPHEMERAL_PORT))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                application or create_application(),
                loop="asyncio",
                lifespan="off",
                ws="websockets-sansio",
                ws_ping_interval=ping_interval,
                ws_ping_timeout=ping_timeout,
                proxy_headers=True,
                forwarded_allow_ips=forwarded_allow_ips,
                log_config=None,
                timeout_graceful_shutdown=IO_TIMEOUT,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(IO_TIMEOUT):
                while not server.started:
                    if task.done():
                        await task
                        pytest.fail("ASGI-сервер завершился до готовности")
                    await asyncio.sleep(STARTUP_POLL_INTERVAL)
            yield f"ws://{HOST}:{port}/ws/chats"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=IO_TIMEOUT)


def _connect(
    base: str,
    room_id: int,
    *,
    cookie: str | None = None,
    origin: str = "http://testserver",
) -> connect:
    """Подключается реальным клиентом с заданными сессией и origin."""
    return connect(
        f"{base}/{room_id}/",
        origin=Origin(origin),
        additional_headers={"Cookie": cookie} if cookie else None,
        open_timeout=IO_TIMEOUT,
        close_timeout=IO_TIMEOUT,
    )


async def _receive_json(connection: ClientConnection) -> dict[str, object]:
    """Получает JSON-сообщение с ограниченным временем ожидания."""
    payload: object = json.loads(
        await asyncio.wait_for(connection.recv(), timeout=IO_TIMEOUT)
    )
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def _scope_echo(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    """Отражает безопасные поля scope для проверки прокси и heartbeat."""
    assert scope["type"] == "websocket"
    assert (await receive())["type"] == "websocket.connect"
    await send({"type": "websocket.accept", "subprotocol": None, "headers": []})
    client = scope.get("client")
    await send(
        {
            "type": "websocket.send",
            "bytes": None,
            "text": json.dumps(
                {"scheme": scope["scheme"], "client": client[0] if client else None}
            ),
        }
    )
    while (await receive())["type"] != "websocket.disconnect":
        continue


async def _receive_frame(reader: asyncio.StreamReader) -> tuple[Opcode, bytes]:
    """Читает серверный кадр без автоматического ответа Pong со стороны клиента."""
    first, second = await reader.readexactly(FRAME_HEADER_SIZE)
    assert not second & FRAME_MASK_BIT
    length = second & FRAME_LENGTH_MASK
    if length == FRAME_MEDIUM_LENGTH:
        length = int.from_bytes(await reader.readexactly(FRAME_MEDIUM_BYTES), "big")
    elif length == FRAME_LARGE_LENGTH:
        length = int.from_bytes(await reader.readexactly(FRAME_LARGE_BYTES), "big")
    return Opcode(first & FRAME_OPCODE_MASK), await reader.readexactly(length)


def test_websocket_when_two_members_should_broadcast_and_revoke_after_leave() -> None:
    """Commit доставляется обоим TCP-клиентам, выход закрывает их соединения."""
    user = make_user(email="socket-chat@test.local")
    city = City.objects.get_or_create(name="Самара")[0]
    room = join_city_with_consent(user=user, city_id=city.pk)
    client = Client()
    client.force_login(user)
    cookie = "; ".join(f"{k}={v.value}" for k, v in client.cookies.items())

    async def scenario() -> None:
        async with _serve_app() as base:
            async with (
                _connect(base, room.pk, cookie=cookie) as first,
                _connect(base, room.pk, cookie=cookie) as second,
            ):
                for connection in (first, second):
                    assert (await _receive_json(connection))["type"] == "ready"
                await database_sync_to_async(services.send_message)(
                    user=user, room_id=room.pk, text="Всем привет", client_id=uuid4()
                )
                for connection in (first, second):
                    assert (await _receive_json(connection))["type"] == "refresh"
                await database_sync_to_async(services.leave_room)(
                    user=user, room_id=room.pk
                )
                for connection in (first, second):
                    await asyncio.wait_for(connection.wait_closed(), timeout=IO_TIMEOUT)
                    assert connection.close_code == POLICY_CLOSE

    async_to_sync(scenario)()


def test_websocket_when_guest_outsider_origin_or_unknown_room_should_reject() -> None:
    """Настоящий handshake отклоняет гостей и чужой origin с HTTP 403."""

    async def scenario() -> None:
        async with _serve_app() as base:
            for origin in ("http://evil.example", "http://testserver"):
                with pytest.raises(InvalidStatus) as caught:
                    async with _connect(base, UNKNOWN_ROOM_ID, origin=origin):
                        pytest.fail("Посторонний клиент подключился к чату")
                assert caught.value.response.status_code == HTTPStatus.FORBIDDEN

    async_to_sync(scenario)()


def test_websocket_when_ping_invalid_frame_or_account_disabled_should_enforce_policy() -> (
    None
):
    """Uvicorn сохраняет прикладной heartbeat, лимит сообщения и отзыв доступа."""
    user = make_user(email="ping-chat@test.local")
    room = join_city_with_consent(
        user=user, city_id=City.objects.get_or_create(name="Клин")[0].pk
    )
    client = Client()
    client.force_login(user)
    cookie = "; ".join(f"{k}={v.value}" for k, v in client.cookies.items())

    async def scenario() -> None:
        async with _serve_app() as base:
            for payload in (
                "{",
                "null",
                '{"type":"send"}',
                "x" * (MAX_FRAME_LENGTH + 1),
                b"binary",
            ):
                async with _connect(base, room.pk, cookie=cookie) as connection:
                    assert (await _receive_json(connection))["type"] == "ready"
                    await connection.send(payload)
                    await asyncio.wait_for(connection.wait_closed(), timeout=IO_TIMEOUT)
                    assert connection.close_code == INVALID_CLOSE
            async with _connect(base, room.pk, cookie=cookie) as connection:
                assert (await _receive_json(connection))["type"] == "ready"
                await connection.send(json.dumps({"type": "ping"}))
                assert (await _receive_json(connection))["type"] == "pong"
                await database_sync_to_async(
                    type(user).objects.filter(pk=user.pk).update
                )(is_active=False)
                await asyncio.sleep(MIN_PING_INTERVAL + SCHEDULING_MARGIN)
                await connection.send(json.dumps({"type": "ping"}))
                await asyncio.wait_for(connection.wait_closed(), timeout=IO_TIMEOUT)
                assert connection.close_code == POLICY_CLOSE

    async_to_sync(scenario)()


@pytest.mark.parametrize(
    ("trusted_proxy", "expected_scheme", "expected_client"),
    [(HOST, "wss", FORWARDED_CLIENT), (UNTRUSTED_PROXY, "ws", HOST)],
)
def test_proxy_headers_when_source_is_checked_should_only_trust_allowed_proxy(
    trusted_proxy: str, expected_scheme: str, expected_client: str
) -> None:
    """Проверяет доверенный HTTPS-прокси и отказ от поддельных заголовков.

    Args:
        trusted_proxy: Разрешённый адрес прокси в конфигурации сервера.
        expected_scheme: Ожидаемая схема WebSocket в scope.
        expected_client: Ожидаемый IP клиента после проверки доверия.
    """

    async def scenario() -> None:
        async with _serve_app(_scope_echo, forwarded_allow_ips=trusted_proxy) as base:
            async with connect(
                base,
                additional_headers={
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-For": FORWARDED_CLIENT,
                },
                open_timeout=IO_TIMEOUT,
                close_timeout=IO_TIMEOUT,
            ) as connection:
                assert await _receive_json(connection) == {
                    "scheme": expected_scheme,
                    "client": expected_client,
                }

    async_to_sync(scenario)()


def test_keepalive_when_client_does_not_pong_should_close_after_timeout() -> None:
    """Настоящий SansIO-сервер посылает Ping и закрывает неответившего клиента."""

    async def scenario() -> None:
        async with _serve_app(
            _scope_echo,
            ping_interval=FAST_PING_INTERVAL,
            ping_timeout=FAST_PING_TIMEOUT,
        ) as base:
            port = urlsplit(base).port
            assert port is not None
            reader, writer = await asyncio.open_connection(HOST, port)
            try:
                request = (
                    "GET /ws/chats/probe/ HTTP/1.1\r\n"
                    f"Host: {HOST}:{port}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {HANDSHAKE_KEY}\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                )
                writer.write(request.encode("ascii"))
                await writer.drain()
                async with asyncio.timeout(IO_TIMEOUT):
                    response = await reader.readuntil(b"\r\n\r\n")
                    assert response.startswith(b"HTTP/1.1 101 ")
                    assert (await _receive_frame(reader))[0] == Opcode.TEXT
                    assert (await _receive_frame(reader))[0] == Opcode.PING
                    opcode, payload = await _receive_frame(reader)
                    assert opcode == Opcode.CLOSE
                    close = Close.parse(payload)
                    assert close.code == CloseCode.INTERNAL_ERROR
                    assert "timeout" in close.reason.lower()
            finally:
                writer.close()
                await writer.wait_closed()

    async_to_sync(scenario)()


def test_websocket_when_connection_reopens_should_receive_new_committed_events() -> (
    None
):
    """Повторное TCP-подключение восстанавливает членство в группе доставки."""
    user = make_user(email="reconnected-chat@test.local")
    room = join_city_with_consent(
        user=user,
        city_id=City.objects.get_or_create(name="Город переподключения")[0].pk,
    )
    client = Client()
    client.force_login(user)
    cookie = "; ".join(f"{k}={v.value}" for k, v in client.cookies.items())

    async def scenario() -> None:
        async with _serve_app() as base:
            async with _connect(base, room.pk, cookie=cookie) as original:
                assert (await _receive_json(original))["type"] == "ready"
            async with _connect(base, room.pk, cookie=cookie) as reconnected:
                assert (await _receive_json(reconnected))["type"] == "ready"
                await database_sync_to_async(services.send_message)(
                    user=user, room_id=room.pk, text="После обрыва", client_id=uuid4()
                )
                assert (await _receive_json(reconnected))["type"] == "refresh"

    async_to_sync(scenario)()


@pytest.mark.parametrize("extra_length", [0, 1])
def test_websocket_when_frame_reaches_size_boundary_should_apply_exact_limit(
    extra_length: int,
) -> None:
    """Допускает граничный размер и отклоняет превышение на один символ.

    Args:
        extra_length: Превышение разрешённого размера кадра.
    """
    user = make_user(email=f"frame-boundary-{extra_length}@test.local")
    room = join_city_with_consent(
        user=user, city_id=City.objects.get_or_create(name="Город границы кадра")[0].pk
    )
    client = Client()
    client.force_login(user)
    cookie = "; ".join(f"{k}={v.value}" for k, v in client.cookies.items())

    async def scenario() -> None:
        async with _serve_app() as base:
            async with _connect(base, room.pk, cookie=cookie) as connection:
                assert (await _receive_json(connection))["type"] == "ready"
                payload = json.dumps({"type": "ping"}).ljust(
                    MAX_FRAME_LENGTH + extra_length
                )
                await connection.send(payload)
                if extra_length:
                    await asyncio.wait_for(connection.wait_closed(), timeout=IO_TIMEOUT)
                    assert connection.close_code == INVALID_CLOSE
                else:
                    assert (await _receive_json(connection))["type"] == "pong"

    async_to_sync(scenario)()


def test_keepalive_when_client_answers_pong_should_keep_connection_open() -> None:
    """Отвечающий клиент остаётся подключённым после срока ожидания Pong."""

    async def scenario() -> None:
        async with _serve_app(
            _scope_echo,
            ping_interval=FAST_PING_INTERVAL,
            ping_timeout=FAST_PING_TIMEOUT,
        ) as base:
            async with connect(
                base, open_timeout=IO_TIMEOUT, close_timeout=IO_TIMEOUT
            ) as connection:
                assert (await _receive_json(connection))["scheme"] == "ws"
                await asyncio.sleep(
                    FAST_PING_INTERVAL + FAST_PING_TIMEOUT + SCHEDULING_MARGIN
                )
                pong = await connection.ping()
                await asyncio.wait_for(pong, timeout=IO_TIMEOUT)
                assert connection.close_code is None

    async_to_sync(scenario)()
