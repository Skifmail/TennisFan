"""Жизненный цикл соединения при отправке итогов турнира."""

from pytest_mock import MockerFixture

from apps.tournaments.completion_notify import _notify_completed_safe


def test_completion_notification_when_dispatched_should_scope_database_connection(
    mocker: MockerFixture,
) -> None:
    """После рассылки освобождается только принадлежащее задаче соединение."""
    scope = mocker.patch("apps.tournaments.completion_notify.background_connection")
    notify = mocker.patch(
        "apps.tournaments.completion_notify.notify_tournament_completed"
    )
    _notify_completed_safe(1)
    scope.return_value.__enter__.assert_called_once_with()
    scope.return_value.__exit__.assert_called_once()
    notify.assert_called_once_with(1)
