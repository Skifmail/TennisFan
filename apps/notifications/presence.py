"""Присутствие пользователя в комнате чата для подавления лишних push."""

from typing import Final, cast

from django.core.cache import cache
from django.utils import timezone
from loguru import logger

PRESENCE_TIMEOUT: Final = 120
PRESENCE_PREFIX: Final = "notifications:chat-presence"


def _presence_key(user_id: int, room_id: int) -> str:
    return f"{PRESENCE_PREFIX}:{user_id}:{room_id}"


def enter_chat(*, user_id: int, room_id: int) -> None:
    """Отмечает открытую комнату и отменяет ещё не отправленный push.

    Счётчик поддерживает несколько вкладок: пока хотя бы одна подключена,
    пользователь считается находящимся в чате.

    Args:
        user_id: Участник комнаты.
        room_id: Открытая комната.
    """
    key = _presence_key(user_id, room_id)
    # Сначала отмечаем присутствие, потом отменяем очередь: сообщение,
    # пришедшее между этими шагами, worker всё равно отбросит по presence.
    try:
        if not cache.add(key, 1, timeout=PRESENCE_TIMEOUT):
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=PRESENCE_TIMEOUT)
            else:
                cache.touch(key, PRESENCE_TIMEOUT)
    except Exception:
        logger.warning(
            "Не удалось отметить присутствие в чате", user_id=user_id, room_id=room_id
        )
    try:
        cancel_pending_chat_push(user_id=user_id, room_id=room_id)
    except Exception:
        # Ошибка отмены не должна рвать WebSocket: worker сам проверит presence.
        logger.warning(
            "Не удалось отменить ожидающий chat-push",
            user_id=user_id,
            room_id=room_id,
        )


def leave_chat(*, user_id: int, room_id: int) -> None:
    """Уменьшает счётчик подключений после закрытия вкладки.

    Args:
        user_id: Участник комнаты.
        room_id: Закрытая комната.
    """
    key = _presence_key(user_id, room_id)
    try:
        remaining = cache.decr(key)
        if remaining <= 0:
            cache.delete(key)
    except ValueError:
        cache.delete(key)
    except Exception:
        logger.warning(
            "Не удалось снять присутствие в чате", user_id=user_id, room_id=room_id
        )


def touch_chat(*, user_id: int, room_id: int) -> None:
    """Продлевает присутствие по heartbeat подключённой вкладки.

    Args:
        user_id: Участник комнаты.
        room_id: Открытая комната.
    """
    key = _presence_key(user_id, room_id)
    try:
        if cache.touch(key, PRESENCE_TIMEOUT):
            return
    except Exception:
        logger.warning(
            "Не удалось продлить присутствие в чате", user_id=user_id, room_id=room_id
        )
        return
    # Ключ истёк во время сна вкладки — восстанавливаем его и отменяем push.
    enter_chat(user_id=user_id, room_id=room_id)


def is_in_chat(user_id: int, room_id: int) -> bool:
    """Проверяет, открыта ли у пользователя эта комната.

    При недоступности кэша возвращает ``False``: лучше отправить push,
    чем потерять уведомление.

    Args:
        user_id: Получатель.
        room_id: Проверяемая комната.

    Returns:
        Есть ли активное подключение к комнате.
    """
    try:
        return bool(cache.get(_presence_key(user_id, room_id)))
    except Exception:
        logger.warning(
            "Не удалось проверить присутствие в чате", user_id=user_id, room_id=room_id
        )
        return False


def cancel_pending_chat_push(*, user_id: int, room_id: int) -> int:
    """Помечает ожидающий chat-push просмотренным.

    Args:
        user_id: Получатель.
        room_id: Комната, которую пользователь уже открыл.

    Returns:
        Число отменённых заданий.
    """
    from .models import Delivery

    updated = Delivery.objects.filter(
        user_id=user_id,
        channel="push",
        status="queued",
        chat_room_id=room_id,
    ).update(
        status="skipped",
        error_code="seen_in_chat",
        finished_at=timezone.now(),
    )
    return cast(int, updated)
