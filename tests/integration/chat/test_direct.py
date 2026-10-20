"""Личные диалоги: изоляция пары, явные согласия и два канала уведомлений."""

from uuid import uuid4

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.chat import services
from apps.chat.consent import accept_chat_consent, build_consent_form
from apps.chat.domain import ChatAccessError, ChatError
from apps.chat.models import ChatMembership, ChatRoom
from apps.notifications.models import Delivery, NotificationPreference
from apps.users.models import Notification, Player, User
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def _pair() -> tuple[User, User, ChatRoom]:
    from apps.chat.direct import open_direct

    first = make_user(email="direct-first@test.local", first_name="Анна")
    second = make_user(email="direct-second@test.local", first_name="Михаил")
    Player.objects.create(user=first)
    Player.objects.create(user=second)
    room = open_direct(user=first, recipient_id=second.pk)
    return first, second, room


def _accept(user: User, room: ChatRoom) -> None:
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
        user_agent="Direct test",
    )


def test_direct_when_opened_both_ways_should_reuse_pair_without_forged_consents() -> (
    None
):
    """Порядок собеседников не создаёт дубль и не подставляет согласия получателя."""
    from apps.chat.direct import open_direct

    first, second, room = _pair()
    assert open_direct(user=second, recipient_id=first.pk).pk == room.pk
    assert room.is_direct
    assert room.memberships.count() == 2
    assert not room.memberships.filter(consented_at__isnull=False).exists()
    assert ChatRoom.objects.count() == 1


def test_direct_when_self_or_disabled_target_should_reject() -> None:
    """Самодиалог и сообщения отключённому аккаунту недоступны."""
    from apps.chat.direct import open_direct

    user = make_user(email="direct-self@test.local")
    disabled = make_user(email="direct-disabled@test.local", is_active=False)
    for target in (user, disabled):
        with pytest.raises(ChatError):
            open_direct(user=user, recipient_id=target.pk)
    assert not ChatRoom.objects.exists()


def test_direct_when_outsider_even_staff_has_membership_should_deny_every_entry(
    client: Client,
) -> None:
    """Даже ошибочно добавленное участие не делает третьего человека собеседником."""
    first, _, room = _pair()
    _accept(first, room)
    outsider = make_user(
        email="direct-outsider@test.local", is_staff=True, is_superuser=True
    )
    ChatMembership.objects.create(room=room, user=outsider)
    with pytest.raises(ChatAccessError):
        _accept(outsider, room)
    with pytest.raises(ChatAccessError):
        services.history(user=outsider, room_id=room.pk)
    client.force_login(outsider)
    assert client.get(reverse("chat:join"), {"room_id": room.pk}).status_code in {
        403,
        404,
    }
    assert client.get(reverse("chat:data_conditions", args=[room.pk])).status_code in {
        403,
        404,
    }
    assert client.get(reverse("chat:management", args=[room.pk])).status_code in {
        403,
        404,
    }
    assert client.get(reverse("chat:invitations", args=[room.pk])).status_code == 403
    assert not services.my_memberships(user=outsider).filter(room=room).exists()


@override_settings(
    NOTIFICATION_CENTER_ENABLED=True, ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru"
)
def test_direct_when_message_and_reply_should_notify_only_peer_once_without_text() -> (
    None
):
    """Любое новое сообщение и ответ дают ЛК и push, без дубля и текста переписки."""
    first, second, room = _pair()
    _accept(first, room)
    NotificationPreference.objects.create(user=second, push_enabled=True)
    key = uuid4()
    message = services.send_message(
        user=first, room_id=room.pk, text="Конфиденциальный текст", client_id=key
    )
    assert (
        services.send_message(
            user=first, room_id=room.pk, text="Повтор", client_id=key
        ).pk
        == message.pk
    )
    note = Notification.objects.get(user=second, event_key="chat.direct_message")
    assert note.url == reverse("chat:room", args=[room.pk])
    assert "Конфиденциальный" not in note.message
    assert Delivery.objects.filter(notification=note, channel="push").count() == 1
    assert not Notification.objects.filter(user=first).exists()
    _accept(second, room)
    NotificationPreference.objects.create(user=first, push_enabled=True)
    services.send_message(
        user=second,
        room_id=room.pk,
        text="Ответ",
        client_id=uuid4(),
        reply_to_id=message.pk,
    )
    assert (
        Notification.objects.filter(user=first, event_key="chat.direct_message").count()
        == 1
    )
    assert not Notification.objects.filter(event_key="chat.reply").exists()
    assert Delivery.objects.filter(user=first, channel="push").count() == 1


