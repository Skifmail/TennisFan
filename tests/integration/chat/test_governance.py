"""Создание городских сообществ и разграничение управляющих ролей."""

from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse
from pytest_mock import MockerFixture

from apps.chat import services
from apps.chat.consent import (
    accept_chat_consent,
    build_consent_form,
    withdraw_chat_consent,
)
from apps.chat.domain import ChatAccessError, ChatConsentVersionError, ChatError
from apps.chat.models import ChatMembership, ChatMessage, ChatRoom
from apps.core.models import City
from apps.users.models import User
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def _accept(
    user: User, city: City | None, data: dict[str, str] | None = None
) -> ChatMembership:
    assert city is not None
    form = build_consent_form(user=user, city=city)
    return accept_chat_consent(
        user=user,
        city=city,
        data=(
            data
            if data is not None
            else {
                "token": str(form["token"].value()),
                "personal_data": "on",
                "rules": "on",
            }
        ),
        ip_address="127.0.0.1",
        user_agent="Governance test",
    )


def test_create_when_first_confirmation_should_assign_only_local_administrator() -> (
    None
):
    """Создатель получает управление комнатой, но не права сотрудника платформы."""
    user = make_user(email="creator@test.local")
    city = City.objects.create(name="Создаваемый город")
    member = _accept(user, city)
    member.room.refresh_from_db()
    assert member.room.administrator_id == user.pk
    assert member.room.is_created
    assert not member.is_moderator
    assert not user.is_staff
    assert (
        member.room.management_events.filter(action="created", actor=user).count() == 1
    )


def test_create_when_form_only_opened_should_not_create_room(client: Client) -> None:
    """Отмена формы не оставляет пустой чат.

    Args:
        client: Авторизованный тестовый HTTP-клиент.
    """
    user = make_user(email="cancel-create@test.local")
    city = City.objects.create(name="Город отмены создания")
    client.force_login(user)
    response = client.get(reverse("chat:join"), {"city_id": city.pk})
    assert "Вы станете администратором" in response.content.decode()
    assert not ChatRoom.objects.filter(city=city).exists()


def test_create_when_required_consent_missing_should_not_create_room() -> None:
    """Создание невозможно без обязательных согласий."""
    user = make_user(email="invalid-create@test.local")
    city = City.objects.create(name="Город без подтверждения")
    with pytest.raises(ChatError):
        _accept(user, city, {"rules": "on"})
    assert not ChatRoom.objects.filter(city=city).exists()


def test_create_when_two_prepared_forms_should_not_replace_first_administrator() -> (
    None
):
    """Вторая форма предлагает заново подтвердить вступление без обещания административной роли."""
    city = City.objects.create(name="Город двух создателей")
    first = make_user(email="first-create@test.local")
    second = make_user(email="second-create@test.local")
    form = build_consent_form(user=second, city=city)
    member = _accept(first, city)
    with pytest.raises(ChatConsentVersionError, match="уже создан"):
        _accept(
            second,
            city,
            {"token": str(form["token"].value()), "personal_data": "on", "rules": "on"},
        )
    assert not ChatMembership.objects.filter(room=member.room, user=second).exists()
    _accept(second, city)
    member.room.refresh_from_db()
    assert member.room.administrator_id == first.pk
    assert ChatRoom.objects.filter(city=city).count() == 1


def test_join_when_existing_legacy_room_should_not_claim_ownership() -> None:
    """Первый новый участник старого сообщества не получает власть автоматически."""
    city = City.objects.create(name="Существующее сообщество")
    room = ChatRoom.objects.create(city=city)
    _accept(make_user(email="legacy-join@test.local"), city)
    room.refresh_from_db()
    assert room.administrator_id is None


