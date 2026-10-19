"""Проверки воспроизводимой установки инструментов без ослабления хеш-проверки."""

import re
from pathlib import Path
from typing import Final

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT: Final = Path(__file__).resolve().parents[3]


def _locked_packages(path: Path) -> dict[str, Requirement]:
    packages: dict[str, Requirement] = {}
    for line in path.read_text(encoding="utf-8").replace("\\\n", " ").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        assert not entry.startswith("-"), f"Неожиданная директива: {entry}"
        declaration, *hashes = entry.split("--hash=")
        requirement = Requirement(declaration.strip())
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1, f"Версия не закреплена: {requirement}"
        assert specifiers[0].operator == "=="
        assert "*" not in specifiers[0].version
        assert hashes, f"Отсутствуют хеши: {requirement}"
        assert all(
            re.fullmatch(r"sha256:[0-9a-f]{64}", value.strip()) for value in hashes
        ), f"Некорректный SHA256: {requirement}"
        name = canonicalize_name(requirement.name)
        assert name not in packages, f"Повторяющийся пакет: {name}"
        packages[name] = requirement
    assert packages
    return packages


def test_dev_requirements_when_hash_checking_should_pin_and_hash_every_package() -> (
    None
):
    """Проверяет точные версии и SHA256 всех устанавливаемых dev-зависимостей.

    Returns:
        Ничего; незакреплённый пакет или пропущенный хеш нарушает контракт CI.
    """
    packages = _locked_packages(ROOT / "requirements-dev.txt")
    assert "black" in packages


def test_dev_requirements_when_resolved_should_preserve_every_runtime_pin() -> None:
    """Проверяет неизменность боевых версий при установке инструментов.

    Returns:
        Ничего; несовпадение runtime-пакета завершает проверку ошибкой.
    """
    runtime = _locked_packages(ROOT / "requirements.lock")
    development = _locked_packages(ROOT / "requirements-dev.txt")
    for name, requirement in runtime.items():
        assert development[name] == requirement, name


def test_dev_requirements_when_compiled_should_satisfy_declared_tool_ranges() -> None:
    """Проверяет включение боевого lock-файла и допустимые версии инструментов.

    Returns:
        Ничего; пропущенный инструмент или выход за диапазон нарушает контракт.
    """
    declarations = [
        line.strip()
        for line in (ROOT / "requirements-dev.in")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert declarations[0] == "-r requirements.lock"
    development = _locked_packages(ROOT / "requirements-dev.txt")
    for declaration in declarations[1:]:
        requirement = Requirement(declaration)
        locked = development[canonicalize_name(requirement.name)]
        version = next(iter(locked.specifier)).version
        assert version in requirement.specifier, requirement.name


def test_ci_when_installing_dev_tools_should_explicitly_require_hashes() -> None:
    """Проверяет обязательность хешей в реальной команде установки CI.

    Returns:
        Ничего; отсутствие обязательной проверки хешей останавливает тест.
    """
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deployment-checks.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["checks"]["steps"]
    installation = next(
        step["run"]
        for step in steps
        if step.get("name") == "Install locked runtime and test tools"
    )
    assert "pip install --require-hashes -r requirements-dev.txt" in installation
    assert "--no-require-hashes" not in installation


@pytest.mark.parametrize("filename", ["requirements.lock", "requirements-dev.txt"])
def test_asgi_dependencies_when_locked_should_use_uvicorn_without_twisted(
    filename: str,
) -> None:
    """Проверяет удаление уязвимого сервера из обоих устанавливаемых наборов.

    Args:
        filename: Боевой либо тестовый lock-файл.
    """
    packages = _locked_packages(ROOT / filename)
    assert {"uvicorn", "websockets"} <= packages.keys()
    assert {"daphne", "twisted", "pyopenssl"}.isdisjoint(packages)
