"""Контракты возврата и закрытия файлов при отправке фото в Telegram."""

from pathlib import Path
from typing import Final, Literal

import pytest
from pytest_mock import MockerFixture

from apps.telegram_bot.services import send_photo_or_raise
from apps.telegram_bot.transport import TelegramAPIError

MESSAGE_ID: Final = 123


@pytest.mark.parametrize("source", ["bytes", "url", "path"])
def test_photo_when_source_supported_should_return_id_and_close_stream(
    source: Literal["bytes", "url", "path"],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    """Возвращает идентификатор сообщения и освобождает локальный ресурс.

    Args:
        source: Вид источника изображения.
        tmp_path: Изолированный каталог теста.
        mocker: Подмена внешнего транспорта.
    """
    photo_path = tmp_path / "photo.jpg"
    photo_path.write_bytes(b"photo")
    photos: dict[str, str | bytes] = {
        "bytes": b"photo",
        "url": "https://example.test/photo.jpg",
        "path": str(photo_path),
    }
    photo = photos[source]
    call = mocker.patch(
        "apps.telegram_bot.services.telegram_call",
        return_value={"message_id": MESSAGE_ID},
    )

    assert send_photo_or_raise(1, photo) == MESSAGE_ID

    stream = call.call_args.kwargs["photo"]
    if source == "url":
        assert stream is None
        assert call.call_args.args[2]["photo"] == photo
    else:
        assert stream.closed


def test_photo_when_api_rejects_should_close_stream_and_preserve_error(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Не скрывает отказ API и закрывает файл при исключении.

    Args:
        tmp_path: Изолированный каталог теста.
        mocker: Подмена внешнего транспорта.
    """
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    call = mocker.patch(
        "apps.telegram_bot.services.telegram_call",
        side_effect=TelegramAPIError("telegram_bad_request"),
    )

    with pytest.raises(TelegramAPIError, match="telegram_bad_request"):
        send_photo_or_raise(1, str(photo))

    assert call.call_args.kwargs["photo"].closed


def test_photo_when_file_missing_should_raise_typed_error_without_api_call(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Возвращает безопасную доменную ошибку для отсутствующего файла.

    Args:
        tmp_path: Изолированный каталог теста.
        mocker: Подмена внешнего транспорта.
    """
    call = mocker.patch("apps.telegram_bot.services.telegram_call")

    with pytest.raises(TelegramAPIError, match="telegram_photo_unavailable"):
        send_photo_or_raise(1, str(tmp_path / "missing.jpg"))

    call.assert_not_called()