def test_create_when_closed_room_should_not_reopen_or_take_over() -> None:
    """Закрытие платформой нельзя обойти созданием."""
    city = City.objects.create(name="Закрытое сообщество")
    room = ChatRoom.objects.create(city=city, is_active=False)
    with pytest.raises(ChatError):
        _accept(make_user(email="closed-create@test.local"), city)
    room.refresh_from_db()
    assert not room.is_active
    assert room.administrator_id is None


def test_leave_when_administrator_should_require_transfer() -> None:
    """Обычный выход администратора требует передачи управления."""
    user = make_user(email="owner-leave@test.local")
    member = _accept(user, City.objects.create(name="Город выхода владельца"))
    with pytest.raises(ChatAccessError, match="передайте"):
        services.leave_room(user=user, room_id=member.room_id)
    member.refresh_from_db()
    assert member.is_active


def test_withdraw_when_administrator_should_exit_and_release_ownership() -> None:
    """Отзыв согласий не блокируется обязанностью передать управление."""
    user = make_user(email="owner-withdraw@test.local")
    member = _accept(user, City.objects.create(name="Город отзыва владельца"))
    withdraw_chat_consent(
        user=user, room_id=member.room_id, ip_address=None, user_agent="test"
    )
    member.refresh_from_db()
    member.room.refresh_from_db()
    assert not member.is_active
    assert member.room.administrator_id is None
    assert member.room.management_events.filter(action="released").exists()


def test_roles_when_administrator_assigns_and_transfers_should_audit_and_preserve_privacy(
    client: Client,
) -> None:
    """Владелец назначает модератора и передаёт управление без выдачи email.

    Args:
        client: Авторизованный тестовый HTTP-клиент.
    """
    user = make_user(email="role-owner@test.local")
    member = _accept(user, City.objects.create(name="Город передачи"))
    target = make_user(email="role-target@test.local")
    target_member = _accept(target, member.room.city)
    client.force_login(user)
    url = reverse("chat:management", args=[member.room_id])
    response = client.get(url)
    assert response.status_code == 200
    assert target.email not in response.content.decode()
    assert (
        client.post(
            url, {"operation": "moderator", "target_id": target.pk, "enabled": "1"}
        ).status_code
        == 302
    )
    target_member.refresh_from_db()
    assert target_member.is_moderator
    assert (
        client.post(url, {"operation": "transfer", "target_id": target.pk}).status_code
        == 302
    )
    member.room.refresh_from_db()
    assert member.room.administrator_id == target.pk
    assert member.room.management_events.filter(
        action="transferred", actor=user, target=target
    ).exists()
    services.leave_room(user=user, room_id=member.room_id)


@pytest.mark.parametrize("moderator", [False, True])
def test_roles_when_not_administrator_should_deny_management(
    client: Client, moderator: bool
) -> None:
    """Ни участник, ни модератор не назначают управляющих.

    Args:
        client: Авторизованный тестовый HTTP-клиент.
        moderator: Есть ли у проверяемого участника права модератора.
    """
    owner = make_user(email="role-deny-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город запрета ролей"))
    user = make_user(email="role-deny-user@test.local")
    other = _accept(user, member.room.city)
    other.is_moderator = moderator
    other.save(update_fields=["is_moderator"])
    client.force_login(user)
    url = reverse("chat:management", args=[member.room_id])
    assert client.get(url).status_code == 403
    assert (
        client.post(url, {"operation": "transfer", "target_id": user.pk}).status_code
        == 403
    )


def test_moderation_when_administrator_not_moderator_should_allow_delete(
    mocker: MockerFixture,
) -> None:
    """Администратор модерирует без дополнительного флага модератора.

    Args:
        mocker: Изоляция публикации внешних событий.
    """
    mocker.patch("apps.chat.services.publish_room_change")
    owner = make_user(email="moderate-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город прав владельца"))
    author = make_user(email="moderate-author@test.local")
    _accept(author, member.room.city)
    message = ChatMessage.objects.create(
        room=member.room, author=author, text="test", client_id=uuid4()
    )
    services.delete_message(user=owner, room_id=member.room_id, message_id=message.pk)
    message.refresh_from_db()
    assert message.deleted_at is not None


