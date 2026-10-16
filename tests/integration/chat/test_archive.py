"""Закрытый архив сохраняет версии и не исчезает вместе с городским чатом."""

from uuid import uuid4

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from pytest_mock import MockerFixture

from apps.chat import services
from apps.chat.models import (
    ChatArchive,
    ChatArchivedMessage,
    ChatMessage,
    ChatMessageVersion,
    ChatRoom,
)
from apps.core.models import City
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def _room() -> tuple[ChatRoom, int]:
    user = make_user(email="archive-author@test.local", first_name="Автор архива")
    room = join_city_with_consent(
        user=user, city_id=City.objects.create(name="Город архива").pk
    )
    return room, user.pk


def test_archive_when_sent_edited_deleted_should_keep_every_text_and_actor(
    mocker: MockerFixture,
) -> None:
    """Редактирование и удаление меняют публичную копию, но не уничтожают версии.

    Args:
        mocker: Изоляция доставки внешних уведомлений.
    """
    mocker.patch("apps.chat.services.publish_room_change")
    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    message = services.send_message(
        user=user, room_id=room.pk, text="Первый текст", client_id=uuid4()
    )
    services.edit_message(
        user=user, room_id=room.pk, message_id=message.pk, text="Исправленный текст"
    )
    services.delete_message(user=user, room_id=room.pk, message_id=message.pk)
    services.delete_message(user=user, room_id=room.pk, message_id=message.pk)
    saved = ChatArchivedMessage.objects.get(
        archive__source_room_id=room.pk, source_message_id=message.pk
    )
    assert list(
        saved.versions.order_by("sequence").values_list(
            "action", "text", "actor_reference"
        )
    ) == [
        ("created", "Первый текст", user.pk),
        ("edited", "Исправленный текст", user.pk),
        ("deleted", "Исправленный текст", user.pk),
    ]
    public = services.history(user=user, room_id=room.pk)["messages"][0]
    assert public["text"] == ""
    assert public["is_deleted"]
    assert saved.text == "Исправленный текст"


def test_archive_when_send_retried_should_not_duplicate_version() -> None:
    """Повтор идемпотентной отправки не добавляет ложную редакцию."""
    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    key = uuid4()
    first = services.send_message(
        user=user, room_id=room.pk, text="Один раз", client_id=key
    )
    services.send_message(user=user, room_id=room.pk, text="Один раз", client_id=key)
    assert (
        ChatArchivedMessage.objects.get(source_message_id=first.pk).versions.count()
        == 1
    )


def test_archive_when_room_deleted_and_recreated_should_keep_generations_separate() -> (
    None
):
    """Новый чат города не получает старую переписку и не затирает её архив."""
    room, user_id = _room()
    message = ChatMessage.objects.create(
        room=room, author_id=user_id, text="Старый разговор", client_id=uuid4()
    )
    old_id = room.pk
    city = room.city
    room.delete()
    archive = ChatArchive.objects.get(source_room_id=old_id)
    assert archive.room_id is None
    assert archive.room_removed_at is not None
    assert archive.messages.get(source_message_id=message.pk).text == "Старый разговор"
    new = ChatRoom.objects.create(city=city)
    assert ChatArchive.objects.get(room=new).pk != archive.pk
    assert not new.messages.exists()
    assert not ChatArchive.objects.get(room=new).messages.exists()


def test_archive_when_write_fails_should_roll_back_public_send(
    mocker: MockerFixture,
) -> None:
    """Сообщение не подтверждается отправленным, если архив не сохранился.

    Args:
        mocker: Управление отказом записи версии.
    """
    from django.db import DatabaseError

    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    mocker.patch(
        "apps.chat.archive.ChatMessageVersion.save",
        side_effect=DatabaseError("test write failure"),
    )
    with pytest.raises(DatabaseError):
        services.send_message(
            user=user, room_id=room.pk, text="Без потери истории", client_id=uuid4()
        )
    assert not ChatMessage.objects.filter(room=room).exists()


