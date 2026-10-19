"""Проверки ограниченного времени заданий CI без сокращения набора тестов."""

from pathlib import Path
from typing import Final

import pytest
import yaml

ROOT: Final = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("job_id", "expected_minutes"),
    [("checks", 40), ("image", 20), ("deploy", 2)],
)
def test_ci_when_running_job_should_use_explicit_bounded_timeout(
    job_id: str, expected_minutes: int
) -> None:
    """Проверяет бюджет полного набора и неизменность лимитов сборки и публикации.

    Args:
        job_id: Имя задания GitHub Actions.
        expected_minutes: Ограниченный бюджет задания в минутах.
    """
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deployment-checks.yml").read_text(encoding="utf-8")
    )
    timeout = workflow["jobs"][job_id]["timeout-minutes"]
    assert isinstance(timeout, int)
    assert timeout == expected_minutes
