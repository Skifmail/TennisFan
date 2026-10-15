"""Подготавливает сессии браузерного теста только в локальной smoke-базе."""

import json
from pathlib import Path

from django.db import connection
from django.test import Client

from apps.chat.models import ChatMessage
from apps.chat.services import join_city
from apps.core.models import City
from apps.users.models import User


class ChatSmokeSetupError(RuntimeError):
    """Ошибка безопасной подготовки локального браузерного стенда."""


def create_smoke_sessions(path: Path) -> None:
    """Создаёт тестовую комнату и приватный файл двух сессий.

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
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(
        json.dumps({"cookies": cookies, "room_path": f"/chats/{room.pk}/"}),
        encoding="utf-8",
    )
