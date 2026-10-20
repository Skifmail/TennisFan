"""Правила согласий, безопасных ссылок и короткого содержимого."""

from typing import Final
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

TITLE_LIMIT: Final = 40
BODY_LIMIT: Final = 120
URL_LIMIT: Final = 255
FORBIDDEN_PATHS: Final = ("password-reset", "verify-email", "token", "unsubscribe")
# Параметры с секретами, токенами и адресами не должны уходить стороннему push-провайдеру.
FORBIDDEN_QUERY_MARKERS: Final = (
    "token",
    "secret",
    "password",
    "passwd",
    "signature",
    "session",
    "authorization",
    "csrf",
    "jwt",
    "otp",
    "ticket",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "verify",
    "verification",
    "email",
)


class NotificationError(Exception):
    """Базовая ошибка домена уведомлений.

    Attributes:
        args: Безопасное для журнала описание ошибки.
    """


class NotificationValidationError(NotificationError):
    """Недопустимые настройки или содержимое уведомления.

    Attributes:
        args: Объяснение нарушения ограничений.
    """


def channels_for(
    policy: tuple[bool, bool, bool], *, push: bool, email: bool
) -> tuple[bool, bool, bool]:
    """Вычисляет каналы, не связывая ЛК с внешними согласиями.

    Args:
        policy: Разрешения администратора в порядке push, email, ЛК.
        push: Пользователь разрешил push.
        email: Пользователь разрешил email.

    Returns:
        Разрешённые каналы в том же порядке.
    """
    return policy[0] and push, policy[1] and email, policy[2]


def validate_content(title: str, body: str) -> tuple[str, str]:
    """Проверяет короткое сообщение без молчаливой обрезки.

    Args:
        title: Заголовок, не более 40 символов.
        body: Текст, не более 120 символов.

    Returns:
        Очищенные от внешних пробелов значения.

    Raises:
        NotificationValidationError: Пустое поле, превышение лимита или CR/LF заголовка.
    """
    if any(char in title for char in "\r\n"):
        raise NotificationValidationError("Заголовок должен занимать одну строку.")
    title, body = title.strip(), body.strip()
    if not title or not body or len(title) > TITLE_LIMIT or len(body) > BODY_LIMIT:
        raise NotificationValidationError(
            "Заголовок: 1–40 символов; текст: 1–120 символов."
        )
    return title, body


def _unsafe_query_name(name: str) -> bool:
    """Определяет параметр, который может содержать секрет или чужой адрес.

    Args:
        name: Имя параметра запроса в исходном регистре.

    Returns:
        Истина, если параметр нельзя передавать внешнему провайдеру.
    """
    normalized = name.strip().lower().replace("-", "_")
    compact = normalized.replace("_", "")
    if not normalized or normalized in {"code", "key", "auth", "sid", "sig"}:
        return True
    return any(marker.replace("_", "") in compact for marker in FORBIDDEN_QUERY_MARKERS)


def validate_destination(url: str, origin: str) -> str:
    """Разрешает HTTPS-ссылки платформы с безопасными query-параметрами.

    Параметры нужны для перехода к конкретному матчу, турниру или оплате.
    Секретные и адресные параметры по-прежнему запрещены: ссылка уходит
    стороннему push-провайдеру, поэтому токены и email не должны попадать в неё.

    Args:
        url: Относительная или абсолютная ссылка; пустая ведёт в ЛК.
        origin: Канонический HTTPS-origin платформы.

    Returns:
        Абсолютная безопасная ссылка.

    Raises:
        NotificationValidationError: Внешняя, неоднозначная, слишком длинная
            или секретная ссылка.
    """
    cleaned = unquote(url.strip())
    if cleaned.startswith("//") or "\\" in cleaned or any(ord(c) < 32 for c in cleaned):
        raise NotificationValidationError("Недопустимая ссылка.")
    target = urljoin(origin + "/", cleaned or "/users/notifications/")
    parsed, base = urlsplit(target), urlsplit(origin)
    if parsed.scheme != "https" or parsed.netloc != base.netloc or parsed.username:
        raise NotificationValidationError("Разрешены только HTTPS-страницы платформы.")
    if len(target) > URL_LIMIT:
        raise NotificationValidationError("Ссылка слишком длинная.")
    if parsed.fragment or any(part in parsed.path.lower() for part in FORBIDDEN_PATHS):
        raise NotificationValidationError(
            "Не используйте секретные ссылки и фрагменты."
        )
    if any(
        _unsafe_query_name(name)
        for name, _ in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise NotificationValidationError(
            "Не используйте секретные параметры в ссылке."
        )
    return target
