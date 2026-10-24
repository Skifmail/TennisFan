"""Проверки единого origin для OneSignal и канонических www-адресов."""

from collections.abc import Iterator

import pytest
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def canonical_settings() -> Iterator[None]:
    """Разрешает production-хосты и отключает HTTPS-редирект в тестах.

    Yields:
        Изолированные домены без побочных эффектов middleware.
    """
    with override_settings(
        ALLOWED_HOSTS=[
            "tennisfan.ru",
            "www.tennisfan.ru",
            "tennistop.ru",
            "www.tennistop.ru",
            "example.test",
        ],
        SITE_DOMAIN="tennisfan.ru",
        TENNISTOP_EXTRA_HOSTS=["tennistop.ru", "www.tennistop.ru"],
        SECURE_SSL_REDIRECT=False,
    ):
        yield


def test_www_when_get_should_redirect_with_path_and_query(client: Client) -> None:
    """www-копия уходит на точный origin OneSignal без потери параметров."""
    response = client.get("/tournaments/?page=2", HTTP_HOST="www.tennisfan.ru")
    assert response.status_code == 308
    assert response["Location"] == "https://tennisfan.ru/tournaments/?page=2"


def test_www_when_post_should_preserve_method(client: Client) -> None:
    """308 сохраняет метод, чтобы формы не превращались в GET."""
    response = client.post("/users/login/", HTTP_HOST="www.tennisfan.ru")
    assert response.status_code == 308
    assert response["Location"] == "https://tennisfan.ru/users/login/"


def test_www_when_health_probe_should_not_redirect(client: Client) -> None:
    """Проверки здоровья отвечают на любом разрешённом хосте без редиректа."""
    response = client.get("/health/live/", HTTP_HOST="www.tennisfan.ru")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_www_when_host_not_allowed_should_still_redirect(client: Client) -> None:
    """www-копия получает 308 даже если её ещё нет в ALLOWED_HOSTS."""
    with override_settings(ALLOWED_HOSTS=["tennisfan.ru"]):
        response = client.get("/tournaments/", HTTP_HOST="www.tennisfan.ru")
    assert response.status_code == 308
    assert response["Location"] == "https://tennisfan.ru/tournaments/"


def test_www_tennistop_when_env_empty_should_use_default_brand_hosts(
    client: Client,
) -> None:
    """Без TENNISTOP_EXTRA_HOSTS действуют те же хосты, что и в брендинге."""
    with override_settings(TENNISTOP_EXTRA_HOSTS=None):
        response = client.get("/robots.txt", HTTP_HOST="www.tennistop.ru")
    assert response.status_code == 308
    assert response["Location"] == "https://tennistop.ru/robots.txt"


def test_www_tennistop_when_extra_hosts_custom_should_still_redirect(
    client: Client,
) -> None:
    """Свои дополнительные хосты не отменяют редирект бренда по умолчанию."""
    with override_settings(TENNISTOP_EXTRA_HOSTS=["shop.example.test"]):
        response = client.get("/robots.txt", HTTP_HOST="www.tennistop.ru")
    assert response.status_code == 308
    assert response["Location"] == "https://tennistop.ru/robots.txt"


@pytest.mark.parametrize(
    ("host", "expected_host"),
    [
        ("tennisfan.ru", None),
        ("www.tennistop.ru", "tennistop.ru"),
        ("tennistop.ru", None),
        ("example.test", None),
    ],
)
def test_host_when_visited_should_redirect_only_www(
    client: Client, host: str, expected_host: str | None
) -> None:
    """Основные домены и чужие разрешённые хосты не перенаправляются."""
    response = client.get("/robots.txt", HTTP_HOST=host)
    if expected_host is None:
        assert response.status_code == 200
    else:
        assert response.status_code == 308
        assert response["Location"] == f"https://{expected_host}/robots.txt"
