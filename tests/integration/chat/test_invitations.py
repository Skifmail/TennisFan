"""Приглашения не добавляют в чат и не позволяют обходить роли или согласия."""

import pytest
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse
from pytest_mock import MockerFixture

from apps.chat import invitations, services
from apps.chat.domain import ChatAccessError, ChatValidationError
from apps.chat.models import (
    ChatInvitation,
    ChatInvitationCampaign,
    ChatInvitationEmailBlock,
    ChatMembership,
    ChatRoom,
)
from apps.core.models import City
from apps.users.models import Notification, Player, User
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def _community() -> tuple[User, ChatRoom]:
    owner = make_user(email="invite-owner@test.local", first_name="Администратор")
    room = join_city_with_consent(
        user=owner, city_id=City.objects.create(name="Казань").pk
    )
    room.administrator = owner
    room.save(update_fields=["administrator"])
    return owner, room


def _queue(
    owner: User, room: ChatRoom, audience: str, target: User | None = None
) -> ChatInvitationCampaign:
    preview = invitations.preview_invitation(
        user=owner,
        room_id=room.pk,
        audience=audience,
        target_id=target.pk if target else None,
    )
    return invitations.queue_invitation(
        user=owner, room_id=room.pk, token=preview.token
    )


def test_invitation_when_personal_should_notify_and_email_without_joining(
    mocker: MockerFixture,
) -> None:
    """Оба канала отправляются один раз, а участие и согласия не подставляются.

    Args:
        mocker: Изоляция реальной почты.
    """
    send = mocker.patch(
        "apps.chat.invitations.send_invitation_email", return_value=True
    )
    owner, room = _community()
    target = make_user(email="invite-person@test.local", first_name="Иван")
    campaign = _queue(owner, room, "person", target)
    assert not Notification.objects.filter(user=target).exists()
    assert not ChatInvitation.objects.exists()
    invitations.process_invitation_queue()
    invitations.process_invitation_queue()
    invite = ChatInvitation.objects.get(room=room, recipient=target)
    assert invite.email_status == "sent"
    assert (
        Notification.objects.filter(
            user=target, url=reverse("chat:invitation", args=[invite.pk])
        ).count()
        == 1
    )
    assert not ChatMembership.objects.filter(room=room, user=target).exists()
    assert send.call_count == 1
    campaign.refresh_from_db()
    assert campaign.status == "done"
    assert campaign.created_count == 1


@pytest.mark.parametrize("audience", ["city", "platform"])
def test_invitation_when_bulk_should_select_only_eligible_players(
    audience: str, mocker: MockerFixture
) -> None:
    """Город берётся из профиля, участники и отключённые аккаунты исключены.

    Args:
        audience: Способ массового отбора.
        mocker: Изоляция SMTP.
    """
    mocker.patch("apps.chat.invitations.send_invitation_email", return_value=True)
    owner, room = _community()
    local = make_user(email="invite-local@test.local")
    Player.objects.create(user=local, city="г. Казань")
    other = make_user(email="invite-other@test.local")
    Player.objects.create(user=other, city="Омск")
    inactive = make_user(email="invite-inactive@test.local", is_active=False)
    Player.objects.create(user=inactive, city="Казань")
    campaign = _queue(owner, room, audience)
    later = make_user(email="invite-created-later@test.local")
    Player.objects.create(user=later, city="Казань")
    invitations.process_invitation_queue(user_batch_size=1)
    for _ in range(5):
        invitations.process_invitation_queue(user_batch_size=1)
    expected = {local.pk} if audience == "city" else {local.pk, other.pk}
    assert (
        set(
            ChatInvitation.objects.filter(campaign=campaign).values_list(
                "recipient_id", flat=True
            )
        )
        == expected
    )


def test_invitation_when_duplicate_confirmation_or_overlapping_batches_should_not_duplicate(
    mocker: MockerFixture,
) -> None:
    """Повтор POST и пересекающиеся аудитории не дублируют приглашение.

    Args:
        mocker: Изоляция почты.
    """
    mocker.patch("apps.chat.invitations.send_invitation_email", return_value=True)
    owner, room = _community()
    target = make_user(email="invite-duplicate@test.local")
    preview = invitations.preview_invitation(
        user=owner, room_id=room.pk, audience="platform"
    )
    first = invitations.queue_invitation(
        user=owner, room_id=room.pk, token=preview.token
    )
    assert (
        invitations.queue_invitation(
            user=owner, room_id=room.pk, token=preview.token
        ).pk
        == first.pk
    )
    _queue(owner, room, "person", target)
    for _ in range(3):
        invitations.process_invitation_queue()
    assert ChatInvitation.objects.filter(recipient=target, room=room).count() == 1
    assert Notification.objects.filter(user=target).count() == 1


