"""Авторизованный WebSocket-транспорт сигналов городского чата."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any, Final

from asgiref.sync import async_to_sync
from channels.generic.websocket import JsonWebsocketConsumer

from apps.users.models import User

from .domain import MAX_FRAME_LENGTH, ChatError
from .services import require_member

POLICY_CLOSE: Final = 4403
INVALID_CLOSE: Final = 4400
MIN_PING_INTERVAL: Final = 1.0


# Channels не поставляет py.typed; типы прикладного интерфейса заданы ниже.
class CityChatConsumer(JsonWebsocketConsumer):
    """Доставляет сигналы участникам с повторной проверкой доступа.

    Attributes:
        room_id: Идентификатор комнаты URL.
        group_name: Группа доставки между ASGI-процессами.
    """

    room_id: int
    group_name: str = ""
    last_ping: float = 0.0

    def _allowed(self) -> bool:
        principal = self.scope.get("user")
        if principal is None or not principal.is_authenticated:
            return False
        user = User.objects.filter(pk=principal.pk, is_active=True).first()
        if user is None:
            return False
        try:
            require_member(user=user, room_id=self.room_id)
        except ChatError:
            return False
        return True

    def connect(self) -> None:
        """Подключает только активного участника открытой комнаты."""
        self.room_id = int(self.scope["url_route"]["kwargs"]["room_id"])
        if not self._allowed():
            self.close(code=POLICY_CLOSE)
            return
        self.group_name = f"city_chat_{self.room_id}"
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.channel_name)
        self.accept()
        self.send_json({"type": "ready"})

    def disconnect(self, code: int) -> None:
        """Отписывает закрытое соединение от доставки.

        Args:
            code: Код завершения соединения.
        """
        if self.group_name:
            async_to_sync(self.channel_layer.group_discard)(
                self.group_name, self.channel_name
            )

    def receive(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,
        **kwargs: Any,
    ) -> None:
        """Принимает ограниченный heartbeat без команд записи.

        Args:
            text_data: JSON heartbeat.
            bytes_data: Неподдерживаемый бинарный кадр.
            **kwargs: Дополнительные параметры Channels.
        """
        if (
            bytes_data is not None
            or text_data is None
            or len(text_data) > MAX_FRAME_LENGTH
        ):
            self.close(code=INVALID_CLOSE)
            return
        try:
            payload = json.loads(text_data)
        except (ValueError, RecursionError):
            self.close(code=INVALID_CLOSE)
            return
        if not isinstance(payload, dict) or payload.get("type") != "ping":
            self.close(code=INVALID_CLOSE)
            return
        now = monotonic()
        if now - self.last_ping < MIN_PING_INTERVAL:
            self.close(code=INVALID_CLOSE)
            return
        self.last_ping = now
        if not self._allowed():
            self.close(code=POLICY_CLOSE)
            return
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.channel_name)
        self.send_json({"type": "pong"})

    def chat_changed(self, event: dict[str, Any]) -> None:
        """Проверяет доступ перед каждым сигналом, не раскрывая текст.

        Args:
            event: Событие фиксации изменений в БД.
        """
        if not self._allowed():
            self.close(code=POLICY_CLOSE)
            return
        self.send_json({"type": "refresh"})
