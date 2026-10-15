"""Проверки контейнерной HTTP-пробы."""

from urllib.error import URLError

from pytest_mock import MockerFixture

from scripts.healthcheck import check_health


def test_check_health_when_endpoint_ready_should_succeed(mocker: MockerFixture) -> None:
    """Проба проверяет HTTP и безопасное содержимое JSON."""
    request = mocker.patch("scripts.healthcheck.urlopen")
    response = request.return_value.__enter__.return_value
    response.status = 200
    response.read.return_value = b'{"status":"ok"}'
    assert check_health(8000)


def test_check_health_when_connection_fails_should_fail(mocker: MockerFixture) -> None:
    """Недоступный процесс не считается готовым."""
    mocker.patch("scripts.healthcheck.urlopen", side_effect=URLError("unavailable"))
    assert not check_health(8001)


def test_check_health_when_wrong_response_should_fail(mocker: MockerFixture) -> None:
    """HTML страницы ошибки не считается успешной пробой."""
    request = mocker.patch("scripts.healthcheck.urlopen")
    response = request.return_value.__enter__.return_value
    response.status = 200
    response.read.return_value = b"<html>maintenance</html>"
    assert not check_health(8000)
