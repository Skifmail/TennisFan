"""Рассылка игрокам города о новой опубликованной заявке на спарринг."""

from __future__ import annotations

import logging
import threading
from urllib.parse import urlencode

from django.db import transaction
from django.db.models import QuerySet
from django.urls import reverse

from apps.core.db_context import background_connection
from apps.core.localities import locality_tokens
from apps.sparring.models import (
    DoublesMatchRequest,
    DoublesMatchRequestStatus,
    SparringRequest,
)
from apps.users.models import Notification, Player

logger = logging.getLogger(__name__)

KIND_SINGLES = "singles"
KIND_DOUBLES = "doubles"
KIND_TEAM = "team"

_KIND_LABELS: dict[str, str] = {
    KIND_SINGLES: "одиночный спарринг",
    KIND_DOUBLES: "парный спарринг",
    KIND_TEAM: "командный спарринг",
}

_PUBLISHED_DOUBLES = (
    DoublesMatchRequestStatus.OPEN,
    DoublesMatchRequestStatus.FORMING,
)


def same_city_player_ids(
    city: str, *, exclude_player_id: int | None = None
) -> list[int]:
    """Идентификаторы игроков с тем же населённым пунктом.

    Сравнение идёт по нормализованному названию: «Москва» и «г. Москва»
    считаются одним городом. Служебные bye и неактивные аккаунты не входят.

    Args:
        city: Населённый пункт заявки или турнира.
        exclude_player_id: Игрок, которого не включать. ``None`` — никого не исключать.

    Returns:
        list[int]: Первичные ключи подходящих игроков.
    """
    wanted = set(locality_tokens(city))
    if not wanted:
        return []
    rows = (
        Player.objects.filter(is_bye=False, user__is_active=True)
        .exclude(city="")
        .values_list("pk", "city")
    )
    if exclude_player_id is not None:
        rows = rows.exclude(pk=exclude_player_id)
    return [
        player_id
        for player_id, player_city in rows
        if wanted.intersection(locality_tokens(player_city))
    ]


def same_city_players(city: str, *, exclude_player_id: int) -> QuerySet[Player]:
    """Игроки с тем же населённым пунктом, кроме автора заявки.

    Args:
        city: Населённый пункт из заявки.
        exclude_player_id: Автор заявки, которому писать не нужно.

    Returns:
        QuerySet[Player]: Получатели с подгруженным пользователем.
    """
    matched_ids = same_city_player_ids(city, exclude_player_id=exclude_player_id)
    if not matched_ids:
        return Player.objects.none()
    return (
        Player.objects.filter(pk__in=matched_ids).select_related("user").order_by("pk")
    )


def sparring_public_path(*, kind: str, pk: int, sport: str) -> str:
    """Относительная ссылка на опубликованную заявку.

    Args:
        kind: ``singles``, ``doubles`` или ``team``.
        pk: Идентификатор заявки.
        sport: Код вида спорта для фильтра списка одиночных заявок.

    Returns:
        str: Путь с якорем на карточку либо страница парной заявки.
    """
    if kind == KIND_SINGLES:
        params = urlencode({"type": "singles", "sport": sport or "tennis"})
        return f"{reverse('sparring_list')}?{params}#sparring-{pk}"
    return reverse("doubles_detail", args=[pk])


def cabinet_message(*, player_name: str, city: str, kind_label: str) -> str:
    """Текст уведомления в личном кабинете, не длиннее 255 символов.

    Args:
        player_name: Имя автора заявки.
        city: Населённый пункт заявки.
        kind_label: «одиночный спарринг», «парный спарринг» или «командный спарринг».

    Returns:
        str: Сообщение для поля ``Notification.message``.
    """
    text = f"{player_name} ищет партнёров для спарринга ({kind_label}) в {city}."
    if len(text) > 255:
        return text[:252] + "..."
    return text


