"""Реальная ASGI-переписка двух участников и запрет постороннего origin."""

from asyncio import sleep
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client

from apps.chat import services
from apps.core.models import City
from config.asgi import create_application
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db(transaction=True)


def test_websocket_when_two_members_should_broadcast_and_revoke_after_leave() -> None:
    """Commit виден обоим клиентам, выход закрывает уже открытый сокет."""
    user = make_user(email="socket-chat@test.local")
    city = City.objects.get_or_create(name="Самара")[0]
    room = join_city_with_consent(user=user, city_id=city.pk)
    client = Client()
    client.force_login(user)
    cookie = "; ".join(f"{k}={v.value}" for k, v in client.cookies.items()).encode()

    async def scenario() -> None:
        sockets = [
            WebsocketCommunicator(
                create_application(),
                f"/ws/chats/{room.pk}/",
                headers=[(b"origin", b"http://testserver"), (b"cookie", cookie)],
            )
            for _ in range(2)
        ]
        try:
            for socket in sockets:
                assert (await socket.connect())[0]
                assert (await socket.receive_json_from())["type"] == "ready"
            await database_sync_to_async(services.send_message)(
                user=user, room_id=room.pk, text="Всем привет", client_id=uuid4()
            )
            for socket in sockets:
                assert (await socket.receive_json_from())["type"] == "refresh"
            await database_sync_to_async(services.leave_room)(
                user=user, room_id=room.pk
            )
            for socket in sockets:
                assert (await socket.receive_output())["type"] == "websocket.close"
        finally:
            for socket in sockets:
                await socket.disconnect()

    async_to_sync(scenario)()


def test_websocket_when_guest_outsider_origin_or_unknown_room_should_reject() -> None:
    """Нельзя подключиться без участия или с чужого сайта."""

    async def scenario() -> None:
        for origin in (b"http://evil.example", b"http://testserver"):
            socket = WebsocketCommunicator(
                create_application(), "/ws/chats/999/", headers=[(b"origin", origin)]
            )
            assert not (await socket.connect())[0]
            await socket.disconnect()

    async_to_sync(scenario)()


def test_websocket_when_ping_invalid_frame_or_account_disabled_should_enforce_policy() -> (
    None
):
    """Сокет обрабатывает heartbeat и повторно проверяет активность аккаунта."""
    user = make_user(email="ping-chat@test.local")
    room = join_city_with_consent(
        user=user, city_id=City.objects.get_or_create(name="Клин")[0].pk
    )
    client = Client()
    client.force_login(user)
    cookie = "; ".join(f"{k}={v.value}" for k, v in client.cookies.items()).encode()

    async def scenario() -> None:
        for payload in ("{", "null", '{"type":"send"}', "x" * 4097):
            socket = WebsocketCommunicator(
                create_application(),
                f"/ws/chats/{room.pk}/",
                headers=[(b"origin", b"http://testserver"), (b"cookie", cookie)],
            )
            try:
                assert (await socket.connect())[0]
                await socket.receive_json_from()
                await socket.send_to(text_data=payload)
                assert (await socket.receive_output())["code"] == 4400
            finally:
                await socket.disconnect()
        socket = WebsocketCommunicator(
            create_application(),
            f"/ws/chats/{room.pk}/",
            headers=[(b"origin", b"http://testserver"), (b"cookie", cookie)],
        )
        try:
            assert (await socket.connect())[0]
            await socket.receive_json_from()
            await socket.send_json_to({"type": "ping"})
            assert (await socket.receive_json_from())["type"] == "pong"
            await database_sync_to_async(type(user).objects.filter(pk=user.pk).update)(
                is_active=False
            )
            await sleep(1.1)
            await socket.send_json_to({"type": "ping"})
            assert (await socket.receive_output())["code"] == 4403
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
