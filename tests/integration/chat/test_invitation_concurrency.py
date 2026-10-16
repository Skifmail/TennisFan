"""Одновременные фоновые обработчики не создают повторных приглашений и писем."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection
from pytest_mock import MockerFixture

from apps.chat import invitations
from apps.chat.models import ChatInvitation, ChatMembership
from apps.core.models import City
from apps.users.models import Notification
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Нужны блокировки PostgreSQL"
    ),
]


def test_invitation_when_workers_overlap_should_create_and_send_once(
    mocker: MockerFixture,
) -> None:
    """Пересекающиеся кампании не дублируют уведомление и SMTP-попытку.

    Args:
        mocker: Изоляция внешнего транспорта.
    """
    send = mocker.patch(
        "apps.chat.invitations.send_invitation_email", return_value=True
    )
    owner = make_user(email="invite-race-owner@test.local")
    room = join_city_with_consent(
        user=owner, city_id=City.objects.create(name="Город приглашений").pk
    )
    room.administrator = owner
    room.save(update_fields=["administrator"])
    target = make_user(email="invite-race-target@test.local")
    for audience in ("platform", "person"):
        preview = invitations.preview_invitation(
            user=owner,
            room_id=room.pk,
            audience=audience,
            target_id=target.pk if audience == "person" else None,
        )
        invitations.queue_invitation(user=owner, room_id=room.pk, token=preview.token)
    barrier = Barrier(2)

    def work() -> None:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            invitations.process_invitation_queue()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    invitations.process_invitation_queue()
    assert ChatInvitation.objects.filter(room=room, recipient=target).count() == 1
    assert Notification.objects.filter(user=target).count() == 1
    assert not ChatMembership.objects.filter(room=room, user=target).exists()
    assert send.call_count == 1
