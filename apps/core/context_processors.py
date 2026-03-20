"""
Context processors for core app.
"""

from django.conf import settings
from django.utils.encoding import iri_to_uri

from .models import FooterSocialLink


def telegram_community_url(request):
    """Добавляет в контекст публичную ссылку на открытое сообщество TennisFan в Telegram."""
    url = getattr(settings, "TELEGRAM_PUBLIC_COMMUNITY_URL", None) or ""
    return {"telegram_community_url": url.strip() or None}


def footer_social_links(request):
    """
    Добавляет в контекст список ссылок на соцсети из админки (раздел «Соцсети»).
    Каждый элемент: url, name, icon_url (медиа) или icon_path (static).
    """
    links = list(FooterSocialLink.objects.all())
    return {"footer_social_links": links}


def search_engine_verification(request):
    """
    Коды верификации для Google Search Console и Yandex Webmaster.
    Подставляются в <meta> в base.html при наличии в настройках.
    """
    google_tokens = [
        token.strip()
        for token in getattr(settings, "GOOGLE_SITE_VERIFICATION", "").split(",")
        if token.strip()
    ]
    yandex_tokens = [
        token.strip()
        for token in getattr(settings, "YANDEX_VERIFICATION", "").split(",")
        if token.strip()
    ]
    return {
        "google_site_verifications": google_tokens,
        "yandex_verifications": yandex_tokens,
    }


def site_meta(request):
    """
    Основные SEO-переменные сайта.

    Всегда строим canonical от основного домена, чтобы поисковики не считали
    второй домен отдельной копией сайта.
    """
    base_url = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", "").strip()
    if not base_url:
        domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru").strip()
        base_url = f"https://{domain}"
    base_url = base_url.rstrip("/")

    path = request.get_full_path() if request else "/"
    canonical_url = iri_to_uri(f"{base_url}{path}")

    return {
        "site_base_url": base_url,
        "canonical_url": canonical_url,
    }


def site_branding(request):
    """
    Возвращает доменно-зависимый брендинг.
    """
    host = ""
    if request:
        host = (request.get_host() or "").split(":", 1)[0].strip().lower()

    tennistop_hosts = {"tennistop.ru", "www.tennistop.ru"}
    is_tennistop = host in tennistop_hosts

    return {
        "site_logo_path": (
            "images/logo_tennistop.png" if is_tennistop else "images/logo.png"
        ),
        "site_logo_alt": "TennisTop" if is_tennistop else "TennisFan",
        "site_favicon_path": (
            "images/logo_tennistop.png" if is_tennistop else "images/favicon.png"
        ),
    }
