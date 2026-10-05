"""QR-код публичной страницы турнира для печатных документов.

Вызывается из ``tournament_export_regulation``: PNG встраивается в PDF
как data URI и в Word как картинка. Текстовая ссылка на бумаге не печатается.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO

import qrcode
from loguru import logger as log

_FILL = "#0B3D2E"


@dataclass(frozen=True)
class PublicPageQr:
    """PNG QR-кода и его data URI."""

    data_uri: str
    content: bytes


def public_page_qr(url: str) -> PublicPageQr | None:
    """Собрать QR-код, который открывает публичную страницу турнира.

    Args:
        url: Абсолютный адрес страницы. Пустая строка — кода нет.

    Returns:
        Картинка кода либо ``None``, если адреса нет или его не удалось закодировать.
    """
    cleaned = url.strip()
    if not cleaned:
        return None
    code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    code.add_data(cleaned)
    try:
        code.make(fit=True)
        image = code.make_image(fill_color=_FILL, back_color="white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    except (OSError, ValueError) as exc:
        log.warning("Не удалось собрать QR-код страницы турнира: {}", exc)
        return None
    raw = buffer.getvalue()
    if not raw:
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return PublicPageQr(data_uri=f"data:image/png;base64,{encoded}", content=raw)