def test_archive_when_moderator_deletes_should_record_moderator_not_author() -> None:
    """В истории различаются автор сообщения и удаливший его модератор."""
    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    moderator = make_user(email="archive-moderator@test.local")
    join_city_with_consent(user=moderator, city_id=room.city_id)
    room.memberships.filter(user=moderator).update(is_moderator=True)
    message = services.send_message(
        user=user, room_id=room.pk, text="Текст нарушения", client_id=uuid4()
    )
    services.delete_message(user=moderator, room_id=room.pk, message_id=message.pk)
    deleted = ChatMessageVersion.objects.get(
        message__source_message_id=message.pk, action="deleted"
    )
    assert deleted.actor_reference == moderator.pk
    assert deleted.message.author_reference == user.pk
    assert deleted.text == "Текст нарушения"


def test_archive_when_author_deleted_should_keep_original_reference_and_text() -> None:
    """Архивные ссылки не каскадируются за удаляемым аккаунтом."""
    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    message = services.send_message(
        user=user, room_id=room.pk, text="История автора", client_id=uuid4()
    )
    user.delete()
    saved = ChatArchivedMessage.objects.get(source_message_id=message.pk)
    assert saved.author_reference == user_id
    assert saved.versions.get().text == "История автора"


def test_archive_when_platform_viewer_opens_should_search_versions_and_paginate(
    client: Client,
) -> None:
    """Закрытая страница находит старую редакцию, не выгружая весь чат в карточку.

    Args:
        client: Клиент сотрудника с доступом к архивам.
    """
    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    message = services.send_message(
        user=user, room_id=room.pk, text="Секрет прежней редакции", client_id=uuid4()
    )
    services.edit_message(
        user=user, room_id=room.pk, message_id=message.pk, text="Новая редакция"
    )
    staff = make_user(email="archive-viewer@test.local", is_staff=True)
    staff.user_permissions.add(
        Permission.objects.get(codename="view_chatarchive"),
        Permission.objects.get(codename="view_chatroom"),
    )
    client.force_login(staff)
    archive = ChatArchive.objects.get(room=room)
    response = client.get(
        reverse("admin:chat_chatarchive_change", args=[archive.pk]),
        {"q": "Секрет прежней"},
    )
    assert response.status_code == 200
    assert response.context["archive_messages_page"].paginator.count == 1
    archived = archive.messages.get(source_message_id=message.pk)
    detail = client.get(
        reverse("admin:chat_chatarchivedmessage_change", args=[archived.pk])
    )
    assert "Секрет прежней редакции" in detail.content.decode()
    assert "Новая редакция" in detail.content.decode()
    assert (
        client.get(
            reverse("admin:chat_chatarchive_delete", args=[archive.pk])
        ).status_code
        == 403
    )


@pytest.mark.parametrize("staff", [False, True])
def test_archive_when_only_city_administrator_should_not_grant_private_access(
    client: Client, staff: bool
) -> None:
    """Управление городом не предоставляет чтения закрытой истории платформы.

    Args:
        client: Клиент локального управляющего.
        staff: Есть ли доступ к входу в Django-админку без специальных разрешений.
    """
    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    user.is_staff = staff
    user.save(update_fields=["is_staff"])
    room.administrator = user
    room.save(update_fields=["administrator"])
    client.force_login(user)
    response = client.get(
        reverse(
            "admin:chat_chatarchive_change",
            args=[ChatArchive.objects.get(room=room).pk],
        )
    )
    assert response.status_code in {302, 403}