@override_settings(NOTIFICATION_CENTER_ENABLED=True)
def test_direct_when_notifications_muted_should_respect_choice() -> None:
    """Выключенные уведомления диалога исключают обе доставки."""
    first, second, room = _pair()
    _accept(first, room)
    ChatMembership.objects.filter(room=room, user=second).update(
        notifications_enabled=False
    )
    services.send_message(
        user=first, room_id=room.pk, text="Тихое сообщение", client_id=uuid4()
    )
    assert not Notification.objects.filter(user=second).exists()
    assert not Delivery.objects.exists()


def test_direct_when_started_through_profile_should_require_post_and_csrf(
    client: Client,
) -> None:
    """Кнопка создаёт диалог POST-запросом, но GET ничего не изменяет."""
    first, second, room = _pair()
    url = reverse("chat:direct_start", args=[second.player.pk])
    client.force_login(first)
    assert client.get(url).status_code == 405
    response = client.post(url)
    assert response.status_code == 302
    assert response["Location"] == reverse("chat:room", args=[room.pk])
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(first)
    assert protected.post(url).status_code == 403


def test_direct_when_catalog_opened_should_show_only_in_private_sidebar(
    client: Client,
) -> None:
    """Диалог не попадает в общие карточки и подписан именем собеседника."""
    first, second, room = _pair()
    _accept(first, room)
    client.force_login(first)
    response = client.get(reverse("chat:index"))
    assert room.pk not in {
        card["room_id"] for card in response.context["catalog_cards"]
    }
    assert "Личные чаты" in response.content.decode()
    assert second.first_name in response.content.decode()


def test_direct_when_peer_writes_after_exit_should_not_restore_recipient_membership() -> (
    None
):
    """Повторная кнопка не отменяет выход или отзыв согласия другим человеком."""
    from apps.chat.direct import open_direct

    first, second, room = _pair()
    ChatMembership.objects.filter(room=room, user=second).update(is_active=False)
    open_direct(user=first, recipient_id=second.pk)
    assert not ChatMembership.objects.get(room=room, user=second).is_active


def test_direct_when_peer_is_staff_should_not_delete_other_authors_message() -> None:
    """Статус сотрудника не превращает личный разговор в модерируемую городскую комнату."""
    first, second, room = _pair()
    _accept(first, room)
    _accept(second, room)
    second.is_staff = second.is_superuser = True
    second.save(update_fields=["is_staff", "is_superuser"])
    message = services.send_message(
        user=first, room_id=room.pk, text="Авторское сообщение", client_id=uuid4()
    )
    with pytest.raises(ChatAccessError):
        services.delete_message(user=second, room_id=room.pk, message_id=message.pk)


def test_direct_when_player_profile_rendered_should_show_post_button_only_for_other_player(
    client: Client,
) -> None:
    """В чужом профиле видна кнопка, в собственном её нет."""
    from django.template.loader import render_to_string
    from django.test import RequestFactory

    first, second, _ = _pair()
    request = RequestFactory().get("/")
    request.user = first
    request.session = Client().session
    fragment = render_to_string(
        "includes/direct_message_button.html",
        {"target_player": second.player},
        request=request,
    )
    assert reverse("chat:direct_start", args=[second.player.pk]) in fragment
    assert 'method="post"' in fragment
    client.force_login(first)
    response = client.get(reverse("profile", args=[second.player.pk]))
    assert response.status_code == 200
    assert (
        reverse("chat:direct_start", args=[second.player.pk])
        in response.content.decode()
    )
    response = client.get(reverse("profile", args=[first.player.pk]))
    assert (
        reverse("chat:direct_start", args=[first.player.pk])
        not in response.content.decode()
    )


def test_direct_when_sparring_cards_rendered_should_offer_personal_message() -> None:
    """Контактная кнопка есть в одиночной карточке и рядом с игроками команды."""
    from types import SimpleNamespace

    from django.template.loader import render_to_string
    from django.test import RequestFactory

    from tests.integration.test_sparring_feed import _single

    first, second, _ = _pair()
    request = RequestFactory().get("/")
    request.user = first
    request.session = Client().session
    sparring = _single(second.player, "Москва")
    html = render_to_string(
        "includes/sparring_single_card.html",
        {"req": sparring, "entry": SimpleNamespace(is_local=True), "user": first},
        request=request,
    )
    assert reverse("chat:direct_start", args=[second.player.pk]) in html
    team = SimpleNamespace(
        members=SimpleNamespace(all=lambda: [SimpleNamespace(player=second.player)])
    )
    roster = render_to_string(
        "includes/sparring_doubles_roster.html",
        {"req": SimpleNamespace(author_team=team, opponent_team=None), "user": first},
        request=request,
    )
    assert reverse("chat:direct_start", args=[second.player.pk]) in roster