def test_invitation_when_moderator_should_have_access_but_regular_member_should_not() -> (
    None
):
    """Право приглашать есть у модератора, но не у обычного участника."""
    owner, room = _community()
    member = make_user(email="invite-member@test.local")
    join_city_with_consent(user=member, city_id=room.city_id)
    with pytest.raises(ChatAccessError):
        invitations.preview_invitation(
            user=member, room_id=room.pk, audience="platform"
        )
    ChatMembership.objects.filter(user=member, room=room).update(is_moderator=True)
    assert (
        invitations.preview_invitation(
            user=member, room_id=room.pk, audience="platform"
        ).count
        == 0
    )
    assert invitations.can_invite(user=owner, room_id=room.pk)


@pytest.mark.parametrize("change", ["role", "closed", "recipient_joined"])
def test_invitation_when_access_changes_before_delivery_should_skip(
    change: str, mocker: MockerFixture
) -> None:
    """Очередь повторно проверяет права и участие непосредственно перед доставкой.

    Args:
        change: Изменение после подтверждения рассылки.
        mocker: Изоляция SMTP.
    """
    send = mocker.patch(
        "apps.chat.invitations.send_invitation_email", return_value=True
    )
    owner, room = _community()
    target = make_user(email="invite-race@test.local")
    _queue(owner, room, "person", target)
    if change == "role":
        room.administrator = None
        room.save(update_fields=["administrator"])
    elif change == "closed":
        room.is_active = False
        room.save(update_fields=["is_active"])
    else:
        join_city_with_consent(user=target, city_id=room.city_id)
    invitations.process_invitation_queue()
    assert not Notification.objects.filter(user=target).exists()
    send.assert_not_called()


@pytest.mark.parametrize("reason", ["unverified", "blocked", "failure"])
def test_invitation_when_email_unavailable_should_keep_internal_notification(
    reason: str, mocker: MockerFixture
) -> None:
    """Отказ от email и ошибка SMTP не отменяют внутреннее приглашение.

    Args:
        reason: Причина отсутствия доставленного письма.
        mocker: Изоляция SMTP.
    """
    send = mocker.patch(
        "apps.chat.invitations.send_invitation_email", return_value=False
    )
    owner, room = _community()
    target = make_user(
        email="invite-email@test.local", email_verified=reason != "unverified"
    )
    if reason == "blocked":
        ChatInvitationEmailBlock.objects.create(user=target)
    _queue(owner, room, "person", target)
    invitations.process_invitation_queue()
    invitations.process_invitation_queue()
    invite = ChatInvitation.objects.get(recipient=target)
    assert invite.email_status == ("failed" if reason == "failure" else "skipped")
    assert Notification.objects.filter(user=target).count() == 1
    assert send.call_count == (1 if reason == "failure" else 0)


def test_invitation_when_preview_tampered_or_used_by_other_account_should_reject() -> (
    None
):
    """Подписанное подтверждение привязано к комнате, автору и сроку."""
    owner, room = _community()
    other = make_user(email="invite-forger@test.local")
    join_city_with_consent(user=other, city_id=room.city_id)
    ChatMembership.objects.filter(user=other).update(is_moderator=True)
    preview = invitations.preview_invitation(
        user=owner, room_id=room.pk, audience="platform"
    )
    for actor, token in ((owner, preview.token + "bad"), (other, preview.token)):
        with pytest.raises(ChatValidationError):
            invitations.queue_invitation(user=actor, room_id=room.pk, token=token)
    assert not ChatInvitationCampaign.objects.exists()


def test_invitation_when_searching_should_not_expose_contacts_or_members(
    client: Client,
) -> None:
    """В поиске нет email, телефона и уже вступивших пользователей.

    Args:
        client: HTTP-клиент администратора.
    """
    owner, room = _community()
    target = make_user(
        email="private-invitation-address@test.local",
        first_name="Иван",
        last_name="Петров",
        phone="+79999990101",
    )
    client.force_login(owner)
    response = client.get(
        reverse("chat:invitations", args=[room.pk]), {"q": "Иван Петров"}
    )
    assert response.status_code == 200
    assert "Иван Петров" in response.content.decode()
    assert target.email not in response.content.decode()
    assert target.phone not in response.content.decode()
    assert not ChatInvitationCampaign.objects.exists()


