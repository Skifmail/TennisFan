"""Прямая отправка из формы и наблюдаемый прогресс без реальных сообщений."""

import html
import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.notifications.models import Broadcast, Delivery
from apps.notifications.services import expand_broadcast, queue_broadcast
from apps.users.models import User
from tests.integration.test_notification_center import (
    notification_settings as notification_settings,
)

pytestmark = pytest.mark.django_db


def _admin(client: Client) -> User:
    user = User.objects.create_superuser(
        email="flow-admin@example.test", password="test-only"
    )
    client.force_login(user)
    return user


def _payload(client: Client, action: str) -> dict[str, str]:
    response = client.get(reverse("admin:notifications_broadcast_add"))
    match = re.search(
        r'name="_broadcast_submission" value="([^"]+)"', response.content.decode()
    )
    assert match is not None
    return {
        "title": "Новость",
        "body": "Проверка формы",
        "url": "",
        "send_inbox": "on",
        action: "1",
        "_broadcast_submission": html.unescape(match.group(1)),
    }


def test_broadcast_form_when_creating_should_show_direct_actions(
    client: Client,
) -> None:
    """Новый черновик сразу предлагает тест и отправку с подтверждением.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    _admin(client)
    response = client.get(reverse("admin:notifications_broadcast_add"))
    assert "Отправить тест себе" in response.content.decode()
    assert "Отправить рассылку" in response.content.decode()
    assert "Сохранить черновик" in response.content.decode()


def test_broadcast_form_when_send_clicked_should_save_and_preview_without_queue(
    client: Client,
) -> None:
    """Кнопка сохраняет заполненную форму, но не отправляет без подтверждения.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    _admin(client)
    response = client.post(
        reverse("admin:notifications_broadcast_add"), _payload(client, "_send_now")
    )
    campaign = Broadcast.objects.get()
    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "admin:notifications_broadcast_send", args=[campaign.pk]
    )
    assert campaign.status == "draft"
    assert Delivery.objects.count() == 0


def test_broadcast_form_when_test_clicked_twice_should_queue_one_personal_copy(
    client: Client,
) -> None:
    """Повтор одного POST не создаёт вторую рассылку или вторую тестовую копию.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    actor = _admin(client)
    data = _payload(client, "_test_now")
    url = reverse("admin:notifications_broadcast_add")
    first = client.post(url, data)
    second = client.post(url, data)
    assert first.status_code == second.status_code == 302
    assert Broadcast.objects.filter(is_test=False).count() == 1
    test = Broadcast.objects.get(is_test=True)
    assert test.target_user == actor
    assert test.status == "queued"
    assert first.headers["Location"] == second.headers["Location"]
    assert Broadcast.objects.get(is_test=False).status == "draft"


def test_broadcast_form_when_token_reused_with_different_content_should_reject(
    client: Client,
) -> None:
    """Повторная форма не подменяет уже сохранённое содержимое по тому же ключу.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    _admin(client)
    data = _payload(client, "_test_now")
    url = reverse("admin:notifications_broadcast_add")
    assert client.post(url, data).status_code == 302
    data["body"] = "Другой текст"
    assert client.post(url, data).status_code == 403
    assert Broadcast.objects.count() == 2


def test_broadcast_form_when_invalid_should_not_create_or_queue(client: Client) -> None:
    """Ошибки формы остаются видимыми и не запускают отправку.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    _admin(client)
    data = _payload(client, "_test_now")
    data["title"] = "x" * 41
    assert (
        client.post(reverse("admin:notifications_broadcast_add"), data).status_code
        == 200
    )
    assert Broadcast.objects.count() == 0


def test_broadcast_progress_when_batches_and_jobs_finish_should_show_real_stage(
    client: Client,
) -> None:
    """Формирование аудитории не выдаётся за завершённую доставку.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    actor = _admin(client)
    campaign = Broadcast.objects.create(
        title="Новость", body="Текст", send_inbox=True, send_email=True
    )
    queue_broadcast(campaign.pk, actor=actor)
    url = reverse("admin:notifications_broadcast_progress", args=[campaign.pk])
    assert client.get(url).json()["stage"] == "Ожидает запуска"
    expand_broadcast(campaign.pk, batch_size=1)
    assert client.get(url).json()["stage"] == "Подготовка получателей"
    expand_broadcast(campaign.pk)
    state = client.get(url).json()
    assert state["stage"] == "Отправляется"
    assert state["inbox_created"] == 1
    assert state["channels"]["email"]["queued"] == 1
    Delivery.objects.filter(broadcast=campaign).update(status="accepted")
    state = client.get(url).json()
    assert state["stage"] == "Завершена"
    assert state["channels"]["email"]["accepted"] == 1
    assert state["terminal"] is True