def test_directory_when_room_absent_existing_closed_should_show_distinct_actions(
    client: Client,
) -> None:
    """В каталоге создание, вступление и закрытый чат имеют разные действия.

    Args:
        client: Авторизованный тестовый HTTP-клиент.
    """
    client.force_login(make_user(email="directory-roles@test.local"))
    city = City.objects.create(name="Город нового чата")
    response = client.get(reverse("chat:index"), {"q": city.name})
    assert "Создать чат" in response.content.decode()
    room = ChatRoom.objects.create(city=city)
    response = client.get(reverse("chat:index"), {"q": city.name})
    assert "Вступить" in response.content.decode()
    room.is_active = False
    room.save(update_fields=["is_active"])
    response = client.get(reverse("chat:index"), {"q": city.name})
    assert "Чат закрыт" in response.content.decode()
    assert "data-chat-join" not in response.content.decode()


@pytest.mark.parametrize("invalid", ["outsider", "inactive", "withdrawn", "other_city"])
def test_transfer_when_target_invalid_should_reject(
    client: Client, invalid: str
) -> None:
    """Управление нельзя передать постороннему или участнику без согласий.

    Args:
        client: Клиент администратора.
        invalid: Недопустимое состояние получателя.
    """
    owner = make_user(email="invalid-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город проверки цели"))
    target = make_user(email="invalid-target@test.local")
    if invalid != "outsider":
        target_member = _accept(
            target,
            (
                City.objects.create(name="Другой город")
                if invalid == "other_city"
                else member.room.city
            ),
        )
        if invalid == "inactive":
            target.is_active = False
            target.save(update_fields=["is_active"])
        elif invalid == "withdrawn":
            withdraw_chat_consent(
                user=target,
                room_id=target_member.room_id,
                ip_address=None,
                user_agent="test",
            )
    client.force_login(owner)
    response = client.post(
        reverse("chat:management", args=[member.room_id]),
        {"operation": "transfer", "target_id": target.pk},
    )
    assert response.status_code == 403
    member.room.refresh_from_db()
    assert member.room.administrator_id == owner.pk


def test_management_when_csrf_missing_should_reject() -> None:
    """Даже администратор не может менять роли без CSRF-подтверждения."""
    owner = make_user(email="csrf-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город CSRF ролей"))
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    assert (
        client.post(
            reverse("chat:management", args=[member.room_id]),
            {"operation": "transfer", "target_id": owner.pk},
        ).status_code
        == 403
    )


def test_moderation_when_moderator_targets_administrator_should_deny() -> None:
    """Модератор не может ограничить администратора своей комнаты."""
    owner = make_user(email="protected-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город защищённого владельца"))
    moderator = make_user(email="protected-mod@test.local")
    target = _accept(moderator, member.room.city)
    target.is_moderator = True
    target.save(update_fields=["is_moderator"])
    with pytest.raises(ChatAccessError):
        services.moderate_member(
            user=moderator, room_id=member.room_id, target_id=owner.pk, banned=True
        )


def test_platform_when_owner_inactive_should_find_orphan_and_assign_replacement(
    client: Client,
) -> None:
    """Платформа видит потерянный чат и назначает действующего участника.

    Args:
        client: Авторизованный тестовый HTTP-клиент.
    """
    owner = make_user(email="orphan-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город без владельца"))
    target = make_user(email="orphan-replacement@test.local")
    _accept(target, member.room.city)
    owner.is_active = False
    owner.save(update_fields=["is_active"])
    staff = make_user(
        email="orphan-platform@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    response = client.get(
        reverse("admin:chat_chatroom_changelist"), {"administrator_state": "missing"}
    )
    assert response.status_code == 200
    assert member.room in response.context["cl"].queryset
    response = client.post(
        reverse("chat:management", args=[member.room_id]),
        {"operation": "transfer", "target_id": target.pk},
    )
    assert response.status_code == 302
    member.room.refresh_from_db()
    assert member.room.administrator_id == target.pk
    assert member.room.management_events.filter(action="assigned", actor=staff).exists()


