"""Проверки вложенного управления сообщениями и участниками городских чатов."""

from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.chat.models import ChatEvent, ChatMembership, ChatMessage, ChatRoom
from apps.core.models import City
from apps.users.models import User
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client: Client) -> Client:
    """Возвращает клиент администратора с доступом к управлению чатами.

    Args:
        client: Тестовый HTTP-клиент.

    Returns:
        Авторизованный клиент суперпользователя.
    """
    client.force_login(
        make_user(email="room-admin@test.local", is_staff=True, is_superuser=True)
    )
    return client


def _room(name: str = "Раменское") -> ChatRoom:
    return ChatRoom.objects.create(city=City.objects.create(name=name))


def _room_url(room: ChatRoom) -> str:
    return reverse("admin:chat_chatroom_change", args=[room.pk])


def _member_url(
    action: str, room: ChatRoom, member: ChatMembership | None = None
) -> str:
    return (
        reverse(
            f"admin:chat_chatmembership_{action}", args=[member.pk] if member else []
        )
        + f"?room_id={room.pk}"
    )


def _member_data(user: User, room: ChatRoom, **changes: Any) -> dict[str, Any]:
    return {
        "user": user.pk,
        "room": room.pk,
        "is_active": "on",
        "notifications_enabled": "on",
        "_save": "Сохранить",
        **changes,
    }


def test_admin_when_menu_opened_should_show_rooms_and_reports_without_child_sections(
    admin_client: Client,
) -> None:
    """В меню остаётся одна точка входа в чаты, а жалобы доступны отдельно.

    Args:
        admin_client: Клиент администратора.
    """
    response = admin_client.get(reverse("admin:index"))
    models = {
        model["object_name"]
        for app in response.context["available_apps"]
        if app["app_label"] == "chat"
        for model in app["models"]
    }
    assert models == {"ChatRoom", "ChatReport"}


def test_admin_when_room_opened_should_show_only_its_messages_and_members(
    admin_client: Client,
) -> None:
    """Карточка содержит два блока с данными исключительно выбранного города.

    Args:
        admin_client: Клиент администратора.
    """
    room, other = _room(), _room("Другой город")
    user = make_user(email="inside-room@test.local")
    outsider = make_user(email="outside-room@test.local")
    member = ChatMembership.objects.create(room=room, user=user)
    ChatMembership.objects.create(room=other, user=outsider)
    message = ChatMessage.objects.create(
        room=room, author=user, text="Текст этого города"
    )
    ChatMessage.objects.create(room=other, author=outsider, text="Чужая переписка")
    response = admin_client.get(_room_url(room))
    html = response.content.decode()
    assert response.status_code == 200
    assert 'id="chat-room-messages"' in html
    assert 'id="chat-room-members"' in html
    assert message.text in html and user.email in html
    assert "Чужая переписка" not in html and outsider.email not in html
    assert _member_url("add", room) in html
    assert _member_url("change", room, member).replace("&", "&amp;") in html
    assert _member_url("delete", room, member).replace("&", "&amp;") in html


def test_admin_when_lists_large_should_paginate_each_block_independently(
    admin_client: Client,
) -> None:
    """Большие списки не загружаются целиком и имеют независимые страницы.

    Args:
        admin_client: Клиент администратора.
    """
    room = _room()
    users = User.objects.bulk_create(
        [
            User(email=f"room-page-{index}@test.local", password="!")
            for index in range(31)
        ]
    )
    ChatMembership.objects.bulk_create(
        [ChatMembership(room=room, user=user) for user in users]
    )
    ChatMessage.objects.bulk_create(
        [
            ChatMessage(room=room, author=user, text=f"Сообщение {user.pk}")
            for user in users
        ]
    )
    first = admin_client.get(_room_url(room))
    assert len(first.context["chat_messages_page"]) == 25
    assert len(first.context["chat_members_page"]) == 25
    second = admin_client.get(_room_url(room), {"messages_page": 2, "members_page": 1})
    assert len(second.context["chat_messages_page"]) == 6
    assert len(second.context["chat_members_page"]) == 25
    assert not {
        message.pk for message in first.context["chat_messages_page"]
    }.intersection(message.pk for message in second.context["chat_messages_page"])


