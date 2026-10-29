"""Доступные кнопки социального входа без передачи ключей в шаблоны."""

from typing import cast

from django.http import Http404, HttpRequest

from .social_oauth import get_provider


def social_auth(request: HttpRequest) -> dict[str, list[dict[str, str]]]:
    """Передать названия только настроенных сервисов."""
    providers: list[dict[str, str]] = []
    for key in ("yandex", "vk"):
        try:
            get_provider(key)
        except Http404:
            continue
        providers.append(
            {
                "key": key,
                "label": "Яндекс" if key == "yandex" else "ВКонтакте",
                "brand": "yandex" if key == "yandex" else "vk",
            }
        )
        if key == "vk":
            providers.extend(
                [
                    {
                        "key": "vk",
                        "label": "Одноклассники",
                        "external_provider": "ok_ru",
                        "brand": "ok",
                    },
                    {
                        "key": "vk",
                        "label": "Mail.ru",
                        "external_provider": "mail_ru",
                        "brand": "mail",
                    },
                ]
            )
    return {"social_auth_providers": providers}


def profile_completion(request: HttpRequest) -> dict[str, object]:
    """Показать ненавязчивое напоминание о фактически недостающих данных."""
    from .models import Player, User
    from .onboarding import tennis_strength_set

    if not request.user.is_authenticated:
        return {}
    user = cast(User, request.user)
    if user.is_staff:
        return {}
    if request.path.startswith("/admin/") or (
        request.resolver_match
        and request.resolver_match.url_name
        in ("profile_edit", "auth", "login", "social_set_password")
    ):
        return {}
    try:
        player = Player.objects.get(user=user)
    except Player.DoesNotExist:
        return {}
    values = (
        ("Имя", user.first_name),
        ("Фамилия", user.last_name),
        ("Email", user.email),
        ("Телефон", user.phone),
        ("Дата рождения", player.birth_date),
        ("Город", player.city),
        ("Пол", player.gender),
        ("Фото профиля", player.avatar),
        ("Уровень силы", tennis_strength_set(player)),
    )
    missing = [label for label, value in values if not value]
    if user.email and not user.email_verified:
        missing.append("Подтверждение email")
    if not missing:
        return {}
    return {
        "profile_completion_reminder": {
            "user_id": user.pk,
            "fields": missing,
            "interval_ms": 6 * 60 * 60 * 1000,
        }
    }
