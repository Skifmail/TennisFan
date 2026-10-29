"""Публичные страницы правовых документов платформы."""

from pathlib import Path
from typing import Final, TypedDict

import markdown
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from loguru import logger

from .utils import LEGAL_DOCUMENT_PATHS


class _DocMeta(TypedDict):
    file: Path
    title: str


_TITLES: Final = {
    "social-personal-data": "Согласие на обработку данных для входа через внешний сервис",
    "personal-data": "Согласие на обработку персональных данных",
    "privacy": "Политика конфиденциальности",
    "offer": "Публичная оферта",
    "terms": "Пользовательское соглашение",
    "club-organizer-rules": "Правила для организаторов клубов",
    "fan-token": "Правила использования FAN-token",
    "chat-rules": "Правила городских чатов",
    "chat-personal-data": "Согласие на обработку персональных данных для городского чата",
    "chat-disclosure": "Согласие на распространение персональных данных в городском чате",
}
DOCS: Final[dict[str, _DocMeta]] = {
    slug: {"file": path, "title": _TITLES[slug]}
    for slug, path in LEGAL_DOCUMENT_PATHS.items()
}


def _load_document(slug: str) -> tuple[str, str] | None:
    meta = DOCS.get(slug)
    if meta is None:
        return None
    try:
        raw = meta["file"].read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        logger.exception("Не удалось прочитать правовой документ", document_slug=slug)
        return None
    return markdown.markdown(raw, extensions=["extra"]), meta["title"]


def legal_document(request: HttpRequest, slug: str) -> HttpResponse:
    """Показывает правовой документ из контролируемого реестра файлов.

    Args:
        request: Публичный запрос.
        slug: Идентификатор документа.

    Returns:
        Страница полного текста документа.

    Raises:
        Http404: Документ отсутствует или его не удалось прочитать.
    """
    result = _load_document(slug)
    if result is None:
        raise Http404("Документ не найден")
    content, title = result
    response = render(
        request,
        "legal/document.html",
        {"content": content, "title": title, "slug": slug},
    )
    # Текст по ссылке из формы не должен оставаться в кэше после смены редакции.
    response["Cache-Control"] = "no-cache"
    return response


def legal_index(request: HttpRequest) -> HttpResponse:
    """Показывает доступные правовые документы сайта.

    Args:
        request: Публичный запрос.

    Returns:
        Каталог документов с действующими маршрутами.
    """
    items = [
        {
            "slug": slug,
            "title": meta["title"],
            "url_name": "legal_" + slug.replace("-", "_"),
        }
        for slug, meta in DOCS.items()
        if meta["file"].exists()
    ]
    return render(request, "legal/index.html", {"documents": items})