def test_broadcast_progress_when_failures_or_uncertain_should_not_claim_success(
    client: Client,
) -> None:
    """Отказы и неопределённый результат видны отдельно от принятых заданий.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    actor = _admin(client)
    campaign = Broadcast.objects.create(title="Тест", body="Текст", send_email=True)
    queue_broadcast(campaign.pk, actor=actor)
    expand_broadcast(campaign.pk)
    url = reverse("admin:notifications_broadcast_progress", args=[campaign.pk])
    Delivery.objects.filter(broadcast=campaign).update(status="uncertain")
    assert client.get(url).json()["stage"] == "Требует проверки"
    Delivery.objects.filter(broadcast=campaign).update(status="failed")
    assert client.get(url).json()["stage"] == "Завершена с ошибками"


def test_broadcast_progress_when_anonymous_should_not_disclose_counts(
    client: Client,
) -> None:
    """Сводка доступна только через проверку прав Django Admin.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    campaign = Broadcast.objects.create(title="Тест", body="Текст")
    assert (
        client.get(
            reverse("admin:notifications_broadcast_progress", args=[campaign.pk])
        ).status_code
        == 302
    )


def test_broadcast_form_when_staff_has_no_send_permission_should_not_save_or_queue(
    client: Client,
) -> None:
    """Подмена кнопки не даёт права на отправку редактору черновиков.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    from django.contrib.auth.models import Permission

    user = User.objects.create_user(email="editor-flow@example.test", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="add_broadcast"))
    client.force_login(user)
    data = _payload(client, "_test_now")
    assert (
        client.post(reverse("admin:notifications_broadcast_add"), data).status_code
        == 403
    )
    assert Broadcast.objects.count() == 0


def test_broadcast_form_when_csrf_missing_should_not_save_or_queue(
    client: Client,
) -> None:
    """Прямые действия сохраняют CSRF-проверку Django Admin.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    _admin(client)
    data = _payload(client, "_test_now")
    protected = Client(enforce_csrf_checks=True)
    protected.cookies = client.cookies
    assert (
        protected.post(reverse("admin:notifications_broadcast_add"), data).status_code
        == 403
    )
    assert Broadcast.objects.count() == 0


def test_broadcast_progress_when_staff_has_no_view_permission_should_reject(
    client: Client,
) -> None:
    """Одного доступа к админке недостаточно для просмотра чужой сводки.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    staff = User.objects.create_user(email="no-view-flow@example.test", is_staff=True)
    client.force_login(staff)
    campaign = Broadcast.objects.create(title="Тест", body="Текст")
    assert (
        client.get(
            reverse("admin:notifications_broadcast_progress", args=[campaign.pk])
        ).status_code
        == 403
    )


def test_broadcast_progress_when_opening_journal_should_filter_campaign(
    client: Client,
) -> None:
    """Ссылка из сводки открывает журнал только выбранной рассылки.

    Args:
        client: Изолированная HTTP-сессия теста.
    """
    _admin(client)
    campaign = Broadcast.objects.create(title="Тест", body="Текст", status="expanded")
    response = client.get(
        reverse("admin:notifications_delivery_changelist"),
        {"broadcast__id__exact": campaign.pk},
    )
    assert response.status_code == 200
