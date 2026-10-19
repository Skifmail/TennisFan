"""Передача настроек центра всем процессам без неявного включения рассылок."""

from pathlib import Path
from typing import Final

import pytest
import yaml

ROOT: Final = Path(__file__).resolve().parents[2]
EXPECTED_ENVIRONMENT: Final = {
    "NOTIFICATION_CENTER_ENABLED": "${NOTIFICATION_CENTER_ENABLED:-False}",
    "ONESIGNAL_APP_ID": "${ONESIGNAL_APP_ID:-c6e46e19-f9f8-4df3-b53d-44f49da24af2}",
    "ONESIGNAL_SITE_ORIGIN": "${ONESIGNAL_SITE_ORIGIN:-https://tennisfan.ru}",
    "ONESIGNAL_REST_API_KEY": "${ONESIGNAL_REST_API_KEY:-}",
}


@pytest.mark.parametrize(
    "filename", ["docker-compose.yml", "docker-compose.dokploy.yml"]
)
def test_compose_when_center_configured_should_forward_settings_to_all_workers(
    filename: str,
) -> None:
    """Web, chat и cron получают одинаковый флаг и ключи только из окружения."""
    configuration = yaml.safe_load((ROOT / filename).read_text())
    for service in ("web", "chat", "cron"):
        environment = configuration["services"][service]["environment"]
        for key, value in EXPECTED_ENVIRONMENT.items():
            assert environment.get(key) == value, f"{filename}: {service}.{key}"
