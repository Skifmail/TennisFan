"""Сервис журналирования действий на платформе (лента активности).

Модуль предоставляет единую точку входа :func:`log_activity` для записи событий
в журнал :class:`apps.core.models.PlatformActivityEvent`. Запись события никогда
не должна прерывать основной бизнес-процесс, поэтому все ошибки логируются и
подавляются.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone

from apps.core.models import PlatformActivityEvent, PlatformDashboardSeen

if TYPE_CHECKING:
    from apps.users.models import Player, User

logger = logging.getLogger(__name__)

PLATFORM_ACTIVITY_UNSEEN_CACHE_PREFIX = "platform_activity_unseen"
HOME_ACTIVITY_FEED_LIMIT = 25
HOME_ACTIVITY_SEEN_COOKIE = "home_activity_seen"
HOME_ACTIVITY_SEEN_MAX_AGE = 60 * 60 * 24 * 365

ActivityEventType = str | PlatformActivityEvent.EventType | tuple[str, ...]


def normalize_event_type(event_type: ActivityEventType) -> str:
    """Привести тип события к строковому значению для записи в БД.

    Args:
        event_type: Член ``EventType`` или уже нормализованная строка.

    Returns:
        str: Значение ``event_type`` для поля модели.
    """
    if isinstance(event_type, PlatformActivityEvent.EventType):
        return str(event_type.value)
    if isinstance(event_type, tuple):
        return str(event_type[0])
    return event_type


def resolve_actor_name(user: User | None) -> str:
    """Получить отображаемое имя пользователя для снимка в событии.

    Args:
        user: Пользователь платформы или None.

    Returns:
        str: ФИО/email пользователя или пустая строка.
    """
    if user is None:
        return ""
    from apps.users.display import format_user_display_name

    return format_user_display_name(user)


def resolve_actor_role(user: User | None) -> str:
    """Определить статус (роль) пользователя на платформе для снимка в событии.

    Приоритет ролей: Админ → Тренер → Организатор клуба → Игрок. Снимок
    сохраняется в событии, поэтому отражает роль на момент действия.

    Args:
        user: Пользователь платформы или None.

    Returns:
        str: Человекочитаемая роль («Админ», «Тренер», «Организатор клуба»,
        «Игрок») либо пустая строка для системных событий.
    """
    if user is None:
        return ""
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return "Админ"
    try:
        from apps.training.models import Coach

        if Coach.objects.filter(user=user, is_active=True).exists():
            return "Тренер"
    except Exception:  # noqa: BLE001
        pass
    try:
        from apps.clubs.models import ClubMember, ClubMemberRole, ClubMemberStatus

        if ClubMember.objects.filter(
            user=user,
            status=ClubMemberStatus.ACTIVE,
            role__in=[ClubMemberRole.ADMIN, ClubMemberRole.MANAGER],
        ).exists():
            return "Организатор клуба"
    except Exception:  # noqa: BLE001
        pass
    return "Игрок"


def player_user(player: Player | None) -> User | None:
    """Безопасно извлечь пользователя из профиля игрока.

    Args:
        player: Профиль игрока или None.

    Returns:
        User | None: Связанный пользователь, либо None для bye/пустого игрока.
    """
    if player is None or getattr(player, "is_bye", False):
        return None
    return getattr(player, "user", None)


def log_activity(
    *,
    event_type: ActivityEventType,
    actor: User | None = None,
    actor_name: str = "",
    actor_role: str | None = None,
    description: str = "",
    amount: Decimal | int | float | None = None,
    currency: str = "RUB",
    target_url: str = "",
    metadata: dict[str, Any] | None = None,
    dedupe_key: str = "",
    created_at: datetime | None = None,
) -> PlatformActivityEvent | None:
    """Записать событие активности в журнал платформы.

    Функция отказоустойчива: любые ошибки записи логируются и не пробрасываются,
    чтобы не ломать основной сценарий (регистрацию, оплату и т.д.). При наличии
    ``dedupe_key`` повторная запись того же события игнорируется.

    Args:
        event_type: Тип события из ``PlatformActivityEvent.EventType``.
        actor: Пользователь, совершивший действие (может быть None).
        actor_name: Явное имя действующего лица; если пусто — берётся из ``actor``.
        actor_role: Явная роль; если None — вычисляется из ``actor``.
        description: Человекочитаемое описание действия.
        amount: Сумма для событий-оплат (иначе None).
        currency: Код валюты суммы.
        target_url: Относительный URL связанного объекта.
        metadata: Дополнительные данные события.
        dedupe_key: Стабильный ключ источника для защиты от дубликатов.
        created_at: Время события; по умолчанию — текущий момент.

    Returns:
        PlatformActivityEvent | None: Созданное (или существующее) событие, либо
        None при ошибке записи.
    """
    try:
        event_type_value = normalize_event_type(event_type)
        resolved_name = actor_name or resolve_actor_name(actor)
        resolved_role = (
            actor_role if actor_role is not None else resolve_actor_role(actor)
        )
        amount_value: Decimal | None
        if amount is None:
            amount_value = None
        elif isinstance(amount, Decimal):
            amount_value = amount
        else:
            amount_value = Decimal(str(amount))

        defaults: dict[str, Any] = {
            "event_type": event_type_value,
            "actor": actor,
            "actor_name": resolved_name,
            "actor_role": resolved_role,
            "description": description[:500],
            "amount": amount_value,
            "currency": currency or "RUB",
            "target_url": target_url[:500],
            "metadata": metadata or {},
        }
        if created_at is not None:
            defaults["created_at"] = created_at

        if dedupe_key:
            with transaction.atomic():
                created_event, _created = PlatformActivityEvent.objects.get_or_create(
                    dedupe_key=dedupe_key,
                    defaults=defaults,
                )
            assert isinstance(created_event, PlatformActivityEvent)
            return created_event

        created_event = PlatformActivityEvent.objects.create(dedupe_key="", **defaults)
        assert isinstance(created_event, PlatformActivityEvent)
        return created_event
    except IntegrityError:
        # Гонка по dedupe_key — событие уже записано другим процессом.
        if dedupe_key:
            existing = PlatformActivityEvent.objects.filter(
                dedupe_key=dedupe_key
            ).first()
            return cast(PlatformActivityEvent | None, existing)
        return None
    except Exception as exc:  # noqa: BLE001 — журнал не должен ронять бизнес-логику
        logger.warning(
            "Не удалось записать событие активности (%s): %s",
            normalize_event_type(event_type),
            exc,
        )
        return None


def user_is_platform_staff(user: User | None) -> bool:
    """Проверить, может ли пользователь видеть панель управления платформы.

    Args:
        user: Пользователь платформы или None.

    Returns:
        bool: True для staff/superuser.
    """
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (user.is_staff or user.is_superuser)
    )


def count_unseen_platform_activity(user: User) -> int:
    """Посчитать непросмотренные события ленты для staff-пользователя.

    Args:
        user: Staff-пользователь платформы.

    Returns:
        int: Количество событий, созданных после последнего просмотра панели.
    """
    if not user_is_platform_staff(user):
        return 0

    last_seen_raw = (
        PlatformDashboardSeen.objects.filter(user_id=user.pk)
        .values_list("last_seen_event_id", flat=True)
        .first()
    )
    last_seen_id = int(last_seen_raw or 0)
    return int(PlatformActivityEvent.objects.filter(id__gt=last_seen_id).count())


def has_unseen_platform_activity(user: User) -> bool:
    """Проверить наличие новых событий ленты для индикатора в меню.

    Args:
        user: Staff-пользователь платформы.

    Returns:
        bool: True, если есть хотя бы одно непросмотренное событие.
    """
    return count_unseen_platform_activity(user) > 0


def mark_platform_dashboard_seen(user: User) -> None:
    """Зафиксировать просмотр панели управления и сбросить индикатор новых событий.

    Args:
        user: Staff-пользователь, открывший панель управления.
    """
    if not user_is_platform_staff(user):
        return
    latest_event_id = (
        PlatformActivityEvent.objects.order_by("-id")
        .values_list("id", flat=True)
        .first()
        or 0
    )
    PlatformDashboardSeen.objects.update_or_create(
        user=user,
        defaults={
            "last_seen_event_id": latest_event_id,
            "seen_at": timezone.now(),
        },
    )
    cache.delete(f"{PLATFORM_ACTIVITY_UNSEEN_CACHE_PREFIX}:{user.pk}:{latest_event_id}")


def get_public_home_activity_events(
    *,
    limit: int = HOME_ACTIVITY_FEED_LIMIT,
) -> list[PlatformActivityEvent]:
    """Вернуть последние публичные события для ленты на главной странице.

    В выборку не попадают оплаты, отклонения, заявки и прочие события,
    которые не предназначены для просмотра другими посетителями сайта.
    Игроки, скрытые с главной (``is_hidden_on_home``), тоже не показываются.

    Args:
        limit: Максимальное число записей в ленте.

    Returns:
        list[PlatformActivityEvent]: Свежие публичные события по убыванию даты.
    """
    from apps.users.models import Player

    hidden_user_ids = Player.objects.filter(is_hidden_on_home=True).values("user_id")
    return list(
        PlatformActivityEvent.objects.filter(
            event_type__in=PlatformActivityEvent.PUBLIC_FEED_EVENT_TYPES
        )
        .exclude(actor_id__in=hidden_user_ids)
        .select_related("actor", "actor__player")
        .order_by("-created_at", "-id")[: max(1, int(limit))]
    )


def parse_home_activity_seen_id(raw: str | None) -> int | None:
    """Разобрать cookie с id последнего просмотренного события ленты.

    Args:
        raw: Сырое значение cookie или ``None``, если cookie ещё нет.

    Returns:
        int | None: Id события либо ``None`` для первого визита.
    """
    if raw is None or raw == "":
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def format_new_home_activity_label(count: int) -> str:
    """Собрать подпись бейджа новых событий с русской формой числительного.

    Args:
        count: Количество новых событий.

    Returns:
        str: Например «1 новое» или «5 новых». Пустая строка, если новых нет.
    """
    total = max(0, int(count))
    if total == 0:
        return ""
    mod10 = total % 10
    mod100 = total % 100
    if mod10 == 1 and mod100 != 11:
        word = "новое"
    elif mod10 in {2, 3, 4} and mod100 not in {12, 13, 14}:
        word = "новых"
    else:
        word = "новых"
    return f"{total} {word}"


def annotate_home_activity_new_events(
    events: list[PlatformActivityEvent],
    *,
    seen_id: int | None,
) -> int:
    """Пометить события, появившиеся после последнего просмотра ленты.

    Первый визит (``seen_id is None``) ничего не подсвечивает: иначе вся лента
    выглядела бы как непрочитанная.

    Args:
        events: События публичной ленты.
        seen_id: Id последнего просмотренного события или ``None``.

    Returns:
        int: Сколько событий в выборке считаются новыми.
    """
    new_count = 0
    for event in events:
        is_new = seen_id is not None and event.id > seen_id
        event.is_new = is_new
        if is_new:
            new_count += 1
    return new_count


def set_home_activity_seen_cookie(
    response: HttpResponse,
    event_id: int,
    *,
    secure: bool,
) -> None:
    """Записать cookie с последним просмотренным событием ленты на главной.

    Args:
        response: HTTP-ответ, в который пишется cookie.
        event_id: Id события, до которого лента считается просмотренной.
        secure: Ставить флаг Secure (для HTTPS).
    """
    response.set_cookie(
        HOME_ACTIVITY_SEEN_COOKIE,
        str(int(event_id)),
        max_age=HOME_ACTIVITY_SEEN_MAX_AGE,
        path="/",
        samesite="Lax",
        secure=secure,
        httponly=False,
    )
