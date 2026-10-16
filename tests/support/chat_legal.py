"""Явно подтверждённые согласия тестовых участников, не производственный обход."""

from apps.chat import services
from apps.chat.consent import accept_chat_consent, build_consent_form
from apps.chat.domain import ChatConsentRequiredError, ChatNotFoundError
from apps.chat.models import ChatRoom
from apps.core.models import City
from apps.users.models import User


def join_city_with_consent(*, user: User, city_id: int) -> ChatRoom:
    """Создаёт тестовое участие и отдельно фиксирует явный выбор тестового игрока.

    Args:
        user: Тестовая учётная запись.
        city_id: Город локального теста.

    Returns:
        Комната с принятыми действующими документами.
    """
    services.require_active_user(user)
    if not City.objects.filter(pk=city_id).exists():
        raise ChatNotFoundError("Город не найден.")
    # Старые тестовые комнаты намеренно моделируют существующие сообщества без владельца.
    ChatRoom.objects.get_or_create(city_id=city_id)
    room = services.join_city(user=user, city_id=city_id)
    try:
        services.require_member(user=user, room_id=room.pk)
    except ChatConsentRequiredError:
        form = build_consent_form(user=user, city=room.city)
        accept_chat_consent(
            user=user,
            city=room.city,
            data={
                "token": str(form["token"].value()),
                "personal_data": "on",
                "rules": "on",
                "disclosure": "on",
                "show_name": "on",
                "show_avatar": "on",
            },
            ip_address="127.0.0.1",
            user_agent="Isolated automated test participant",
        )
    return room