def notify_published_sparring(*, kind: str, pk: int) -> tuple[int, int]:
    """Создать уведомления в ЛК и отправить письма игрокам того же города.

    Автор заявки письма не получает. Сбой одного адреса не останавливает остальных.

    Args:
        kind: Тип заявки: ``singles``, ``doubles`` или ``team``.
        pk: Первичный ключ заявки.

    Returns:
        tuple[int, int]: Число записей в ЛК и успешно отправленных писем.
    """
    loaded = _load_published(kind, pk)
    if loaded is None:
        return 0, 0
    player_name, city, sport, author_id, kind_label = loaded
    url = sparring_public_path(kind=kind, pk=pk, sport=sport)
    message = cabinet_message(player_name=player_name, city=city, kind_label=kind_label)
    from apps.core.email_service import send_city_sparring_email

    lk_count = 0
    email_count = 0
    for player in same_city_players(city, exclude_player_id=author_id):
        user = player.user
        try:
            Notification.objects.create(user=user, message=message, url=url)
            lk_count += 1
        except Exception:
            logger.exception(
                "city sparring LK failed user_id=%s kind=%s pk=%s",
                getattr(user, "pk", None),
                kind,
                pk,
            )
        try:
            if send_city_sparring_email(
                user,
                player_name=player_name,
                city=city,
                sparring_path=url,
                kind_label=kind_label,
            ):
                email_count += 1
        except Exception:
            logger.exception(
                "city sparring email failed user_id=%s kind=%s pk=%s",
                getattr(user, "pk", None),
                kind,
                pk,
            )
    return lk_count, email_count


def schedule_published_sparring_notify(*, kind: str, pk: int) -> None:
    """Поставить рассылку после коммита заявки, чтобы не держать ответ формы.

    Args:
        kind: Тип заявки.
        pk: Первичный ключ заявки.
    """

    def _start() -> None:
        thread = threading.Thread(
            target=_notify_published_sparring_background,
            kwargs={"kind": kind, "pk": pk},
            daemon=True,
            name=f"sparring-city-{kind}-{pk}",
        )
        thread.start()

    transaction.on_commit(_start)


def _notify_published_sparring_background(*, kind: str, pk: int) -> None:
    """Фоновая отправка: своё соединение с БД и журнал итога."""
    with background_connection():
        try:
            lk_count, email_count = notify_published_sparring(kind=kind, pk=pk)
        except Exception:
            logger.exception("city sparring notify failed kind=%s pk=%s", kind, pk)
            return
        logger.info(
            "city sparring notify kind=%s pk=%s lk=%s email=%s",
            kind,
            pk,
            lk_count,
            email_count,
        )


def _load_published(kind: str, pk: int) -> tuple[str, str, str, int, str] | None:
    """Прочитать опубликованную заявку.

    Returns:
        Кортеж имени, города, спорта, id автора и подписи вида либо ``None``.
    """
    kind_label = _KIND_LABELS.get(kind)
    if kind_label is None:
        logger.warning("city sparring notify: unknown kind=%s pk=%s", kind, pk)
        return None
    if kind == KIND_SINGLES:
        sparring = (
            SparringRequest.objects.filter(pk=pk).select_related("player").first()
        )
        if sparring is None or sparring.status != SparringRequest.Status.ACTIVE:
            logger.info("city sparring notify: singles pk=%s is not active", pk)
            return None
        city = (sparring.city or "").strip()
        if not city:
            return None
        return (
            sparring.player.get_display_name(),
            city,
            sparring.sport or "tennis",
            sparring.player_id,
            kind_label,
        )
    request = (
        DoublesMatchRequest.objects.filter(pk=pk).select_related("created_by").first()
    )
    if request is None or request.status not in _PUBLISHED_DOUBLES:
        logger.info("city sparring notify: doubles pk=%s is not published", pk)
        return None
    city = (request.city or "").strip()
    if not city:
        return None
    return (
        request.created_by.get_display_name(),
        city,
        request.sport or "tennis",
        request.created_by_id,
        kind_label,
    )
