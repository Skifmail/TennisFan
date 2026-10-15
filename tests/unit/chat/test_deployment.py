"""Проверки включения WebSocket в поддерживаемые схемы запуска."""

from pathlib import Path
from typing import Final

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
    assert "web: daphne" in procfile
    assert "config.asgi:application" in procfile