def test_admin_when_assigning_outsider_should_reject_form() -> None:
    """Django-форма также запрещает назначать невступившего пользователя."""
    from apps.chat.admin import ChatRoomForm

    owner = make_user(email="form-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город админской формы"))
    outsider = make_user(email="form-outsider@test.local")
    form = ChatRoomForm(
        data={
            "city": member.room.city_id,
            "is_active": True,
            "is_created": True,
            "administrator": outsider.pk,
            "rules": "Правила",
        },
        instance=member.room,
    )
    assert not form.is_valid()
    assert "administrator" in form.errors


def test_badge_when_administrator_transferred_should_update_history_roles() -> None:
    """История использует актуальные роли, а не роль автора в момент отправки."""
    from apps.chat.governance import change_role

    owner = make_user(email="badge-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город бейджа администратора"))
    replacement = make_user(email="badge-replacement@test.local")
    _accept(replacement, member.room.city)
    message = ChatMessage.objects.create(
        room=member.room, author=owner, text="История", client_id=uuid4()
    )
    assert services.serialize_message(message)["author_is_administrator"]
    change_role(
        user=owner,
        room_id=member.room_id,
        target_id=replacement.pk,
        operation="transfer",
    )
    assert not services.serialize_message(message)["author_is_administrator"]
    assert not services.serialize_message(message)["author_is_moderator"]
    payload = services.sync_events(user=replacement, room_id=member.room_id, after_id=0)
    assert payload["administrator_id"] == replacement.pk
    assert payload["can_moderate"]


def test_join_when_low_level_called_for_absent_room_should_require_explicit_creation() -> (
    None
):
    """Низкоуровневое вступление не создаёт комнату в обход формы согласий."""
    user = make_user(email="no-implicit-create@test.local")
    city = City.objects.create(name="Город явного создания")
    with pytest.raises(ChatError):
        services.join_city(user=user, city_id=city.pk)
    assert not ChatRoom.objects.filter(city=city).exists()


def test_admin_when_valid_participant_assigned_should_save_and_audit(
    client: Client,
) -> None:
    """Назначение через поле Django-админки сохраняется и попадает в историю.

    Args:
        client: Клиент уполномоченного сотрудника платформы.
    """
    original = make_user(email="admin-form-original@test.local")
    member = _accept(original, City.objects.create(name="Город назначения из админки"))
    target = make_user(email="admin-form-target@test.local")
    _accept(target, member.room.city)
    staff = make_user(
        email="admin-form-staff@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    response = client.post(
        reverse("admin:chat_chatroom_change", args=[member.room_id]),
        {
            "city": member.room.city_id,
            "administrator": target.pk,
            "is_active": "on",
            "rules": member.room.rules,
            "_save": "1",
        },
    )
    assert response.status_code == 302
    member.room.refresh_from_db()
    assert member.room.administrator_id == target.pk
    assert member.room.is_created
    assert member.room.management_events.filter(
        action="assigned", actor=staff, target=target
    ).exists()


def test_administrator_when_account_deleted_should_preserve_room_without_auto_successor() -> (
    None
):
    """Удаление аккаунта освобождает ссылку, не удаляя сообщество и его участников."""
    owner = make_user(email="deleted-owner@test.local")
    member = _accept(owner, City.objects.create(name="Город удалённого владельца"))
    other = make_user(email="deleted-owner-other@test.local")
    _accept(other, member.room.city)
    room = member.room
    owner.delete()
    room.refresh_from_db()
    assert room.administrator_id is None
    assert room.memberships.filter(user=other, is_active=True).exists()
    assert room.management_events.filter(action="created").exists()
