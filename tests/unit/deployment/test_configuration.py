"""Контракт безопасной сборки и порядка запуска Dokploy."""

import re
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final = Path(__file__).resolve().parents[3]
SOCIAL_PROVIDERS: Final = ("yandex", "vk")


def _compose() -> dict[str, Any]:
    return dict(yaml.safe_load((ROOT / "docker-compose.dokploy.yml").read_text()))


def test_compose_when_workers_start_should_wait_for_ready_web() -> None:
    """Миграции завершаются до подключения чата и выполнения cron."""
    services = _compose()["services"]
    for name in ("chat", "cron"):
        assert services[name]["depends_on"]["web"]["condition"] == "service_healthy"
    assert "healthcheck" in services["web"]
    assert "healthcheck" in services["chat"]


def test_compose_when_proxy_routes_chat_should_use_explicit_network() -> None:
    """Traefik не выбирает случайную внутреннюю сеть для WebSocket."""
    compose = _compose()
    assert compose["networks"]["dokploy-network"]["external"] is True
    for name in ("web", "chat"):
        service = compose["services"][name]
        assert "dokploy-network" in service["networks"]
        assert "traefik.docker.network=dokploy-network" in service["labels"]
    for name in ("db", "redis", "chat_redis"):
        assert "ports" not in compose["services"][name]


def test_compose_when_building_release_should_keep_static_in_image() -> None:
    """Статика относится к версии образа, а не общему устаревающему тому."""
    web = _compose()["services"]["web"]
    assert not any("/app/staticfiles" in value for value in web.get("volumes", []))
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "requirements.lock" in dockerfile
    assert "build_css_bundle.py" in dockerfile
    assert "collectstatic" in dockerfile


def test_dockerignore_when_building_should_exclude_private_and_local_artifacts() -> (
    None
):
    """Локальные секреты, окружения и тестовые отчёты не попадают в образ."""
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    for pattern in (
        ".env.*",
        ".passwords",
        "*.dump",
        "*.sqlite3-*",
        "backups/",
        "node_modules/",
        "docs_venv/",
        "tmp/",
        ".coverage",
        "htmlcov/",
    ):
        assert pattern in ignored


def test_entrypoints_when_starting_should_not_source_environment_as_shell() -> None:
    """Значения из Dokploy не исполняются как shell-код."""
    assert "source /app/.env" not in (ROOT / "entrypoint.sh").read_text()
    assert "prepare_deployment" in (ROOT / "entrypoint.sh").read_text()


def test_ci_when_deploying_should_require_checks_and_image_and_opt_in() -> None:
    """CI не создаёт второй автоматический деплой без явного включения."""
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deployment-checks.yml").read_text()
    )
    deploy = workflow["jobs"]["deploy"]
    assert set(deploy["needs"]) == {"checks", "image"}
    assert "DOKPLOY_DEPLOY_ENABLED == 'true'" in deploy["if"]
    steps = workflow["jobs"]["checks"]["steps"]
    runs = "\n".join(step.get("run", "") for step in steps)
    assert "tests/unit/deployment" in runs
    assert "tests/e2e/chat" in runs
    assert "pip-audit" in runs
    assert "venv/bin/pytest tests/ --no-cov -q" in runs
    assert "makemigrations --check --dry-run" in runs
    assert "venv/bin/python scripts/build_css_bundle.py" in runs


def test_compose_when_starting_http_should_not_run_as_root_or_gain_privileges() -> None:
    """HTTP и WebSocket не получают привилегии системного cron-контейнера."""
    for name in ("web", "chat"):
        service = _compose()["services"][name]
        assert service["user"] == "10001:10001"
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]


def test_compose_when_social_login_enabled_should_pass_provider_environment() -> None:
    """Переменные соцвхода из Dokploy доходят до контейнера, иначе кнопки скрыты."""
    app_env = dict(_compose()["x-app-env"])
    settings_source = (ROOT / "config/settings.py").read_text()
    suffixes = set(
        re.findall(r'f"SOCIAL_\{key\.upper\(\)\}_([A-Z_]+)"', settings_source)
    )
    assert suffixes == {"ENABLED", "CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"}
    for provider in SOCIAL_PROVIDERS:
        for suffix in suffixes:
            name = f"SOCIAL_{provider.upper()}_{suffix}"
            assert name in app_env, f"{name} отсутствует в x-app-env"
            assert str(app_env[name]).startswith(
                "${" + name
            ), f"{name} должен читаться из окружения Dokploy"
