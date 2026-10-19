"""Проверки включения WebSocket в поддерживаемые схемы запуска."""

from pathlib import Path
from typing import Final

import pytest
import yaml

ROOT: Final = Path(__file__).resolve().parents[3]


def test_deployment_when_dokploy_should_route_websocket_to_asgi() -> None:
    """WebSocket имеет отдельный ASGI backend и общий Redis между процессами."""
    compose = (ROOT / "docker-compose.dokploy.yml").read_text()
    assert "PathPrefix(`/ws/chats/`)" in compose
    assert "config.asgi:application" in compose
    assert "CHAT_REDIS_URL" in compose
    assert "chat_redis:" in compose


def test_deployment_when_procfile_should_use_websocket_capable_server() -> None:
    """Публичный web-процесс умеет принимать WebSocket, а не только WSGI."""
    procfile = (ROOT / "Procfile").read_text()
    assert "web: uvicorn" in procfile
    assert "config.asgi:application" in procfile


@pytest.mark.parametrize(
    "filename", ["docker-compose.yml", "docker-compose.dokploy.yml"]
)
def test_chat_server_when_started_should_preserve_websocket_and_shutdown_policy(
    filename: str,
) -> None:
    """Проверяет WebSocket, heartbeat, прокси и ограниченное завершение процесса.

    Args:
        filename: Поддерживаемый Compose-файл.
    """
    compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
    chat = compose["services"]["chat"]
    command = chat["entrypoint"]
    assert command[:2] == ["uvicorn", "config.asgi:application"]
    expected = {
        "--host": "0.0.0.0",
        "--port": "8001",
        "--ws": "websockets-sansio",
        "--ws-ping-interval": "20",
        "--ws-ping-timeout": "30",
        "--lifespan": "off",
        "--timeout-graceful-shutdown": "30",
        "--forwarded-allow-ips": "*",
    }
    for option, value in expected.items():
        assert command[command.index(option) + 1] == value
    assert "--proxy-headers" in command
    assert "ports" not in chat
    assert chat["expose"] == ["8001"]
    assert chat["stop_grace_period"] == "45s"


def test_ci_when_running_browser_checks_should_use_two_uvicorn_processes() -> None:
    """Проверяет настоящий запуск обоих ASGI-процессов новым сервером.

    Returns:
        Ничего; отсутствие двух процессов или SansIO останавливает проверку.
    """
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deployment-checks.yml").read_text(encoding="utf-8")
    )
    step = next(
        step
        for step in workflow["jobs"]["checks"]["steps"]
        if step.get("name") == "Browser tests with real Redis and two ASGI processes"
    )
    run = step["run"]
    assert "daphne" not in run
    for port in ("18765", "18766"):
        assert f"--port {port}" in run
    assert run.count("venv/bin/uvicorn config.asgi:application") == 2
    assert run.count("--ws websockets-sansio") == 2
    assert run.count("--lifespan off") == 2


def test_ci_when_building_image_should_check_removed_server_packages_are_absent() -> (
    None
):
    """Проверяет реальный образ на отсутствие прежнего сервера и Twisted.

    Returns:
        Ничего; отсутствие проверки установленных пакетов нарушает контракт сборки.
    """
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deployment-checks.yml").read_text(encoding="utf-8")
    )
    checks = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["image"]["steps"]
    )
    assert "find_spec" in checks
    for package in ("daphne", "twisted", "uvicorn", "websockets"):
        assert package in checks