def test_archive_when_owner_purges_detached_archive_should_remove_only_it(
    client: Client,
) -> None:
    """Владелец подтверждает отдельное удаление архива уже удалённого чата.

    Args:
        client: Клиент владельца платформы.
    """
    room, user_id = _room()
    ChatMessage.objects.create(
        room=room, author_id=user_id, text="Удаляемый архив", client_id=uuid4()
    )
    archive = ChatArchive.objects.get(room=room)
    room.delete()
    owner = make_user(
        email="archive-owner@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(owner)
    url = reverse("admin:chat_chatarchive_delete", args=[archive.pk])
    preview = client.get(url)
    assert preview.status_code == 200
    assert not preview.context["perms_lacking"]
    assert client.post(url, {"post": "yes"}).status_code == 302
    assert not ChatArchive.objects.filter(pk=archive.pk).exists()
    assert not ChatArchivedMessage.objects.filter(archive_id=archive.pk).exists()
    assert not ChatMessageVersion.objects.exists()
    assert City.objects.filter(pk=room.city_id).exists()


def test_archive_when_owner_tries_to_purge_active_chat_should_require_room_deletion_first(
    client: Client,
) -> None:
    """Текущий чат не остаётся без истории из-за случайного удаления активного архива.

    Args:
        client: Клиент владельца платформы.
    """
    room, _ = _room()
    owner = make_user(
        email="archive-active-owner@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(owner)
    archive = ChatArchive.objects.get(room=room)
    assert (
        client.post(
            reverse("admin:chat_chatarchive_delete", args=[archive.pk]), {"post": "yes"}
        ).status_code
        == 403
    )
    assert ChatArchive.objects.filter(pk=archive.pk).exists()


def test_archive_when_large_and_filtered_should_paginate_and_keep_filters(
    client: Client,
) -> None:
    """Архив не растёт вместе с карточкой чата и показывает по 25 сообщений.

    Args:
        client: Клиент владельца платформы.
    """
    from apps.chat.archive import ensure_message_snapshot

    room, user_id = _room()
    for index in range(31):
        message = ChatMessage.objects.create(
            room=room,
            author_id=user_id,
            text=f"Строка архива {index}",
            client_id=uuid4(),
        )
        ensure_message_snapshot(message)
    owner = make_user(
        email="archive-page-owner@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(owner)
    archive = ChatArchive.objects.get(room=room)
    url = reverse("admin:chat_chatarchive_change", args=[archive.pk])
    first = client.get(url, {"q": "Строка архива", "author": str(user_id)})
    second = client.get(
        url, {"q": "Строка архива", "author": str(user_id), "page": "2"}
    )
    assert len(first.context["archive_messages_page"]) == 25
    assert len(second.context["archive_messages_page"]) == 6
    assert f"author={user_id}" in first.content.decode()
    card = client.get(reverse("admin:chat_chatroom_change", args=[room.pk]))
    assert "Открыть архив переписки" in card.content.decode()
    assert "Строка архива" not in card.content.decode()
    invalid = client.get(url, {"start": "2026-99-99"})
    assert invalid.status_code == 200
    assert invalid.context["archive_search_form"].errors


def test_archive_when_message_contains_markup_should_escape_all_versions(
    client: Client,
) -> None:
    """Просмотр старых текстов не выполняет присланный пользователем HTML.

    Args:
        client: Клиент владельца платформы.
    """
    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    message = services.send_message(
        user=user,
        room_id=room.pk,
        text='<script>alert("archive")</script>',
        client_id=uuid4(),
    )
    owner = make_user(
        email="archive-xss-owner@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(owner)
    saved = ChatArchivedMessage.objects.get(source_message_id=message.pk)
    response = client.get(
        reverse("admin:chat_chatarchivedmessage_change", args=[saved.pk])
    )
    assert '<script>alert("archive")</script>' not in response.content.decode()
    assert "&lt;script&gt;" in response.content.decode()


def test_archive_when_viewer_posts_changes_should_not_modify_snapshots(
    client: Client,
) -> None:
    """Право чтения не позволяет менять текст или удалять отдельные версии.

    Args:
        client: Клиент сотрудника с правом чтения.
    """
    room, user_id = _room()
    user = room.memberships.get(user_id=user_id).user
    message = services.send_message(
        user=user, room_id=room.pk, text="Неизменяемый текст", client_id=uuid4()
    )
    viewer = make_user(email="archive-readonly@test.local", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_chatarchive"))
    client.force_login(viewer)
    saved = ChatArchivedMessage.objects.get(source_message_id=message.pk)
    assert (
        client.post(
            reverse("admin:chat_chatarchivedmessage_change", args=[saved.pk]),
            {"text": "Подмена", "_save": "1"},
        ).status_code
        == 403
    )
    saved.refresh_from_db()
    assert saved.text == "Неизменяемый текст"


def test_archive_when_mixed_bulk_purge_requested_should_preserve_active_and_detached(
    client: Client,
) -> None:
    """Нельзя обойти защиту живого архива, смешав его с уже удалённым чатом.

    Args:
        client: Клиент владельца платформы.
    """
    room, _ = _room()
    active = ChatArchive.objects.get(room=room)
    other = ChatRoom.objects.create(
        city=City.objects.create(name="Удалённый чат массового архива")
    )
    detached = ChatArchive.objects.get(room=other)
    other.delete()
    owner = make_user(
        email="archive-bulk-owner@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(owner)
    response = client.post(
        reverse("admin:chat_chatarchive_changelist"),
        {
            "action": "delete_selected",
            "_selected_action": [str(active.pk), str(detached.pk)],
            "post": "yes",
        },
    )
    assert response.status_code in {200, 403}
    assert ChatArchive.objects.filter(pk__in=[active.pk, detached.pk]).count() == 2


def test_archive_when_searching_city_in_index_should_not_parse_name_as_numeric_id(
    client: Client,
) -> None:
    """Поиск города в общем списке архивов не пытается преобразовать название в число.

    Args:
        client: Клиент владельца платформы.
    """
    room, _ = _room()
    owner = make_user(
        email="archive-index-search@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(owner)
    response = client.get(
        reverse("admin:chat_chatarchive_changelist"), {"q": "Город архива"}
    )
    assert response.status_code == 200
    assert ChatArchive.objects.get(room=room) in response.context["cl"].queryset


def test_archive_when_initial_snapshot_fails_should_not_leave_empty_version_history(
    mocker: MockerFixture,
) -> None:
    """Самостоятельный перенос снимка также атомарен при отказе записи версии.

    Args:
        mocker: Изоляция отказа хранения версии.
    """
    from django.db import DatabaseError

    from apps.chat.archive import ensure_message_snapshot

    room, user_id = _room()
    message = ChatMessage.objects.create(
        room=room, author_id=user_id, text="Переносимый снимок", client_id=uuid4()
    )
    mocker.patch(
        "apps.chat.archive.ChatMessageVersion.save",
        side_effect=DatabaseError("test failure"),
    )
    with pytest.raises(DatabaseError):
        ensure_message_snapshot(message)
    assert not ChatArchivedMessage.objects.filter(source_message_id=message.pk).exists()


def test_archive_when_delete_preview_large_should_show_counts_not_entire_version_tree(
    client: Client,
) -> None:
    """Подтверждение удаления архива не разворачивает сотни версий в огромную страницу.

    Args:
        client: Клиент владельца платформы.
    """
    from django.utils import timezone

    room, user_id = _room()
    archive = ChatArchive.objects.get(room=room)
    saved = ChatArchivedMessage.objects.create(
        archive=archive,
        source_message_id=999999,
        author_reference=user_id,
        author_label="Автор",
        text="Текст",
        sent_at=timezone.now(),
    )
    ChatMessageVersion.objects.bulk_create(
        [
            ChatMessageVersion(
                message=saved,
                sequence=index + 1,
                action="edited",
                text=f"Редакция {index}",
                actor_reference=user_id,
                occurred_at=timezone.now(),
            )
            for index in range(100)
        ]
    )
    room.delete()
    owner = make_user(
        email="archive-preview-owner@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(owner)
    response = client.get(reverse("admin:chat_chatarchive_delete", args=[archive.pk]))
    assert response.status_code == 200
    assert not response.context["perms_lacking"]
    assert len(str(response.context["deleted_objects"])) < 500
    assert (
        dict(response.context["model_count"])[
            str(ChatMessageVersion._meta.verbose_name_plural)
        ]
        == 100
    )
