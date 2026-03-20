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


def _tennistop_host_set() -> frozenset[str]:
    """Возвращает множество хостов, для которых показывается бренд TennisTop.

    Returns:
        frozenset[str]: Нормализованные имена хостов (нижний регистр).
    """
    extra_raw = getattr(settings, "TENNISTOP_EXTRA_HOSTS", None)
    hosts = {"tennistop.ru", "www.tennistop.ru"}
    if extra_raw:
        if isinstance(extra_raw, str):
            for part in extra_raw.split(","):
                h = part.strip().lower()
                if h:
                    hosts.add(h)
        else:
            for part in extra_raw:
                h = str(part).strip().lower()
                if h:
                    hosts.add(h)
    return frozenset(hosts)


def site_branding(request):
    """Возвращает доменно-зависимый брендинг (логотип, подписи в шапке и футере).

    Args:
        request: HTTP-запрос (для чтения Host). Может быть пустым в редких случаях.

    Returns:
        dict[str, object]: Ключи для шаблонов: пути к логотипу/фавиконку, части текста бренда.
    """
    host = ""
    if request:
        host = (request.get_host() or "").split(":", 1)[0].strip().lower()

    is_tennistop = host in _tennistop_host_set()

    if is_tennistop:
        return {
            "is_tennistop": True,
            "site_logo_path": "images/logo_tennistop.png",
            "site_logo_alt": "TennisTop",
            "site_favicon_path": "images/logo_tennistop.png",
            "site_brand_en_base": "Tennis",
            "site_brand_en_accent": "Top",
            "site_brand_ru_base": "Теннис",
            "site_brand_ru_accent": "Топ",
            "site_brand_copyright": "TennisTop",
            "site_brand_footer_ru": "ТеннисТоп",
        }

    return {
        "is_tennistop": False,
        "site_logo_path": "images/logo.png",
        "site_logo_alt": "TennisFan",
        "site_favicon_path": "images/favicon.png",
        "site_brand_en_base": "Tennis",
        "site_brand_en_accent": "Fan",
        "site_brand_ru_base": "Теннис",
        "site_brand_ru_accent": "Фан",
        "site_brand_copyright": "TennisFan",
        "site_brand_footer_ru": "ТеннисФан",
    }
