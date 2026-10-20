"""Проверки графа миграций, используемого при запуске контейнера."""

from django.db.migrations.loader import MigrationLoader


def test_notification_migrations_have_one_leaf() -> None:
    """Параллельные изменения уведомлений не блокируют запуск командой migrate."""
    loader = MigrationLoader(None)
    assert "notifications" not in loader.detect_conflicts()
    assert len(loader.graph.leaf_nodes("notifications")) == 1
