"""Защита подготовки браузерного стенда от изменений рабочей базы."""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from apps.core.models import City
from apps.users.models import User
from tests.support import chat_browser_seed


@pytest.mark.parametrize(
    ("name", "host"),
    [("production", "127.0.0.1"), ("tennison_smoke", "db.internal")],
)
def test_browser_seed_when_database_not_isolated_should_not_create_admin(
    mocker: MockerFixture, tmp_path: Path, name: str, host: str
) -> None:
    """Администратор тестового стенда не создаётся в рабочей или удалённой базе.

    Args:
        mocker: Подмена внешних зависимостей.
        tmp_path: Временная папка теста.
        name: Имя проверяемой базы.
        host: Адрес сервера базы.
    """
    connection = mocker.patch.object(chat_browser_seed, "connection")
    connection.settings_dict = {"NAME": name, "HOST": host}
    users = mocker.patch.object(User.objects, "update_or_create")
    cities = mocker.patch.object(City.objects, "get_or_create")
    path = tmp_path / "sessions.json"
    with pytest.raises(chat_browser_seed.ChatSmokeSetupError):
        chat_browser_seed.create_smoke_sessions(path)
    users.assert_not_called()
    cities.assert_not_called()
    assert not path.exists()


@pytest.mark.parametrize(
    ("name", "host"), [("production", "127.0.0.1"), ("tennison_smoke", "db.internal")]
)
def test_browser_worker_when_database_not_isolated_should_not_deliver(
    mocker: MockerFixture, name: str, host: str
) -> None:
    """Браузерный помощник не запускает очередь рабочей базы.

    Args:
        mocker: Изоляция конфигурации и обработчика.
        name: Проверяемое имя базы.
        host: Проверяемый сервер базы.
    """
    connection = mocker.patch.object(chat_browser_seed, "connection")
    connection.settings_dict = {"NAME": name, "HOST": host}
    worker = mocker.patch("apps.chat.invitations.process_invitation_queue")
    with pytest.raises(chat_browser_seed.ChatSmokeSetupError):
        chat_browser_seed.process_smoke_invitations()
    worker.assert_not_called()
