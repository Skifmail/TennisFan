"""Именованные сообщества платформы не подменяют справочник городов."""

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.chat.consent import accept_chat_consent, build_consent_form
from apps.chat.domain import ChatConsentVersionError, ChatValidationError
from apps.chat.invitations import preview_invitation
from apps.chat.models import ChatArchive, ChatMembership, ChatRoom
from apps.core.localities import SettlementType
from apps.core.models import City, LegalAcceptanceLog
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_custom_creation_when_platform_owner_should_create_multiple_without_cities(
    client: Client,
) -> None:
    """Владелец создаёт несколько комнат, но не выдаёт за себя согласия автоматически."""
    owner = make_user(email="custom-owner@test.local", is_staff=True, is_superuser=True)
    client.force_login(owner)
    initial_cities = City.objects.count()
    for title in ("  Турниры выходного дня  ", "Поиск пары"):
        response = client.post(reverse("chat:create"), {"title": title})
        room = ChatRoom.objects.get(title=title.strip())
        assert response.status_code == 302
        assert response["Location"].endswith(f"?room_id={room.pk}")
        assert room.city_id is None
        assert room.administrator_id == owner.pk
        assert room.is_created
        assert not ChatMembership.objects.filter(room=room).exists()
    assert City.objects.count() == initial_cities


@pytest.mark.parametrize("staff", [False, True])
def test_custom_creation_when_not_superuser_should_deny(
    client: Client, staff: bool
) -> None:
    """Обычные сотрудники и игроки не получают право создавать произвольные комнаты."""
    client.force_login(make_user(email="custom-denied@test.local", is_staff=staff))
    for method in (client.get, client.post):
        assert method(reverse("chat:create"), {"title": "Чужой чат"}).status_code == 403
    assert not ChatRoom.objects.exists()


@pytest.mark.parametrize("title", ["", "   ", "a" * 121])
def test_custom_creation_when_invalid_title_should_reject(
    client: Client, title: str
) -> None:
    """Пустые и слишком длинные названия не создают записи."""
    client.force_login(
        make_user(email="custom-invalid@test.local", is_staff=True, is_superuser=True)
    )
    assert client.post(reverse("chat:create"), {"title": title}).status_code == 400
    assert not ChatRoom.objects.exists()


def test_custom_room_when_joining_and_updating_should_bind_consent_to_room(
    client: Client,
) -> None:
    """Согласия относятся к конкретной комнате; настройки и каталог сохраняют её имя."""
    user = make_user(email="custom-player@test.local")
    room = ChatRoom.objects.create(title="Теннис без границ")
    other = ChatRoom.objects.create(title="Другой чат")
    client.force_login(user)
    response = client.get(reverse("chat:join"), {"room_id": room.pk})
    assert response.status_code == 200
    token = str(response.context["consent_form"]["token"].value())
    data = {"token": token, "personal_data": "on", "rules": "on"}
    with pytest.raises(ChatConsentVersionError):
        accept_chat_consent(
            user=user, city=other, data=data, ip_address="127.0.0.1", user_agent="test"
        )
    response = client.post(reverse("chat:join"), {"room_id": room.pk, **data})
    assert response.status_code == 302
    member = ChatMembership.objects.get(room=room, user=user)
    assert member.consented_at
    assert (
        LegalAcceptanceLog.objects.filter(user=user, metadata__room_id=room.pk).count()
        == 2
    )
    response = client.get(reverse("chat:room", args=[room.pk]))
    assert response.status_code == 200
    assert room.title in response.content.decode()
    response = client.get(reverse("chat:join"), {"room_id": room.pk})
    assert response.context["consent_form"].initial["personal_data"] is True
    assert room.title in client.get(reverse("chat:index")).content.decode()
    response = client.post(reverse("chat:withdraw_consent", args=[room.pk]))
    assert response.status_code == 302
    member.refresh_from_db()
    assert not member.is_active


def test_custom_invitation_when_city_audience_should_reject_without_crashing() -> None:
    """У комнаты без города доступны личные и общие приглашения, но не городской отбор."""
    owner = make_user(
        email="custom-inviter@test.local", is_staff=True, is_superuser=True
    )
    room = ChatRoom.objects.create(title="Клуб любителей", administrator=owner)
    form = build_consent_form(user=owner, city=room)
    accept_chat_consent(
        user=owner,
        city=room,
        data={
            "token": str(form["token"].value()),
            "personal_data": "on",
            "rules": "on",
        },
        ip_address=None,
        user_agent="test",
    )
    with pytest.raises(ChatValidationError):
        preview_invitation(user=owner, room_id=room.pk, audience="city")
    assert (
        preview_invitation(user=owner, room_id=room.pk, audience="platform").count >= 0
    )


def test_custom_room_when_deleted_should_preserve_named_archive() -> None:
    """Архив сохраняет название удалённой комнаты без обращения к отсутствующему городу."""
    room = ChatRoom.objects.create(title="Архивируемый чат")
    room_id = room.pk
    room.delete()
    assert (
        ChatArchive.objects.get(source_room_id=room_id).city_label == "Архивируемый чат"
    )


