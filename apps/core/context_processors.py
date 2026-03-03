"""
Context processors for core app.
"""

from django.conf import settings

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
    return {
        "google_site_verification": getattr(
            settings, "GOOGLE_SITE_VERIFICATION", ""
        ).strip()
        or None,
        "yandex_verification": getattr(settings, "YANDEX_VERIFICATION", "").strip()
        or None,
    }
