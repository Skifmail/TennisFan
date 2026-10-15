"""Проверки единственного подготовительного этапа релиза."""

from django.core.management import call_command
from pytest_mock import MockerFixture


def test_prepare_deployment_when_postgres_should_lock_migrations(
    mocker: MockerFixture,
) -> None:
    """Два web-процесса не запускают миграции одновременно."""
    mocker.patch("apps.core.management.commands.prepare_deployment.wait_for_database")
    database = mocker.patch(
        "apps.core.management.commands.prepare_deployment.connection"
    )
    database.vendor = "postgresql"
    cursor = database.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (True,)
    migrate = mocker.patch(
        "apps.core.management.commands.prepare_deployment.call_command"
    )
    mocker.patch.dict(
        "os.environ", {"DJANGO_SUPERUSER_EMAIL": "", "DJANGO_SUPERUSER_PASSWORD": ""}
    )
    call_command("prepare_deployment")
    migrate.assert_called_once_with("migrate", interactive=False)
    sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert "SELECT pg_try_advisory_lock(%s)" in sql
    assert "SELECT pg_advisory_unlock(%s)" in sql


def test_prepare_deployment_when_migration_fails_should_unlock_and_fail(
    mocker: MockerFixture,
) -> None:
    """Ошибка миграции прерывает релиз и освобождает блокировку."""
    import pytest
    from django.core.management.base import CommandError

    mocker.patch("apps.core.management.commands.prepare_deployment.wait_for_database")
    database = mocker.patch(
        "apps.core.management.commands.prepare_deployment.connection"
    )
    database.vendor = "postgresql"
    cursor = database.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (True,)
    mocker.patch(
        "apps.core.management.commands.prepare_deployment.call_command",
        side_effect=CommandError("migration failed"),
    )
    with pytest.raises(CommandError):
        call_command("prepare_deployment")
    cursor.execute.assert_any_call("SELECT pg_advisory_unlock(%s)", [87352016])


def test_prepare_deployment_when_lock_unavailable_should_fail_before_migration(
    mocker: MockerFixture,
) -> None:
    """Зависший соседний релиз не вызывает бесконечного ожидания."""
    import pytest
    from django.core.management.base import CommandError

    mocker.patch("apps.core.management.commands.prepare_deployment.wait_for_database")
    database = mocker.patch(
        "apps.core.management.commands.prepare_deployment.connection"
    )
    database.vendor = "postgresql"
    database.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
    mocker.patch(
        "apps.core.management.commands.prepare_deployment.time.monotonic",
        side_effect=[0, 121],
    )
    migrate = mocker.patch(
        "apps.core.management.commands.prepare_deployment.call_command"
    )
    with pytest.raises(CommandError, match="блокировки"):
        call_command("prepare_deployment")
    migrate.assert_not_called()
