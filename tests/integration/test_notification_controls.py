"""Постоянный доступ к настройкам из ленты и сохранение независимого канала ЛК."""

from collections.abc import Iterator

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.notifications.models import NotificationPreference
from apps.users.models import Notification, User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def controls_settings() -> Iterator[None]:
    """Включает центр локально без реальной отправки уведомлений.

    Yields:
        Тестовые параметры транспорта и разрешённых хостов.
    """
    with override_settings(
        NOTIFICATION_CENTER_ENABLED=True,
        SECURE_SSL_REDIRECT=False,
        ONESIGNAL_REST_API_KEY="test-only",
        ALLOWED_HOSTS=["testserver", "tennisfan.ru"],
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
    ):
        yield


@pytest.mark.parametrize("connected", [False, True])
def test_inbox_when_push_state_differs_should_always_offer_settings(
    client: Client, connected: bool
) -> None:
    """Настройки доступны до и после подключения, не вынесены в подвал сайта.

    Args:
        client: Клиент текущего пользователя.
        connected: Сохранённое согласие на push.
    """
    user = User.objects.create_user(email="controls@example.test")
    client.force_login(user)
    NotificationPreference.objects.create(user=user, push_enabled=connected)
    response = client.get(reverse("notifications"))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="notification-settings"' in html
    assert "Настроить уведомления" in html
    assert html.count('id="web-push-toggle"') == 1
    assert 'name="push_enabled"' in html and 'name="email_enabled"' in html
    assert 'id="notification-disable-dialog"' in html
    assert 'id="web-push-badge"' in html
    assert "Подключено" in html
    assert "уведомления в личном кабинете" in html.lower()


def test_settings_when_center_enabled_should_offer_test_delivery(
    client: Client,
) -> None:
    """Подключённое устройство можно проверить тестовым push из настроек.

    Args:
        client: Авторизованный клиент.
    """
    client.force_login(User.objects.create_user(email="test-push@example.test"))
    html = client.get(
        reverse("notifications"), HTTP_HOST="tennisfan.ru", secure=True
    ).content.decode()
    assert 'id="web-push-test"' in html
    assert "Проверить доставку" in html
    assert reverse("notification_test_push") in html


def test_settings_when_old_link_opened_should_redirect_to_inbox(client: Client) -> None:
    """Старые ссылки ведут в раскрытые настройки единой страницы уведомлений.

    Args:
        client: Авторизованный клиент.
    """
    client.force_login(User.objects.create_user(email="settings-link@example.test"))
    response = client.get(reverse("notification_settings"))
    assert response.status_code == 302
    assert (
        response["Location"]
        == reverse("notifications") + "?settings=1#notification-settings"
    )


def test_preferences_when_push_disabled_should_preserve_email_and_inbox(
    client: Client,
) -> None:
    """Push-согласие не удаляет историю и не отключает email.

    Args:
        client: Авторизованный клиент с историей уведомлений.
    """
    user = User.objects.create_user(email="disable-push@example.test")
    client.force_login(user)
    NotificationPreference.objects.create(
        user=user, push_enabled=True, email_enabled=True
    )
    note = Notification.objects.create(user=user, message="Приглашение на матч")
    response = client.post(reverse("notification_settings"), {"email_enabled": "on"})
    assert response.status_code == 302
    preference = NotificationPreference.objects.get(user=user)
    assert not preference.push_enabled and preference.email_enabled
    assert Notification.objects.filter(pk=note.pk, is_visible=True).exists()
    assert response["Location"].startswith(reverse("notifications"))


@pytest.mark.parametrize("flag", [False, True])
def test_invitation_when_rollout_flag_changes_should_be_home_only(flag: bool) -> None:
    """Приглашение включается отдельно после проверки iPhone и не мешает настройкам.

    Args:
        flag: Согласованный флаг запуска приглашения.
    """
    from django.test import RequestFactory
    from django.urls import resolve

    from apps.core.web_push import web_push_context

    user = User.objects.create_user(email="invite-rollout@example.test")
    with override_settings(NOTIFICATION_PROMPT_ENABLED=flag):
        for path in ("/", reverse("notifications")):
            request = RequestFactory().get(path, secure=True, HTTP_HOST="tennisfan.ru")
            request.user = user
            request.resolver_match = resolve(path)
            context = web_push_context(request)
            assert context.get("show_notification_invitation", False) is (
                flag and path == "/"
            )