def test_invitation_when_recipient_opens_should_offer_consents_and_reject_other_account(
    client: Client, mocker: MockerFixture
) -> None:
    """Переход не вступает за пользователя и закрыт от чужого аккаунта.

    Args:
        client: HTTP-клиент получателя.
        mocker: Изоляция SMTP.
    """
    mocker.patch("apps.chat.invitations.send_invitation_email", return_value=True)
    owner, room = _community()
    target = make_user(email="invite-landing@test.local")
    _queue(owner, room, "person", target)
    invitations.process_invitation_queue()
    invite = ChatInvitation.objects.get(recipient=target)
    url = reverse("chat:invitation", args=[invite.pk])
    client.force_login(owner)
    assert client.get(url).status_code == 404
    client.force_login(target)
    response = client.get(url)
    assert response.status_code == 200
    assert (
        reverse("chat:join") + f"?city_id={room.city_id}" in response.content.decode()
    )
    assert not ChatMembership.objects.filter(user=target, room=room).exists()
    join_city_with_consent(user=target, city_id=room.city_id)
    invite.refresh_from_db()
    assert invite.status == "accepted"


def test_invitation_when_declined_or_email_unsubscribed_should_require_explicit_post(
    client: Client, mocker: MockerFixture
) -> None:
    """Просмотр ссылки не отписывает, POST отписывает только от приглашений.

    Args:
        client: HTTP-клиент получателя.
        mocker: Изоляция SMTP.
    """
    mocker.patch("apps.chat.invitations.send_invitation_email", return_value=True)
    owner, room = _community()
    target = make_user(email="invite-decline@test.local")
    _queue(owner, room, "person", target)
    invitations.process_invitation_queue()
    invite = ChatInvitation.objects.get(recipient=target)
    token = invitations.invitation_email_token(user_id=target.pk)
    url = reverse("chat:invitation_email_settings", args=[token])
    assert client.get(url).status_code == 200
    assert not ChatInvitationEmailBlock.objects.filter(user=target).exists()
    assert client.post(url).status_code == 200
    assert ChatInvitationEmailBlock.objects.filter(user=target).exists()
    assert not ChatInvitationEmailBlock.objects.filter(user=owner).exists()
    assert (
        client.post(reverse("chat:invitation_email_settings", args=["bad"])).status_code
        == 400
    )
    client.force_login(target)
    client.post(reverse("chat:invitation", args=[invite.pk]), {"operation": "decline"})
    invite.refresh_from_db()
    assert invite.status == "declined"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_invitation_when_email_sent_should_include_safe_links_and_no_recipient_list() -> (
    None
):
    """Письмо содержит персональную ссылку, отказ и отдельного получателя."""
    owner, room = _community()
    target = make_user(email="invite-real-locmem@test.local")
    _queue(owner, room, "person", target)
    invitations.process_invitation_queue()
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [target.email]
    assert not message.cc and not message.bcc
    assert "Казань" in message.body
    assert "/chats/invitations/" in message.body
    assert "/chats/invitation-email/" in message.body
    assert "не добавляет" in message.body


def test_invitation_when_confirmation_posted_should_queue_only_after_preview(
    client: Client,
) -> None:
    """Массовая отправка требует явного второго подтверждения.

    Args:
        client: Клиент администратора.
    """
    owner, room = _community()
    make_user(email="invite-confirm@test.local")
    client.force_login(owner)
    url = reverse("chat:invitations", args=[room.pk])
    response = client.post(url, {"operation": "preview", "audience": "platform"})
    assert response.status_code == 200
    assert response.context["preview"].count == 1
    assert not ChatInvitationCampaign.objects.exists()
    token = response.context["preview"].token
    assert client.post(url, {"operation": "confirm", "token": token}).status_code == 302
    assert ChatInvitationCampaign.objects.count() == 1
    assert not ChatInvitation.objects.exists()


def test_invitation_when_preview_expired_should_reject(
    mocker: MockerFixture,
) -> None:
    """Нельзя подтвердить просроченную аудиторию.

    Args:
        mocker: Управление временем подписанного подтверждения.
    """
    owner, room = _community()
    target = make_user(email="invite-expired@test.local")
    preview = invitations.preview_invitation(
        user=owner, room_id=room.pk, audience="person", target_id=target.pk
    )
    from time import time

    mocker.patch(
        "django.core.signing.time.time",
        return_value=time() + invitations.PREVIEW_MAX_AGE + 1,
    )
    with pytest.raises(ChatValidationError):
        invitations.queue_invitation(user=owner, room_id=room.pk, token=preview.token)
    assert not ChatInvitationCampaign.objects.exists()