def test_admin_when_member_added_and_edited_should_return_to_room_and_save_all_settings(
    admin_client: Client,
) -> None:
    """Добавление и изменение сохраняют контекст города и настройки участника.

    Args:
        admin_client: Клиент администратора.
    """
    room, other = _room(), _room("Не выбранный город")
    user = make_user(email="room-new-member@test.local")
    add_url = _member_url("add", room)
    response = admin_client.get(add_url)
    assert response.context["adminform"].form.fields["room"].disabled
    response = admin_client.post(add_url, _member_data(user, other))
    assert response.status_code == 302
    assert response.headers["Location"] == _room_url(room) + "#chat-room-members"
    member = ChatMembership.objects.get(user=user)
    assert member.room_id == room.pk
    response = admin_client.post(
        _member_url("change", room, member),
        _member_data(
            user,
            other,
            is_moderator="on",
            banned_until_0="2027-01-01",
            banned_until_1="10:00:00",
        ),
    )
    assert response.status_code == 302
    assert response.headers["Location"] == _room_url(room) + "#chat-room-members"
    member.refresh_from_db()
    assert member.room_id == room.pk and member.is_moderator
    assert member.banned_until is not None
    assert member.notifications_enabled


def test_admin_when_member_deleted_should_preserve_user_messages_and_events(
    admin_client: Client,
) -> None:
    """Удаляется участие, но не аккаунт, сообщения или журнал переписки.

    Args:
        admin_client: Клиент администратора.
    """
    user = make_user(email="room-remove-member@test.local")
    room = _room()
    member = ChatMembership.objects.create(room=room, user=user)
    message = ChatMessage.objects.create(
        room=room, author=user, text="Сохранить историю"
    )
    event = ChatEvent.objects.create(room=room, message=message)
    url = _member_url("delete", room, member)
    assert admin_client.get(url).status_code == 200
    response = admin_client.post(url, {"post": "yes"})
    assert response.status_code == 302
    assert response.headers["Location"] == _room_url(room) + "#chat-room-members"
    assert not ChatMembership.objects.filter(pk=member.pk).exists()
    assert User.objects.filter(pk=user.pk).exists()
    assert ChatMessage.objects.filter(pk=message.pk, text=message.text).exists()
    assert ChatEvent.objects.filter(pk=event.pk).exists()


@pytest.mark.parametrize("model", ["chatmembership", "chatmessage"])
def test_admin_when_legacy_child_list_opened_should_redirect_to_city_chats(
    admin_client: Client, model: str
) -> None:
    """Старые ссылки не открывают отдельные глобальные списки дочерних объектов.

    Args:
        admin_client: Клиент администратора.
        model: Имя дочерней модели.
    """
    response = admin_client.get(reverse(f"admin:chat_{model}_changelist"))
    assert response.status_code == 302
    assert response.headers["Location"] == reverse("admin:chat_chatroom_changelist")


@pytest.mark.parametrize("room_id", ["abc", "-1", "999999999999999999999999999999"])
def test_admin_when_parent_identifier_invalid_should_reject_without_server_error(
    admin_client: Client, room_id: str
) -> None:
    """Неверный идентификатор родительского чата безопасно отклоняется.

    Args:
        admin_client: Клиент администратора.
        room_id: Некорректный идентификатор комнаты.
    """
    response = admin_client.get(
        reverse("admin:chat_chatmembership_add"), {"room_id": room_id}
    )
    assert response.status_code == 404


def test_admin_when_nested_room_mismatches_member_should_not_edit_another_city(
    admin_client: Client,
) -> None:
    """Контекст другого города не позволяет изменить чужую запись участия.

    Args:
        admin_client: Клиент администратора.
    """
    room, other = _room(), _room("Другой контекст")
    user = make_user(email="room-mismatch@test.local")
    member = ChatMembership.objects.create(room=room, user=user)
    response = admin_client.post(
        _member_url("change", other, member),
        _member_data(user, other, is_moderator="on"),
    )
    assert response.status_code == 404
    member.refresh_from_db()
    assert member.room_id == room.pk and not member.is_moderator


