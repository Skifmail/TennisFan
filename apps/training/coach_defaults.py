"""Подстановка известных данных пользователя в карточку тренера."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils.text import slugify

from apps.users.display import format_user_display_name
from apps.users.models import Player

if TYPE_CHECKING:
    from apps.users.models import User

    from .models import Coach


def coach_defaults_from_user(user: User | None) -> dict[str, str]:
    """Собрать имя, контакты и город из профиля пользователя.

    Email не подставляется в имя: для карточки тренера нужно ФИО.

    Args:
        user: Выбранный пользователь платформы.

    Returns:
        dict[str, str]: Значения для пустых полей ``Coach``.
    """
    if user is None:
        return {
            "name": "",
            "phone": "",
            "telegram": "",
            "whatsapp": "",
            "max_contact": "",
            "city": "",
        }

    try:
        player = user.player
    except Player.DoesNotExist:
        player = None
    display_name = format_user_display_name(user)
    email = str(user.email or "").strip()
    name = display_name if display_name and display_name != email else ""

    return {
        "name": name,
        "phone": str(user.phone or "").strip(),
        "telegram": str(getattr(player, "telegram", "") or "").strip(),
        "whatsapp": str(getattr(player, "whatsapp", "") or "").strip(),
        "max_contact": str(getattr(player, "max_contact", "") or "").strip(),
        "city": str(getattr(player, "city", "") or "").strip(),
    }


def unique_coach_slug(name: str, *, exclude_pk: int | None = None) -> str:
    """Построить свободный slug карточки тренера.

    Args:
        name: Отображаемое имя тренера.
        exclude_pk: Исключить текущую карточку при редактировании.

    Returns:
        str: Уникальный slug.
    """
    from .models import Coach

    base = slugify(name) or "coach"
    slug = base
    index = 0
    queryset = Coach.objects.all()
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    while queryset.filter(slug=slug).exists():
        index += 1
        slug = f"{base}-{index}"
    return slug


def apply_coach_defaults(
    values: dict[str, str],
    defaults: dict[str, str],
) -> dict[str, str]:
    """Заполнить пустые поля известными данными пользователя.

    Args:
        values: Текущие значения формы или модели.
        defaults: Результат ``coach_defaults_from_user``.

    Returns:
        dict[str, str]: Словарь с подставленными пустыми полями.
    """
    filled = dict(values)
    for field, default in defaults.items():
        current = str(filled.get(field) or "").strip()
        if not current and default:
            filled[field] = default
    return filled


def fill_coach_from_user(coach: Coach, *, overwrite: bool = False) -> None:
    """Подставить данные пользователя в экземпляр тренера.

    Args:
        coach: Карточка тренера.
        overwrite: Заменять уже заполненные поля.
    """
    defaults = coach_defaults_from_user(coach.user)
    for field, default in defaults.items():
        if not default:
            continue
        current = str(getattr(coach, field) or "").strip()
        if overwrite or not current:
            setattr(coach, field, default)
    if coach.name and not str(coach.slug or "").strip():
        coach.slug = unique_coach_slug(coach.name, exclude_pk=coach.pk)
