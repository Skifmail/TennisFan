"""Контракт правил каналов и ограничений ручных сообщений."""

import pytest

from apps.notifications.domain.rules import (
    NotificationValidationError,
    channels_for,
    validate_content,
    validate_destination,
)


@pytest.mark.parametrize(
    "push,email", [(False, False), (False, True), (True, False), (True, True)]
)
def test_channels_when_user_switches_change_should_keep_inbox(
    push: bool, email: bool
) -> None:
    """ЛК не зависит от внешних пользовательских переключателей."""
    assert channels_for((True, True, True), push=push, email=email) == (
        push,
        email,
        True,
    )


def test_channels_when_admin_disables_should_not_override_with_user_choice() -> None:
    """Пользователь не может разрешить запрещённый администратором канал."""
    assert channels_for((False, False, False), push=True, email=True) == (
        False,
        False,
        False,
    )


@pytest.mark.parametrize(
    "title,body",
    [
        ("", "Текст"),
        ("Тема", " "),
        ("я" * 41, "Текст"),
        ("Тема", "я" * 121),
        ("Тема\nBcc: чужой", "Текст"),
    ],
)
def test_content_when_invalid_should_raise_domain_error(title: str, body: str) -> None:
    """Пустой, слишком длинный и многострочный заголовок отвергаются."""
    with pytest.raises(NotificationValidationError):
        validate_content(title, body)


def test_content_when_boundary_should_accept_unicode() -> None:
    """Лимиты считаются в символах, а не в байтах UTF-8."""
    assert validate_content("я" * 40, "я" * 120) == ("я" * 40, "я" * 120)


@pytest.mark.parametrize(
    "url",
    [
        "//evil.test/",
        "javascript:alert(1)",
        "https://evil.test/",
        "/\\evil.test",
        "http://tennisfan.ru/",
        "/users/password-reset-confirm/secret/",
    ],
)
def test_destination_when_unsafe_should_reject(url: str) -> None:
    """Личные сообщения не ведут на сторонние и секретные страницы."""
    with pytest.raises(NotificationValidationError):
        validate_destination(url, "https://tennisfan.ru")


def test_destination_when_local_should_build_canonical_url() -> None:
    """Относительные ссылки преобразуются в HTTPS-адрес платформы."""
    assert (
        validate_destination("/tournaments/", "https://tennisfan.ru")
        == "https://tennisfan.ru/tournaments/"
    )
