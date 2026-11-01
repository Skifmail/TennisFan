"""HTTP-клиент для внешних API с предсказуемым TLS из контейнера."""

import ssl
from typing import Any, Final

import requests
from requests.adapters import HTTPAdapter

# Debian trixie приносит OpenSSL 3.5, который по умолчанию предлагает
# пост-квантовый обмен X25519MLKEM768. ClientHello вырастает за пределы MSS,
# и на пути из Docker-моста к Cloudflare он теряется: TCP-соединение
# устанавливается, а TLS-рукопожатие зависает до таймаута. Явная кривая
# оставляет TLS 1.3 и убирает только пост-квантовый обмен.
ECDH_CURVE: Final = "X25519"


class ProviderTLSAdapter(HTTPAdapter):
    """Адаптер requests с TLS 1.3 без пост-квантового обмена ключами.

    Attributes:
        Нет собственного состояния: контекст создаётся при инициализации пула.
    """

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        """Подставляет SSL-контекст с явной группой вместо умолчания OpenSSL.

        Args:
            *args: Позиционные аргументы родительского пула соединений.
            **kwargs: Параметры пула; сюда добавляется `ssl_context`.
        """
        context = ssl.create_default_context()
        context.set_ecdh_curve(ECDH_CURVE)
        kwargs["ssl_context"] = context
        super().init_poolmanager(*args, **kwargs)


def provider_session() -> requests.Session:
    """Возвращает сессию для запросов к внешним API.

    Returns:
        Сессия, которая использует TLS 1.3 без пост-квантового обмена.
    """
    session = requests.Session()
    session.mount("https://", ProviderTLSAdapter())
    return session
