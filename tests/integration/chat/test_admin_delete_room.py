"""Удаление сообщества целиком, без снятия защиты отдельных сообщений."""

from uuid import uuid4

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.chat.models import (
    ChatArchive,
    ChatEvent,
    ChatManagementEvent,
    ChatMembership,
    ChatMessage,
    ChatReport,
    ChatRoom,
)
from apps.core.models import City, LegalAcceptanceLog
from apps.users.models import Notification
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("bulk", [False, True])
def test_delete_when_platform_owner_confirms_should_remove_whole_room_only(
    client: Client, bulk: bool
) -> None:
    """Владелец удаляет сообщения, жалобы, роли и уведомления только выбранного чата.

    Args:
        client: Клиент владельца платформы.
        bulk: Используется ли массовое удаление из списка.
    """
    staff = make_user(
        email="delete-platform@test.local", is_staff=True, is_superuser=True
    )
    user = make_user(email="delete-player@test.local")
    city = City.objects.create(name="Удаляемое сообщество")
    room = ChatRoom.objects.create(city=city)
    other = ChatRoom.objects.create(
        city=City.objects.create(name="Соседнее сообщество")
    )
    ChatMembership.objects.create(room=room, user=user, is_moderator=True)
    message = ChatMessage.objects.create(
        room=room, author=user, text="Удаляемое сообщение", client_id=uuid4()
    )
    event = ChatEvent.objects.create(room=room, message=message)
    report = ChatReport.objects.create(
        message=message, reporter=user, reason="Проверка"
    )
    role = ChatManagementEvent.objects.create(
        room=room, actor=staff, target=user, action="assigned"
    )
    notice = Notification.objects.create(
        user=user, message="Ответ", url=reverse("chat:room", args=[room.pk])
    )
    retained = Notification.objects.create(
        user=user, message="Другой ответ", url=reverse("chat:room", args=[other.pk])
    )
    legal_proof = LegalAcceptanceLog.objects.create(
        user=user,
        document_slug="chat-rules",
        document_version="test",
        source="chat_join",
        metadata={"room_id": room.pk},
    )
    client.force_login(staff)
    url = (
        reverse("admin:chat_chatroom_changelist")
        if bulk
        else reverse("admin:chat_chatroom_delete", args=[room.pk])
    )
    selected = (
        {"action": "delete_selected", "_selected_action": [room.pk]} if bulk else {}
    )
    preview = client.post(url, selected) if bulk else client.get(url)
    assert preview.status_code == 200
    assert not preview.context["perms_lacking"]
    assert "журнал юридических согласий" in preview.content.decode()
    assert ChatRoom.objects.filter(pk=room.pk).exists()
    response = client.post(url, {**selected, "post": "yes"})
    assert response.status_code == 302
    assert not ChatRoom.objects.filter(pk=room.pk).exists()
    archive = ChatArchive.objects.get(source_room_id=room.pk)
    assert archive.room_id is None
    assert (
        archive.messages.get(source_message_id=message.pk).text == "Удаляемое сообщение"
    )
    assert not ChatMessage.objects.filter(pk=message.pk).exists()
    assert not ChatMembership.objects.filter(room_id=room.pk).exists()
    assert not ChatEvent.objects.filter(pk=event.pk).exists()
    assert not ChatReport.objects.filter(pk=report.pk).exists()
    assert not ChatManagementEvent.objects.filter(pk=role.pk).exists()
    assert not Notification.objects.filter(pk=notice.pk).exists()
    assert ChatRoom.objects.filter(pk=other.pk).exists()
    assert Notification.objects.filter(pk=retained.pk).exists()
    assert City.objects.filter(pk=city.pk).exists()
    assert type(user).objects.filter(pk=user.pk).exists()
    assert LegalAcceptanceLog.objects.filter(pk=legal_proof.pk).exists()


def test_delete_when_staff_has_no_room_delete_permission_should_deny(
    client: Client,
) -> None:
    """Прав просмотра комнаты недостаточно для её уничтожения.

    Args:
        client: Клиент сотрудника с правом просмотра.
    """
    staff = make_user(email="delete-viewer@test.local", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_chatroom"))
    room = ChatRoom.objects.create(city=City.objects.create(name="Защищённый чат"))
    client.force_login(staff)
    assert (
        client.post(
            reverse("admin:chat_chatroom_delete", args=[room.pk]), {"post": "yes"}
        ).status_code
        == 403
    )
    assert ChatRoom.objects.filter(pk=room.pk).exists()


def test_delete_when_message_selected_alone_should_remain_forbidden(
    client: Client,
) -> None:
    """Каскадное удаление комнаты не открывает физическое удаление отдельных сообщений.

    Args:
        client: Клиент владельца платформы.
    """
    staff = make_user(
        email="delete-message-owner@test.local", is_staff=True, is_superuser=True
    )
    room = ChatRoom.objects.create(
        city=City.objects.create(name="Город защиты сообщения")
    )
    message = ChatMessage.objects.create(
        room=room, author=staff, text="Сохраняем", client_id=uuid4()
    )
    client.force_login(staff)
    assert (
        client.post(
            reverse("admin:chat_chatmessage_delete", args=[message.pk]), {"post": "yes"}
        ).status_code
        == 403
    )
    assert ChatMessage.objects.filter(pk=message.pk).exists()