@pytest.mark.parametrize(
    ("user_agent", "direct"),
    [
        (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 YaBrowser/26.8.0.0 "
            "Mobile Safari/537.36",
            True,
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) YaBrowser/26.8.0.0 "
            "Mobile/15E148 Safari/604.1",
            False,
        ),
        (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
            False,
        ),
    ],
)
def test_sdk_url_when_yandex_mobile_should_bypass_loader(
    user_agent: str, direct: bool
) -> None:
    """Yandex на Android получает SDK напрямую, iOS остаётся на загрузчике.

    Args:
        user_agent: Браузер, который открыл страницу.
        direct: Ожидается ли прямая ссылка на SDK без проверки загрузчика.
    """
    from django.test import RequestFactory
    from django.urls import resolve

    from apps.core.web_push import (
        ONESIGNAL_DIRECT_SDK_URL,
        ONESIGNAL_PAGE_SDK_URL,
        web_push_context,
    )

    request = RequestFactory().get(
        "/", secure=True, HTTP_HOST="tennisfan.ru", HTTP_USER_AGENT=user_agent
    )
    request.resolver_match = resolve("/")
    context = web_push_context(request)
    assert context["web_push_sdk_url"] == (
        ONESIGNAL_DIRECT_SDK_URL if direct else ONESIGNAL_PAGE_SDK_URL
    )


def test_invitation_when_user_explicitly_disabled_push_should_not_nag() -> None:
    """Явный отказ в настройках не переопределяется приглашением.

    «Позже» не меняет согласия, а выключенный пользователем push уважается.
    """
    from django.test import RequestFactory
    from django.urls import resolve

    from apps.core.web_push import web_push_context

    user = User.objects.create_user(email="invite-optout@example.test")
    NotificationPreference.objects.create(user=user, push_enabled=False)
    request = RequestFactory().get("/", secure=True, HTTP_HOST="tennisfan.ru")
    request.user = user
    request.resolver_match = resolve("/")
    with override_settings(NOTIFICATION_PROMPT_ENABLED=True):
        assert not web_push_context(request).get("show_notification_invitation", False)


@pytest.mark.parametrize("current_device", [False, True])
def test_invitation_when_verified_device_exists_should_match_signed_browser(
    client: Client, current_device: bool
) -> None:
    """Подключение другого браузера не скрывает приглашение на текущем устройстве.

    Args:
        client: Клиент с подписанным идентификатором браузера.
        current_device: Соответствует ли подтверждённое устройство текущему cookie.
    """
    from datetime import timedelta
    from uuid import uuid4

    from django.http import HttpResponse
    from django.utils import timezone

    from apps.notifications.browser_identity import COOKIE_NAME, COOKIE_SALT
    from apps.notifications.models import PushDevice

    user = User.objects.create_user(email="verified-browser@example.test")
    client.force_login(user)
    response = HttpResponse()
    response.set_signed_cookie(COOKIE_NAME, "current-browser", salt=COOKIE_SALT)
    client.cookies[COOKIE_NAME] = response.cookies[COOKIE_NAME]
    import hashlib

    PushDevice.objects.create(
        user=user,
        device_hash=hashlib.sha256(
            ("current-browser" if current_device else "other-browser").encode()
        ).hexdigest(),
        active=True,
        subscription_id=uuid4(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    with override_settings(NOTIFICATION_PROMPT_ENABLED=True):
        page = client.get("/", secure=True, HTTP_HOST="tennisfan.ru")
    assert page.status_code == 200
    assert (
        'id="notification-invitation"' in page.content.decode()
    ) is not current_device
