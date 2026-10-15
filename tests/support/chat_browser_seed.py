"""Подготавливает сессии браузерного теста только в локальной smoke-базе."""

import json
from pathlib import Path

from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.chat.models import ChatMembership, ChatMessage
from apps.core.models import City
from apps.users.models import User
from tests.support.chat_legal import join_city_with_consent as join_city


class ChatSmokeSetupError(RuntimeError):
    """Ошибка безопасной подготовки локального браузерного стенда."""


def create_smoke_sessions(path: Path) -> None:
    """Создаёт комнату и приватные сессии двух участников и администратора стенда.

    Args:
        path: Путь файла для CHAT_E2E_SESSION_FILE.

    Raises:
        ChatSmokeSetupError: База не является локальной smoke-базой.
    """
    database = connection.settings_dict
    if not str(database["NAME"]).endswith("_smoke") or database["HOST"] not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ChatSmokeSetupError(
            "Для браузерной проверки нужна отдельная локальная база *_smoke."
        )
    city, _ = City.objects.get_or_create(name="Казань", region="Тест браузера")
    consent_city, _ = City.objects.get_or_create(
        name="Тест согласий браузера", region="Изолированная проверка"
    )
    ChatMembership.objects.filter(room__city=consent_city).delete()
    cookies: list[list[dict[str, str]]] = []
    for index, name in enumerate(("Анна", "Михаил")):
        user, created = User.objects.get_or_create(
            email=f"city-chat-browser-{index}@test.local", defaults={"first_name": name}
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        room = join_city(user=user, city_id=city.pk)
        client = Client()
        client.force_login(user)
        cookies.append(
            [
                {"name": key, "value": cookie.value}
                for key, cookie in client.cookies.items()
            ]
        )
    ChatMessage.objects.filter(room=room).delete()
    admin_user, _ = User.objects.update_or_create(
        email="city-chat-admin-browser@test.local",
        defaults={
            "first_name": "Администратор стенда",
            "password": "!",
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        },
    )
    ChatMembership.objects.filter(room=room, user=admin_user).delete()
    admin_client = Client()
    admin_client.force_login(admin_user)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(
        json.dumps(
            {
                "cookies": cookies,
                "room_path": f"/chats/{room.pk}/",
                "consent_city_id": consent_city.pk,
                "consent_city_name": consent_city.name,
                "admin_cookies": [
                    {"name": key, "value": cookie.value}
                    for key, cookie in admin_client.cookies.items()
                ],
                "admin_email": admin_user.email,
                "admin_room_path": reverse(
                    "admin:chat_chatroom_change", args=[room.pk]
                ),
            }
        ),
        encoding="utf-8",
    )
