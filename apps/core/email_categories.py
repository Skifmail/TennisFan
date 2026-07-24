"""Классификация исходящих писем по разделам админки.

Вызывается из:
- ``apps.core.mail.LoggingEmailBackend._store_message``
- ``apps.core.email_service._send_html_email`` (явная категория на сообщении)
"""

from __future__ import annotations

import re

_CATEGORY_REGISTRATION = "registration"
_CATEGORY_NEW_TOURNAMENT = "new_tournament"
_CATEGORY_TOURNAMENT = "tournament"
_CATEGORY_SUBSCRIPTION = "subscription"
_CATEGORY_SECURITY = "security"
_CATEGORY_CLUBS = "clubs"
_CATEGORY_SUPPORT = "support"
_CATEGORY_OTHER = "other"

# Явные темы → раздел (порядок важен: более специфичные раньше).
_SUBJECT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"добро пожаловать|подтвердите\s+ваш\s+email|подтверждение\s+email|"
            r"верификац",
            re.IGNORECASE,
        ),
        _CATEGORY_REGISTRATION,
    ),
    (
        re.compile(
            r"новый\s+турнир|стартовал\s+новый\s+турнир",
            re.IGNORECASE,
        ),
        _CATEGORY_NEW_TOURNAMENT,
    ),
    (
        re.compile(
            r"турнир|постоплат|взнос|участ",
            re.IGNORECASE,
        ),
        _CATEGORY_TOURNAMENT,
    ),
    (
        re.compile(
            r"подписк|автопродлен|автосписан|тариф|спасибо за поддержку|"
            r"донат|пожертвован|оплат",
            re.IGNORECASE,
        ),
        _CATEGORY_SUBSCRIPTION,
    ),
    (
        re.compile(r"пароль|телефон", re.IGNORECASE),
        _CATEGORY_SECURITY,
    ),
    (
        re.compile(r"клуб|членск", re.IGNORECASE),
        _CATEGORY_CLUBS,
    ),
    (
        re.compile(r"поддержк|обращени|support", re.IGNORECASE),
        _CATEGORY_SUPPORT,
    ),
)

_VALID_CATEGORIES = frozenset(
    {
        _CATEGORY_REGISTRATION,
        _CATEGORY_NEW_TOURNAMENT,
        _CATEGORY_TOURNAMENT,
        _CATEGORY_SUBSCRIPTION,
        _CATEGORY_SECURITY,
        _CATEGORY_CLUBS,
        _CATEGORY_SUPPORT,
        _CATEGORY_OTHER,
    }
)


def classify_outbound_email(
    *,
    category: str = "",
    subject: str = "",
) -> str:
    """Определить раздел письма по явной категории или теме.

    Args:
        category (str): Явно заданный раздел (если передан отправителем).
        subject (str): Тема письма для эвристики.

    Returns:
        str: Значение ``OutboundEmail.Category``.
    """
    explicit = (category or "").strip()
    if explicit in _VALID_CATEGORIES:
        return explicit

    subject_text = (subject or "").strip()
    if not subject_text:
        return _CATEGORY_OTHER

    for pattern, mapped in _SUBJECT_RULES:
        if pattern.search(subject_text):
            return mapped
    return _CATEGORY_OTHER
