"""Проверки HTTP-готовности без побочных эффектов и утечки ошибок."""

from django.db import OperationalError
from django.test import Client, override_settings
from pytest_mock import MockerFixture
from redis.exceptions import ConnectionError as RedisConnectionError


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=True)
def test_liveness_when_requested_should_skip_startup_tasks(
    mocker: MockerFixture,
) -> None:
    """Локальная проба работает без HTTPS и без отправки Telegram-приветствий."""
    startup = mocker.patch("apps.core.middleware._run_startup_tasks")
    response = Client().get("/health/live/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response["Cache-Control"] == "no-store"
    startup.assert_not_called()


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SECURE_SSL_REDIRECT=True,
    USE_REDIS=True,
    CHAT_REDIS_URL="redis://chat_redis:6379/0",
    HEALTHCHECK_CHAT_REDIS=True,
)
def test_readiness_when_dependencies_work_should_check_database_and_both_redis(
    mocker: MockerFixture,
) -> None:
    """Готовность требует основную БД и оба независимых Redis."""
    database = mocker.patch("apps.core.health.connection")
    redis = mocker.patch("apps.core.health.Redis.from_url")
    response = Client().get("/health/ready/")
    assert response.status_code == 200
    database.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
        "SELECT 1"
    )
    assert redis.call_count == 2


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=True)
def test_readiness_when_database_fails_should_return_generic_unavailable(
    mocker: MockerFixture,
) -> None:
    """Ответ не раскрывает имена узлов, пользователей и пароли."""
    database = mocker.patch("apps.core.health.connection")
    database.cursor.side_effect = OperationalError("private-password")
    response = Client().get("/health/ready/")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"private" not in response.content


@override_settings(
    ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=True, USE_REDIS=True
)
def test_readiness_when_redis_fails_should_return_unavailable(
    mocker: MockerFixture,
) -> None:
    """Работающая БД не маскирует отказ транспорта чата."""
    mocker.patch("apps.core.health.connection")
    redis = mocker.patch("apps.core.health.Redis.from_url")
    redis.return_value.__enter__.return_value.ping.side_effect = RedisConnectionError(
        "private-password"
    )
    assert Client().get("/health/ready/").status_code == 503


@override_settings(ALLOWED_HOSTS=["allowed.example"], SECURE_SSL_REDIRECT=False)
def test_liveness_when_host_untrusted_should_reject() -> None:
    """Проба не отменяет проверку разрешённых HTTP-хостов."""
    assert Client().get("/health/live/").status_code == 400


@override_settings(
    ALLOWED_HOSTS=["testserver"], USE_REDIS=False, HEALTHCHECK_CHAT_REDIS=False
)
def test_web_readiness_when_chat_transport_offline_should_keep_http_available(
    mocker: MockerFixture,
) -> None:
    """Отказ WebSocket не выводит весь HTTP-сайт из маршрутизации."""
    mocker.patch("apps.core.health.connection")
    redis = mocker.patch("apps.core.health.Redis.from_url")
    assert Client().get("/health/ready/").status_code == 200
    redis.assert_not_called()