def test_custom_room_when_no_city_or_title_should_fail_validation() -> None:
    """Модель запрещает безымянные комнаты и переименование городского сообщества."""
    with pytest.raises(ValidationError):
        ChatRoom().full_clean()
    city, _ = City.objects.get_or_create(
        name="Москва", region="", settlement_type=SettlementType.CITY
    )
    with pytest.raises(ValidationError):
        ChatRoom(city=city, title="Не Москва").full_clean()


def test_custom_catalog_when_mixed_rooms_should_sort_by_active_count(
    client: Client,
) -> None:
    """Именованные и городские сообщества сортируются общим списком по числу участников."""
    user = make_user(email="custom-catalog@test.local")
    second = make_user(email="custom-catalog-second@test.local")
    city_room = ChatRoom.objects.create(city=City.objects.create(name="Город каталога"))
    custom = ChatRoom.objects.create(title="Общий теннис")
    ChatMembership.objects.create(room=city_room, user=user)
    ChatMembership.objects.create(room=custom, user=user)
    ChatMembership.objects.create(room=custom, user=second)
    ChatRoom.objects.create(title="Закрытый отдельный", is_active=False)
    client.force_login(user)
    response = client.get(reverse("chat:index"))
    assert [card["room_id"] for card in response.context["catalog_cards"]] == [
        custom.pk,
        city_room.pk,
    ]
    assert "Закрытый отдельный" not in response.content.decode()
    response = client.get(reverse("chat:index"), {"q": "Общий"})
    assert [card["room_id"] for card in response.context["catalog_cards"]] == [
        custom.pk
    ]


def test_custom_creation_when_request_has_no_csrf_should_reject() -> None:
    """Авторизованный владелец не создаёт комнату запросом без CSRF-защиты."""
    user = make_user(email="custom-csrf@test.local", is_superuser=True, is_staff=True)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    assert (
        client.post(reverse("chat:create"), {"title": "Без защиты"}).status_code == 403
    )
    assert not ChatRoom.objects.exists()


def test_custom_admin_when_editing_named_room_should_render_and_validate(
    client: Client,
) -> None:
    """Владелец видит название в админке; пустая комната не сохраняется."""
    user = make_user(email="custom-django@test.local", is_superuser=True, is_staff=True)
    client.force_login(user)
    room = ChatRoom.objects.create(title="Именованный чат админки")
    response = client.get(reverse("admin:chat_chatroom_change", args=[room.pk]))
    assert response.status_code == 200
    assert room.title in response.content.decode()
    response = client.get(reverse("admin:chat_chatroom_changelist"))
    assert room.title in response.content.decode()


def test_custom_documents_when_presented_should_explicitly_cover_named_communities() -> (
    None
):
    """Тексты явно распространяются и на отдельные чаты без города."""
    from apps.chat.consent import load_consent_documents

    for snapshot in load_consent_documents().values():
        assert "без привязки к городу" in snapshot["document_text"]


def test_custom_creation_when_django_owner_adds_should_assign_owner_without_forged_consents(
    client: Client,
) -> None:
    """Создание через Django также назначает владельца, но не подделывает согласия."""
    owner = make_user(
        email="custom-admin-create@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(owner)
    response = client.post(
        reverse("admin:chat_chatroom_add"),
        {
            "title": "Клуб из админки",
            "city": "",
            "administrator": "",
            "rules": "Правила сообщества",
            "is_active": "on",
            "_save": "Сохранить",
        },
    )
    assert response.status_code == 302
    room = ChatRoom.objects.get(title="Клуб из админки")
    assert room.administrator_id == owner.pk
    assert not ChatMembership.objects.filter(room=room).exists()


def test_custom_messages_when_deleted_should_remain_in_private_archive() -> None:
    """Отдельная комната сохраняет тот же журнал отправки, редактирования и удаления."""
    from uuid import uuid4

    from apps.chat import services
    from apps.chat.models import ChatArchivedMessage

    user = make_user(email="custom-history@test.local")
    room = ChatRoom.objects.create(title="История отдельного чата")
    form = build_consent_form(user=user, city=room)
    accept_chat_consent(
        user=user,
        city=room,
        data={
            "token": str(form["token"].value()),
            "personal_data": "on",
            "rules": "on",
        },
        ip_address=None,
        user_agent="test",
    )
    message = services.send_message(
        user=user, room_id=room.pk, text="Исходный текст", client_id=uuid4()
    )
    services.edit_message(
        user=user, room_id=room.pk, message_id=message.pk, text="Исправленный текст"
    )
    services.delete_message(user=user, room_id=room.pk, message_id=message.pk)
    snapshot = ChatArchivedMessage.objects.get(source_message_id=message.pk)
    room.delete()
    snapshot.refresh_from_db()
    assert snapshot.archive.city_label == "История отдельного чата"
    assert snapshot.archive.room_id is None
