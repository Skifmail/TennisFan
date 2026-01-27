"""
Views for legal documents (privacy, offer, terms, personal data).
"""

import logging
from pathlib import Path

import markdown
from django.conf import settings
from django.shortcuts import render

logger = logging.getLogger(__name__)

BASE_DIR = Path(settings.BASE_DIR)
DOCS = {
    "personal-data": {
        "file": BASE_DIR / "personal_data.txt",
        "title": "Согласие на обработку персональных данных",
    },
    "privacy": {
        "file": BASE_DIR / "privacy.txt",
        "title": "Политика конфиденциальности",
    },
    "offer": {
        "file": BASE_DIR / "public_offer.txt",
        "title": "Публичная оферта",
    },
    "terms": {
        "file": BASE_DIR / "user_agreement.txt",
        "title": "Пользовательское соглашение",
    },
}


def _load_document(slug: str) -> tuple[str, str] | None:
    """Load document content and title by slug. Returns (html_content, title) or None."""
    meta = DOCS.get(slug)
    if not meta:
        return None
    path = meta["file"]
    if not path.exists():
        logger.warning("Legal document not found: %s", path)
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.exception("Failed to read %s: %s", path, e)
        return None
    html = markdown.markdown(raw, extensions=["extra"])
    return (html, meta["title"])


def legal_document(request, slug: str):
    """Render a legal document page."""
    result = _load_document(slug)
    if not result:
        from django.http import Http404

        raise Http404("Документ не найден")

    html_content, title = result
    return render(
        request,
        "legal/document.html",
        {"content": html_content, "title": title, "slug": slug},
    )


def legal_index(request):
    """Index page linking to all legal documents."""
    url_names = {
        "personal-data": "legal_personal_data",
        "privacy": "legal_privacy",
        "offer": "legal_offer",
        "terms": "legal_terms",
    }
    items = [
        {"slug": k, "title": v["title"], "url_name": url_names[k]}
        for k, v in DOCS.items()
        if v["file"].exists()
    ]
    return render(request, "legal/index.html", {"documents": items})