def test_admin_when_only_room_permission_should_not_expose_children_or_mutate_members(
    client: Client,
) -> None:
    """Доступ к комнате сам по себе не даёт доступа к сообщениям и участникам.

    Args:
        client: Тестовый HTTP-клиент.
    """
    user = make_user(email="room-only-view@test.local", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_chatroom"))
    room = _room()
    member = ChatMembership.objects.create(room=room, user=user)
    ChatMessage.objects.create(room=room, author=user, text="Закрытый текст сообщения")
    client.force_login(user)
    response = client.get(_room_url(room))
    assert response.status_code == 200
    assert "Закрытый текст сообщения" not in response.content.decode()
    assert response.context["chat_messages_page"] is None
    assert response.context["chat_members_page"] is None
    assert client.get(_member_url("add", room)).status_code == 403
    assert (
        client.post(_member_url("delete", room, member), {"post": "yes"}).status_code
        == 403
    )


def test_admin_when_child_permissions_without_room_permission_should_deny_access(
    client: Client,
) -> None:
    """Прямой URL не обходит права доступа к родительскому чату.

    Args:
        client: Тестовый HTTP-клиент.
    """
    user = make_user(email="room-child-only@test.local", is_staff=True)
    user.user_permissions.add(
        *Permission.objects.filter(content_type__app_label="chat").exclude(
            content_type__model="chatroom"
        )
    )
    room = _room()
    member = ChatMembership.objects.create(room=room, user=user)
    client.force_login(user)
    assert client.get(_member_url("change", room, member)).status_code == 403


def test_admin_when_message_opened_should_remain_readonly_and_escape_user_text(
    admin_client: Client,
) -> None:
    """Просмотр переписки не позволяет переписать сообщение в обход сервиса.

    Args:
        admin_client: Клиент администратора.
    """
    room = _room()
    message = ChatMessage.objects.create(room=room, text="<script>alert(1)</script>")
    html = admin_client.get(_room_url(room)).content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    url = reverse("admin:chat_chatmessage_change", args=[message.pk])
    response = admin_client.get(url)
    assert response.status_code == 200
    assert _room_url(room) in response.content.decode()
    assert admin_client.post(url, {"text": "Подмена"}).status_code == 403
    message.refresh_from_db()
    assert message.text == "<script>alert(1)</script>"


def test_admin_when_initial_room_forged_should_keep_selected_parent(
    admin_client: Client,
) -> None:
    """Начальные параметры формы не подменяют город, выбранный в карточке.

    Args:
        admin_client: Клиент администратора.
    """
    room, other = _room(), _room("Подставленный город")
    user = make_user(email="room-forged-initial@test.local")
    url = _member_url("add", room) + f"&room={other.pk}"
    page = admin_client.get(url)
    assert str(page.context["adminform"].form["room"].value()) == str(room.pk)
    response = admin_client.post(url, _member_data(user, other))
    assert response.status_code == 302
    assert ChatMembership.objects.get(user=user).room_id == room.pk


def test_admin_when_member_already_exists_should_show_validation_error(
    admin_client: Client,
) -> None:
    """Повторное добавление не создаёт дубль и не приводит к ошибке сервера.

    Args:
        admin_client: Клиент администратора.
    """
    room = _room()
    user = make_user(email="room-duplicate@test.local")
    ChatMembership.objects.create(room=room, user=user)
    response = admin_client.post(_member_url("add", room), _member_data(user, room))
    assert response.status_code == 200
    assert response.context["adminform"].form.errors
    assert ChatMembership.objects.filter(room=room, user=user).count() == 1


def test_admin_when_member_view_only_should_forbid_save_and_delete(
    client: Client,
) -> None:
    """Просмотр участия не позволяет назначить модератора или удалить человека.

    Args:
        client: Тестовый HTTP-клиент.
    """
    user = make_user(email="room-readonly-member@test.local", is_staff=True)
    user.user_permissions.add(
        *Permission.objects.filter(
            codename__in=["view_chatroom", "view_chatmembership"]
        )
    )
    room = _room()
    member = ChatMembership.objects.create(room=room, user=user)
    client.force_login(user)
    response = client.get(_member_url("change", room, member))
    assert response.status_code == 200
    assert not response.context["has_change_permission"]
    assert (
        client.post(
            _member_url("change", room, member),
            _member_data(user, room, is_moderator="on"),
        ).status_code
        == 403
    )
    assert client.get(_member_url("delete", room, member)).status_code == 403


@pytest.mark.parametrize("button", ["_continue", "_addanother"])
def test_admin_when_alternative_save_used_should_preserve_room_context(
    admin_client: Client, button: str
) -> None:
    """Дополнительные кнопки сохранения не теряют выбранный город.

    Args:
        admin_client: Клиент администратора.
        button: Вариант сохранения формы.
    """
    room = _room()
    user = make_user(email="room-save-options@test.local")
    response = admin_client.post(
        _member_url("add", room), _member_data(user, room, **{button: "1"})
    )
    assert response.status_code == 302
    assert f"room_id={room.pk}" in response.headers["Location"]
    assert admin_client.get(response.headers["Location"]).status_code == 200
