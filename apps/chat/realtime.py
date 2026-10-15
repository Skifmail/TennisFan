"""Доставка сигналов об изменениях; сообщения остаются в основной БД."""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from loguru import logger
from redis.exceptions import RedisError


def publish_room_change(room_id: int) -> None:
    """Уведомляет подключённых участников после фиксации транзакции.

    Args:
        room_id: Комната с изменениями.
    """
    layer = get_channel_layer()
    if layer is None:
        logger.error("Не настроен канал городских чатов", room_id=room_id)
        return
    try:
        async_to_sync(layer.group_send)(
            f"city_chat_{room_id}", {"type": "chat.changed"}
        )
    except (RedisError, OSError, TimeoutError):
        logger.exception(
            "Не доставлен сигнал чата; клиент восстановится по журналу", room_id=room_id
        )
