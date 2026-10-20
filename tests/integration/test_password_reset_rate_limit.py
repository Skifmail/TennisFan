"""Ограничение публичного восстановления пароля без раскрытия аккаунта."""

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def reset_settings() -> None:
    """Изолирует окно частоты и локальный почтовый транспорт."""
    cache.clear()
    if hasattr(mail, "outbox"):
        mail.outbox.clear()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver"],
)
def test_password_reset_when_ip_limit_exceeded_should_not_send_more(
    client: Client,
) -> None:
    """Шестой запрос с одного IP получает тот же редирект без нового письма."""
    user = User.objects.create_user(
        email="reset-limit@example.test", password="test-only"
    )
    for _ in range(5):
        response = client.post(reverse("password_reset"), {"email": user.email})
        assert response.status_code == 302
    sixth = client.post(reverse("password_reset"), {"email": user.email})
    assert sixth.status_code == 302
    assert len(mail.outbox) == 5


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver"],
)
def test_password_reset_when_email_limit_exceeded_from_many_ips_should_stop(
    client: Client,
) -> None:
    """Лимит на адрес не позволяет засыпать ящик с разных адресов."""
    user = User.objects.create_user(
        email="reset-email-limit@example.test", password="test-only"
    )
    for index in range(5):
        response = client.post(
            reverse("password_reset"),
            {"email": user.email},
            REMOTE_ADDR=f"10.0.0.{index + 1}",
        )
        assert response.status_code == 302
    sixth = client.post(
        reverse("password_reset"),
        {"email": user.email},
        REMOTE_ADDR="10.0.0.99",
    )
    assert sixth.status_code == 302
    assert len(mail.outbox) == 5
