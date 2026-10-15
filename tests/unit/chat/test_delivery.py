"""Сбои realtime-доставки не откатывают сохранённую переписку."""

from pytest_mock import MockerFixture
from redis.exceptions import ConnectionError

from apps.chat.realtime import publish_room_change


def test_publish_when_redis_unavailable_should_log_without_raising(
    mocker: MockerFixture,
) -> None:
    """Журнал БД позволяет восстановить изменения после сбоя Redis."""
    layer = mocker.Mock()
    mocker.patch("apps.chat.realtime.get_channel_layer", return_value=layer)
    mocker.patch(
        "apps.chat.realtime.async_to_sync", side_effect=ConnectionError("offline")
    )
    log = mocker.patch("apps.chat.realtime.logger")
    publish_room_change(1)
    log.exception.assert_called_once()
