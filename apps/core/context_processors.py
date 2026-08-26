"""
Context processors for core app.
"""

from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils.encoding import iri_to_uri

from apps.clubs.models import (
    Club,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMemberStatus,
)
from apps.clubs.services import club_can_add_member, club_has_public_page_access

from .models import FooterSocialLink

PLATFORM_ACTIVITY_UNSEEN_CACHE_PREFIX = "platform_activity_unseen"

FOOTER_SOCIAL_LINKS_CACHE_KEY = "footer_social_links:v1"


def telegram_community_url(request):
    """Добавляет в контекст публичную ссылку на открытое сообщество TennisFan в Telegram."""
    url = getattr(settings, "TELEGRAM_PUBLIC_COMMUNITY_URL", None) or ""
    return {"telegram_community_url": url.strip() or None}


def footer_social_links(request):
    """
    Добавляет в контекст список ссылок на соцсети из админки (раздел «Соцсети»).
    Каждый элемент: url, name, icon_url (медиа) или icon_path (static).
    """
    links = cache.get_or_set(
        FOOTER_SOCIAL_LINKS_CACHE_KEY,
        lambda: list(FooterSocialLink.objects.all()),
        timeout=300,
    )
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
    media_url = getattr(settings, "MEDIA_URL", "").strip()
    media_origin = ""
    if media_url.startswith("http://") or media_url.startswith("https://"):
        parsed = urlparse(media_url)
        media_origin = f"{parsed.scheme}://{parsed.netloc}"

    return {
        "site_base_url": base_url,
        "canonical_url": canonical_url,
        "media_origin": media_origin,
    }


def metrika_goals(request):
    """Передать в шаблон цели Метрики из сессии и сразу очистить очередь.

    Args:
        request: HTTP-запрос с сессией.

    Returns:
        dict: Ключ ``metrika_goals`` — список целей для ``reachGoal``.
    """
    import json

    from apps.core.metrika import pop_metrika_goals

    goals = []
    for item in pop_metrika_goals(request):
        entry = {"goal": item.get("goal", ""), "params_json": ""}
        params = item.get("params") or {}
        if params:
            entry["params_json"] = json.dumps(params, ensure_ascii=False)
        goals.append(entry)
    return {"metrika_goals": goals}


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


def footer_joinable_clubs(request):
    """Добавляет в контекст список клубов для футерной модалки вступления."""
    if (
        not request
        or not getattr(request, "user", None)
        or not request.user.is_authenticated
    ):
        return {"footer_joinable_clubs": []}

    clubs = list(
        Club.objects.filter(is_public=True)
        .annotate(
            active_members_count=Count(
                "members",
                filter=Q(members__status=ClubMemberStatus.ACTIVE),
                distinct=True,
            )
        )
        .order_by("name")
    )

    if not clubs:
        return {"footer_joinable_clubs": []}

    member_states = {
        item["club_id"]: item["status"]
        for item in ClubMember.objects.filter(user=request.user).values(
            "club_id", "status"
        )
    }
    pending_request_ids = set(
        ClubJoinRequest.objects.filter(
            user=request.user,
            status=ClubJoinRequestStatus.PENDING,
        ).values_list("club_id", flat=True)
    )

    items: list[dict[str, object]] = []
    for club in clubs:
        if not club_has_public_page_access(club):
            continue

        can_add, limit_message = club_can_add_member(club)
        membership_status = member_states.get(club.id)

        item = {
            "club": club,
            "members_count": club.active_members_count,
            "action": "request",
            "action_label": "Подать заявку",
            "action_disabled": False,
            "action_message": "",
        }

        if membership_status == ClubMemberStatus.ACTIVE:
            item["action"] = "member"
            item["action_label"] = "Вы участник"
            item["action_disabled"] = True
        elif membership_status == ClubMemberStatus.INVITED:
            item["action"] = "invite"
            item["action_label"] = "Есть приглашение"
        elif club.id in pending_request_ids:
            item["action"] = "pending"
            item["action_label"] = "Заявка отправлена"
            item["action_disabled"] = True
        elif not can_add:
            item["action"] = "closed"
            item["action_label"] = "Набор закрыт"
            item["action_disabled"] = True
            item["action_message"] = limit_message

        items.append(item)

    return {"footer_joinable_clubs": items}


def platform_activity_unseen(request):
    """Добавить флаг новых событий ленты для индикатора «Панель управления».

    Args:
        request: HTTP-запрос текущей страницы.

    Returns:
        dict[str, bool]: ``platform_activity_unseen`` — True, если есть новые события.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"platform_activity_unseen": False}
    if not (user.is_staff or user.is_superuser):
        return {"platform_activity_unseen": False}

    from apps.core.activity import has_unseen_platform_activity
    from apps.core.models import PlatformActivityEvent

    latest_event_id = (
        PlatformActivityEvent.objects.order_by("-id")
        .values_list("id", flat=True)
        .first()
        or 0
    )
    cache_key = f"{PLATFORM_ACTIVITY_UNSEEN_CACHE_PREFIX}:{user.id}:{latest_event_id}"
    unseen = cache.get(cache_key)
    if unseen is None:
        unseen = has_unseen_platform_activity(user)
        cache.set(cache_key, unseen, timeout=30)
    return {"platform_activity_unseen": unseen}