def test_invitation_when_recipient_joins_or_sender_loses_role_after_expansion_should_not_email(
    mocker: MockerFixture,
) -> None:
    """Уже созданное уведомление не заставляет отправить ставшее неактуальным письмо.

    Args:
        mocker: Изоляция почтового транспорта.
    """
    send = mocker.patch(
        "apps.chat.invitations.send_invitation_email", return_value=True
    )
    owner, room = _community()
    target = make_user(email="invite-post-expansion@test.local")
    _queue(owner, room, "person", target)
    invitations.process_invitation_queue(email_limit=0)
    assert Notification.objects.filter(user=target).exists()
    room.administrator = None
    room.save(update_fields=["administrator"])
    invitations.process_invitation_queue()
    assert (
        ChatInvitation.objects.get(recipient=target).email_reason
        == "sender_access_lost"
    )
    send.assert_not_called()


def test_invitation_when_chat_deleted_should_cancel_queue_and_remove_linked_notifications(
    mocker: MockerFixture,
) -> None:
    """После удаления чата не остаётся работающих приглашений или фоновых писем.

    Args:
        mocker: Изоляция SMTP.
    """
    send = mocker.patch(
        "apps.chat.invitations.send_invitation_email", return_value=True
    )
    owner, room = _community()
    target = make_user(email="invite-deleted-room@test.local")
    _queue(owner, room, "person", target)
    invitations.process_invitation_queue(email_limit=0)
    room.delete()
    invitations.process_invitation_queue()
    assert not ChatInvitation.objects.exists()
    assert not ChatInvitationCampaign.objects.exists()
    assert not Notification.objects.filter(user=target).exists()
    send.assert_not_called()


def test_invitation_when_post_without_csrf_or_regular_member_should_reject(
    client: Client,
) -> None:
    """Скрытая кнопка не заменяет проверку роли и CSRF на сервере.

    Args:
        client: Клиент обычного участника.
    """
    owner, room = _community()
    target = make_user(email="invite-access@test.local")
    join_city_with_consent(user=target, city_id=room.city_id)
    client.force_login(target)
    url = reverse("chat:invitations", args=[room.pk])
    assert client.get(url).status_code == 403
    assert (
        client.post(url, {"operation": "preview", "audience": "platform"}).status_code
        == 403
    )
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(owner)
    assert (
        protected.post(
            url, {"operation": "preview", "audience": "platform"}
        ).status_code
        == 403
    )
    assert not ChatInvitationCampaign.objects.exists()


def test_invitation_when_worker_crashes_after_smtp_should_not_repeat_uncertain_email(
    mocker: MockerFixture,
) -> None:
    """Неопределённый результат доставки не превращается в повторные письма.

    Args:
        mocker: Моделирование остановки процесса во время SMTP.
    """
    send = mocker.patch(
        "apps.chat.invitations.send_invitation_email",
        side_effect=RuntimeError("simulated process termination"),
    )
    owner, room = _community()
    target = make_user(email="invite-uncertain@test.local")
    _queue(owner, room, "person", target)
    with pytest.raises(RuntimeError):
        invitations.process_invitation_queue()
    assert ChatInvitation.objects.get(recipient=target).email_status == "sending"
    invitations.process_invitation_queue()
    assert send.call_count == 1


def test_invitation_when_worker_command_registered_should_process_only_local_queue(
    mocker: MockerFixture,
) -> None:
    """Штатный планировщик вызывает ограниченный обработчик каждую минуту.

    Args:
        mocker: Изоляция фонового обработчика.
    """
    from django.conf import settings as django_settings
    from django.core.management import call_command

    worker = mocker.patch(
        "apps.chat.management.commands.process_chat_invitations.process_invitation_queue"
    )
    call_command("process_chat_invitations")
    worker.assert_called_once_with()
    assert (
        "* * * * *",
        "django.core.management.call_command",
        ["process_chat_invitations"],
    ) in django_settings.CRONJOBS


def test_invitation_when_staff_opens_journal_should_show_paginated_delivery_states(
    client: Client, mocker: MockerFixture
) -> None:
    """Журнал доступен уполномоченному сотруднику, а не местной роли.

    Args:
        client: HTTP-клиент сотрудника.
        mocker: Изоляция SMTP.
    """
    mocker.patch("apps.chat.invitations.send_invitation_email", return_value=True)
    owner, room = _community()
    target = make_user(email="invite-journal@test.local")
    campaign = _queue(owner, room, "person", target)
    invitations.process_invitation_queue()
    platform_owner = make_user(
        email="invite-platform-owner@test.local", is_staff=True, is_superuser=True
    )
    url = reverse("admin:chat_chatinvitationcampaign_change", args=[campaign.pk])
    client.force_login(platform_owner)
    response = client.get(url)
    assert response.status_code == 200
    assert len(response.context["invitation_page"]) == 1
    assert "Принято почтовым сервисом" in response.content.decode()
    client.force_login(owner)
    assert client.get(url).status_code == 302


