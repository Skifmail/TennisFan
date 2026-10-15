"""Недоверенный заголовок прокси не подменяет адрес доказательства согласия."""

import pytest
from django.test import RequestFactory

from apps.chat.consent import consent_request_ip


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("127.0.0.1", "203.0.113.10", "127.0.0.1"),
        ("::1", "bad", "::1"),
        ("invalid", "203.0.113.10", None),
        ("fe80::1%eth0", "", None),
    ],
)
def test_ip_when_forwarded_header_untrusted_should_record_only_valid_peer(
    peer: str, forwarded: str, expected: str | None
) -> None:
    """Без настроенного доверия фиксируется адрес соединения, а не заголовок."""
    request = RequestFactory().get(
        "/", REMOTE_ADDR=peer, HTTP_X_FORWARDED_FOR=forwarded
    )
    assert consent_request_ip(request) == expected
