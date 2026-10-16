"""Чистые правила городских чатов, не зависящие от Django."""

from typing import ClassVar, Final

MAX_CHAT_TITLE_LENGTH: Final = 120

MAX_MESSAGE_LENGTH: Final = 2000
MAX_REPORT_LENGTH: Final = 500
MESSAGE_INTERVAL_SECONDS: Final = 2
EDIT_WINDOW_SECONDS: Final = 900
PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 100
MAX_FRAME_LENGTH: Final = 4096
MAX_DATABASE_ID: Final = (1 << 63) - 1
MAX_HTTP_BODY_BYTES: Final = 16384


class ChatError(Exception):
    """Базовая ошибка городского чата.

    Attributes:
        code: Машиночитаемая категория ошибки.
        status: HTTP-статус для транспортного слоя.
    """

    code: ClassVar[str] = "chat_error"
    status: ClassVar[int] = 400


class ChatValidationError(ChatError):
    """Ошибка входных данных чата."""

    code: ClassVar[str] = "invalid_input"


class ChatAccessError(ChatError):
    """Ошибка доступа к комнате или действию."""

    code: ClassVar[str] = "forbidden"
    status: ClassVar[int] = 403


class ChatNotFoundError(ChatError):
    """Запрошенный объект чата отсутствует."""

    code: ClassVar[str] = "not_found"
    status: ClassVar[int] = 404


class ChatConsentRequiredError(ChatAccessError):
    """Требуется личное подтверждение действующих документов чата.

    Attributes:
        code: Причина отказа, по которой интерфейс предлагает форму согласий.
    """

    code: ClassVar[str] = "consent_required"


class ChatConsentVersionError(ChatError):
    """Подтверждаемая редакция или подписанная форма устарела.

    Attributes:
        status: Конфликт версии, требующий повторного открытия формы.
    """

    code: ClassVar[str] = "consent_stale"
    status: ClassVar[int] = 409


class ChatConsentDocumentError(ChatError):
    """Обязательный документ недоступен, поэтому вступление закрыто.

    Attributes:
        status: Временная недоступность юридических документов.
    """

    code: ClassVar[str] = "consent_document_unavailable"
    status: ClassVar[int] = 503


class ChatRateLimitError(ChatError):
    """Превышена допустимая частота сообщений."""

    code: ClassVar[str] = "rate_limited"
    status: ClassVar[int] = 429


def normalize_message(text: str) -> str:
    """Проверяет текст сообщения и удаляет внешние пробелы.

    Args:
        text: Исходный текст, включая возможную разметку как обычный текст.

    Returns:
        Проверенный текст.

    Raises:
        ChatValidationError: При пустом, слишком длинном или некорректном тексте.
    """
    cleaned = text.strip()
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ChatValidationError("Некорректный текст Unicode.") from exc
    if not cleaned or len(cleaned) > MAX_MESSAGE_LENGTH or "\x00" in cleaned:
        raise ChatValidationError(f"Введите от 1 до {MAX_MESSAGE_LENGTH} символов.")
    return cleaned
