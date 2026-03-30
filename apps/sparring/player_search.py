"""
Поиск игроков для приглашения на спарринг (ФИО или email).
"""

from __future__ import annotations

from django.db.models import CharField, Q, QuerySet, Value
from django.db.models.functions import Coalesce, Concat

from apps.users.models import Player


def _search_string_variants(q: str) -> list[str]:
    """
    Варианты строки для поиска без учёта регистра на любых СУБД.

    В SQLite LIKE/icontains для кириллицы регистрозависимы; LOWER() в SQL для
    кириллицы не всегда совпадает с Python. Поэтому перебираем типичные варианты
    написания (в т.ч. q.title() для Unicode — «шатайло» → «Шатайло»).

    Args:
        q: Строка запроса без лишних пробелов.

    Returns:
        Уникальные непустые варианты для icontains / iexact.
    """
    raw = q.strip()
    if not raw:
        return []
    variants = {raw, raw.lower(), raw.upper(), raw.title()}
    return [v for v in variants if len(v) >= 1]


def search_players_for_sparring_invite(
    query: str,
    *,
    exclude_player_id: int | None,
    limit: int = 20,
) -> QuerySet[Player]:
    """
    Найти игроков по email или ФИО (без учёта регистра).

    Args:
        query: Строка поиска (минимум 2 символа).
        exclude_player_id: Исключить игрока (обычно текущего).
        limit: Максимум записей.

    Returns:
        QuerySet игроков с select_related("user").
    """
    q = (query or "").strip()
    if len(q) < 2:
        return Player.objects.none()

    variants = _search_string_variants(q)
    if not variants:
        return Player.objects.none()

    base = Player.objects.filter(is_bye=False).select_related("user")
    if exclude_player_id is not None:
        base = base.exclude(pk=exclude_player_id)

    # Точное совпадение email (любой из вариантов регистра латиницы в локальной части)
    for v in variants:
        if "@" in v:
            by_email = base.filter(user__email__iexact=v.strip())
            if by_email.exists():
                return by_email[:limit]

    # Собираем OR по вариантам строки и полям (без SQL Lower — совместимо с SQLite + кириллица)
    name_email_q = Q()
    for v in variants:
        name_email_q |= Q(user__email__icontains=v)
        name_email_q |= Q(user__first_name__icontains=v)
        name_email_q |= Q(user__last_name__icontains=v)

    annotated = base.annotate(
        _full_name=Concat(
            Coalesce("user__first_name", Value("")),
            Value(" "),
            Coalesce("user__last_name", Value("")),
            output_field=CharField(),
        ),
    )
    full_q = Q()
    for v in variants:
        full_q |= Q(_full_name__icontains=v)

    return (
        annotated.filter(name_email_q | full_q)
        .distinct()
        .order_by("user__last_name", "user__first_name")[:limit]
    )
