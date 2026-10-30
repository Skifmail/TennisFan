"""Полнота явного каталога и отсутствие обходов политики каналов."""

import ast
from pathlib import Path
from typing import Final

from apps.notifications.domain.catalog import EVENTS, PRODUCER_EVENTS

ROOT: Final = Path(__file__).resolve().parents[2]


def test_catalog_when_producers_declared_should_have_known_event_and_existing_function() -> (
    None
):
    """Каждый источник ссылается на существующую функцию и известное событие."""
    for producer, event_key in PRODUCER_EVENTS.items():
        module, function = producer.split(":")
        tree = ast.parse((ROOT / (module.replace(".", "/") + ".py")).read_text())
        assert any(
            isinstance(node, ast.FunctionDef) and node.name == function
            for node in ast.walk(tree)
        ), producer
        assert event_key in EVENTS, producer


def test_catalog_when_application_emits_should_never_bypass_registered_event() -> None:
    """Новые прямые Notification.create или неразмеченные письма ломают тест."""
    helpers = {
        "_send_html_email",
        "_send_club_email",
        "send_to_user_by_user",
        "_send_notification_email_to_user",
        "_notify_by_email_and_telegram",
    }
    for path in (ROOT / "apps").rglob("*.py"):
        if "migrations" in path.parts or "notifications" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            assert (
                name != "Notification.objects.create"
            ), f"{path}:{node.lineno}: обход центра"
            if name.split(".")[-1] not in helpers | {
                "create_notification",
                "EmailMultiAlternatives",
                "send_mail",
            }:
                continue
            key = next(
                (kw.value for kw in node.keywords if kw.arg == "event_key"), None
            )
            if (
                key is None
                and name == "send_to_user_by_user"
                and any(kw.arg == "skip_email" for kw in node.keywords)
            ):
                continue
            assert key is not None, f"{path}:{node.lineno}: нет event_key"
            if isinstance(key, ast.Constant):
                assert key.value in EVENTS, f"{path}:{node.lineno}: неизвестное событие"


def test_catalog_when_critical_credentials_should_keep_required_email() -> None:
    """Восстановление доступа и подтверждение адреса не отключаются общей подпиской."""
    assert EVENTS["account.password_reset"].required_email
    assert EVENTS["account.email_verification"].required_email


def test_catalog_when_seed_migration_loaded_should_cover_all_current_events() -> None:
    """Миграции политик в совокупности покрывают текущий каталог событий."""
    from importlib import import_module

    migration = import_module("apps.notifications.migrations.0002_seed_event_policies")
    stringer_migration = import_module(
        "apps.notifications.migrations.0004_stringer_application_event"
    )
    direct_migration = import_module(
        "apps.notifications.migrations.0008_direct_message_event"
    )
    junior_migration = import_module("apps.juniors.migrations.0008_notification_policy")
    assert {
        row["key"]
        for row in [
            *migration.ROWS,
            *stringer_migration.ROWS,
            *direct_migration.ROWS,
            *junior_migration.ROWS,
        ]
    } == set(EVENTS)