def test_invitation_when_wrong_room_or_empty_scope_should_not_queue() -> None:
    """Подтверждение привязано к комнате и не запускает пустые рассылки."""
    owner, room = _community()
    empty = invitations.preview_invitation(
        user=owner, room_id=room.pk, audience="platform"
    )
    assert empty.count == 0
    with pytest.raises(ChatValidationError):
        invitations.queue_invitation(user=owner, room_id=room.pk, token=empty.token)
    target = make_user(email="invite-wrong-room@test.local")
    preview = invitations.preview_invitation(
        user=owner, room_id=room.pk, audience="person", target_id=target.pk
    )
    other = join_city_with_consent(
        user=owner, city_id=City.objects.create(name="Другая комната приглашений").pk
    )
    other.administrator = owner
    other.save(update_fields=["administrator"])
    with pytest.raises(ChatValidationError):
        invitations.queue_invitation(user=owner, room_id=other.pk, token=preview.token)
    assert not ChatInvitationCampaign.objects.exists()


def test_invitation_when_admin_deletes_room_should_remove_queue_without_journal_permission_block(
    client: Client,
) -> None:
    """Защита журнала от прямого удаления не мешает владельцу удалить сам чат.

    Args:
        client: Клиент владельца платформы.
    """
    owner, room = _community()
    target = make_user(email="invite-admin-delete-target@test.local")
    campaign = _queue(owner, room, "person", target)
    platform_owner = make_user(
        email="invite-admin-delete-owner@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(platform_owner)
    assert (
        client.post(
            reverse("admin:chat_chatinvitationcampaign_delete", args=[campaign.pk]),
            {"post": "yes"},
        ).status_code
        == 403
    )
    response = client.get(reverse("admin:chat_chatroom_delete", args=[room.pk]))
    assert not response.context["perms_lacking"]
    assert (
        client.post(
            reverse("admin:chat_chatroom_delete", args=[room.pk]), {"post": "yes"}
        ).status_code
        == 302
    )
    assert not ChatInvitationCampaign.objects.filter(pk=campaign.pk).exists()


@pytest.mark.parametrize("role", ["owner", "moderator", "member"])
def test_invitation_when_room_opened_should_show_control_only_to_local_managers(
    client: Client, role: str
) -> None:
    """Кнопка настроек и обновления роли согласованы с серверным разрешением.

    Args:
        client: Клиент игрока.
        role: Проверяемая роль в комнате.
    """
    owner, room = _community()
    user = owner
    if role != "owner":
        user = make_user(email="invite-room-control@test.local")
        join_city_with_consent(user=user, city_id=room.city_id)
        ChatMembership.objects.filter(user=user, room=room).update(
            is_moderator=role == "moderator"
        )
    client.force_login(user)
    response = client.get(reverse("chat:room", args=[room.pk]))
    assert response.context["can_invite_chat"] is (role != "member")
    assert services.sync_events(user=user, room_id=room.pk, after_id=0)[
        "can_invite"
    ] is (role != "member")


@override_settings(
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
)
def test_invitation_when_logged_backend_sends_should_record_copy_and_bound_timeout(
    mocker: MockerFixture,
) -> None:
    """Письмо использует штатный журнал и ограничение ожидания SMTP.

    Args:
        mocker: Наблюдение за выбором почтового backend без настоящей сети.
    """
    from apps.chat.invitation_email import SMTP_TIMEOUT_SECONDS
    from apps.core import email_service
    from apps.core.models import OutboundEmail

    backend = mocker.spy(email_service, "get_connection")
    owner, room = _community()
    target = make_user(email="invite-logged-copy@test.local")
    _queue(owner, room, "person", target)
    invitations.process_invitation_queue()
    row = OutboundEmail.objects.get(to_email=target.email)
    assert row.status == OutboundEmail.Status.SENT
    assert row.user_id == target.pk
    assert "/chats/invitations/" in row.body_html
    assert "/chats/invitation-email/" in row.body_text
    assert len(mail.outbox) == 1
    backend.assert_called_once_with(timeout=SMTP_TIMEOUT_SECONDS)
