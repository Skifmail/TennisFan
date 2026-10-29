"""Полное удаление аккаунтов через подтверждение в административной панели."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.content.models import StringerApplication
from apps.notifications.models import Delivery, NotificationPreference, PushDevice
from apps.tournaments.models import Match, SparringDeadlineExtension
from apps.users.models import Notification, Player, SocialIdentity, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def account() -> User:
    """Создаёт аккаунт с записями, ранее блокировавшими удаление."""
    user = User.objects.create_user(email="delete-me@example.test")
    NotificationPreference.objects.create(user=user)
    note = Notification.objects.create(user=user, message="Тестовое уведомление")
    now = timezone.now()
    Delivery.objects.create(
        user=user,
        notification=note,
        event_key="test",
        channel="email",
        available_at=now,
        expires_at=now + timedelta(days=1),
    )
    PushDevice.objects.create(
        user=user, device_hash="a" * 64, expires_at=now + timedelta(days=1)
    )
    SocialIdentity.objects.create(user=user, provider="yandex", subject="delete-me")
    player = Player.objects.create(user=user)
    opponent = Player.objects.create(
        user=User.objects.create_user(email="opponent@example.test")
    )
    match = Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=player,
        player2=opponent,
        deadline=now,
    )
    # У обоих участников есть продление: удаление матча очищает обе записи.
    for participant in (player, opponent):
        SparringDeadlineExtension.objects.create(
            match=match,
            player=participant,
            old_deadline=now,
            new_deadline=now + timedelta(days=7),
        )
    StringerApplication.objects.create(
        applicant=user,
        name="Мастерская",
        email="company@example.test",
        phone="+79991234567",
    )
    return user


@pytest.mark.parametrize("bulk", [False, True])
def test_superuser_deletes_account_after_confirmation(
    client: Client, account: User, bulk: bool
) -> None:
    """Одиночное и массовое удаление очищают аккаунт после подтверждения."""
    moderator = User.objects.create_superuser(email="admin@example.test")
    client.force_login(moderator)
    url = (
        reverse("admin:users_user_changelist")
        if bulk
        else reverse("admin:users_user_delete", args=[account.pk])
    )
    data = {"action": "delete_selected", "_selected_action": [str(account.pk)]}
    preview = (
        client.post(url, data, secure=True) if bulk else client.get(url, secure=True)
    )
    assert preview.status_code == 200
    assert not preview.context["perms_lacking"]
    assert not preview.context["protected"]
    assert User.objects.filter(pk=account.pk).exists()

    response = client.post(url, {**data, "post": "yes"}, secure=True)
    assert response.status_code == 302
    assert not User.objects.filter(pk=account.pk).exists()
    assert not Player.objects.filter(user_id=account.pk).exists()
    assert not Notification.objects.filter(user_id=account.pk).exists()
    assert not Delivery.objects.filter(user_id=account.pk).exists()
    assert not NotificationPreference.objects.filter(user_id=account.pk).exists()
    assert not PushDevice.objects.filter(user_id=account.pk).exists()
    assert not SocialIdentity.objects.filter(user_id=account.pk).exists()
    assert not SparringDeadlineExtension.objects.exists()
    assert not Match.objects.exists()
    assert User.objects.filter(email="opponent@example.test").exists()
    assert StringerApplication.objects.get().applicant is None


def test_staff_cannot_bypass_related_delete_permissions(
    client: Client, account: User
) -> None:
    """Обычный сотрудник сохраняет ограничения на удаление связанных журналов."""
    staff = User.objects.create_user(email="staff@example.test", is_staff=True)
    staff.user_permissions.add(
        Permission.objects.get(content_type__app_label="users", codename="delete_user")
    )
    client.force_login(staff)
    url = reverse("admin:users_user_delete", args=[account.pk])
    preview = client.get(url, secure=True)
    assert preview.status_code == 200
    assert preview.context["perms_lacking"]
    response = client.post(url, {"post": "yes"}, secure=True)
    assert response.status_code == 403
    assert User.objects.filter(pk=account.pk).exists()


def test_superuser_cannot_delete_notification_separately(
    client: Client, account: User
) -> None:
    """Разрешение каскада не открывает ручное удаление истории уведомлений."""
    moderator = User.objects.create_superuser(email="admin@example.test")
    client.force_login(moderator)
    note = Notification.objects.get(user=account, message="Тестовое уведомление")
    response = client.post(
        reverse("admin:users_notification_delete", args=[note.pk]),
        {"post": "yes"},
        secure=True,
    )
    assert response.status_code == 403
    assert Notification.objects.filter(pk=note.pk).exists()
