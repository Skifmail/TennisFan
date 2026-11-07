"""Общие плагины тестов без изменения production-настроек."""

from typing import Final

pytest_plugins: Final = ("tests.support.network_guard",)
