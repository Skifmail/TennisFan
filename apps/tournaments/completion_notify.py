"""Рассылка итогов турнира участникам после первой финализации."""

from __future__ import annotations

import html
import logging
import threading
from collections import defaultdict
from typing import Any

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification, Player, User

from .models import (
    SeasonPoints,
    Tournament,
    TournamentPlayerResult,
    TournamentStatus,
)

logger = logging.getLogger(__name__)

_ROUND_CAPTIONS: dict[str, str] = {
    TournamentPlayerResult.RoundEliminated.SF[0]: "полуфинала",
    TournamentPlayerResult.RoundEliminated.R2[0]: "второго круга",
    TournamentPlayerResult.RoundEliminated.R1[0]: "первого круга",
}
_ROUND_STAT: dict[str, str] = {
    TournamentPlayerResult.RoundEliminated.SF[0]: "ПФ",
    TournamentPlayerResult.RoundEliminated.R2[0]: "2 кр.",
    TournamentPlayerResult.RoundEliminated.R1[0]: "1 кр.",
}


def complete_tournament_and_notify(tournament: Tournament) -> None:
    """Поставить статус «Завершён» и один раз разослать итоги после коммита.

    Идемпотентность держит ``completion_notified_at``, а не сам статус:
    если статус уже COMPLETED, но письма ещё не уходили — рассылка планируется.

    Args:
        tournament: Турнир, который только что финализирован.
    """
    tournament_pk = tournament.pk
    with transaction.atomic():
        if tournament.status != TournamentStatus.COMPLETED:
            tournament.status = TournamentStatus.COMPLETED
            tournament.save(update_fields=["status"])
        if tournament.completion_notified_at:
            return
        transaction.on_commit(
            lambda pk=tournament_pk: dispatch_completion_notify(pk),
        )


def dispatch_completion_notify(tournament_pk: int) -> None:
    """Запустить рассылку итогов: в тестах синхронно, иначе в фоне.

    Args:
        tournament_pk: Идентификатор турнира.
    """
    if getattr(settings, "COMPLETION_NOTIFY_SYNC", False):
        _notify_completed_safe(tournament_pk)
        return
    thread = threading.Thread(
        target=_notify_completed_safe,
        args=(tournament_pk,),
        daemon=True,
        name=f"notify_completed_{tournament_pk}",
    )
    thread.start()


def _notify_completed_safe(tournament_pk: int) -> None:
    """Обёртка, чтобы исключение рассылки не роняло поток или on_commit.

    Args:
        tournament_pk: Идентификатор турнира.
    """
    try:
        notify_tournament_completed(tournament_pk)
    except Exception:
        logger.exception(
            "Completion notify crashed tournament=%s",
            tournament_pk,
        )


def notify_tournament_completed(tournament_pk: int) -> int:
    """Отправить письма, ЛК и Telegram участникам завершённого турнира.

    Идемпотентно: повторный вызов не шлёт рассылку снова.
    Флаг ``completion_notified_at`` ставится только если есть результаты
    и турнир всё ещё в статусе COMPLETED.

    Args:
        tournament_pk: Идентификатор турнира.

    Returns:
        int: Сколько писем удалось отправить.
    """
    claimed_at = None
    with transaction.atomic():
        locked = (
            Tournament.objects.select_for_update()
            .select_related("club")
            .filter(
                pk=tournament_pk,
                status=TournamentStatus.COMPLETED,
                completion_notified_at__isnull=True,
            )
            .first()
        )
        if locked is None:
            return 0
        has_results = locked.fan_results.filter(player__is_bye=False).exists()
        if not has_results:
            logger.info(
                "Tournament %s completed notify: no player results, skip",
                locked.pk,
            )
            return 0
        claimed_at = timezone.now()
        locked.completion_notified_at = claimed_at
        locked.save(update_fields=["completion_notified_at"])

    try:
        return _deliver_completion_notifications(locked)
    except Exception:
        logger.exception(
            "Completion notify delivery failed tournament=%s",
            tournament_pk,
        )
        Tournament.objects.filter(
            pk=tournament_pk,
            completion_notified_at=claimed_at,
        ).update(completion_notified_at=None)
        return 0


def _deliver_completion_notifications(tournament: Tournament) -> int:
    """Собрать контекст и разослать уведомления участникам с результатом.

    Args:
        tournament: Завершённый турнир с проставленным ``completion_notified_at``.

    Returns:
        int: Число успешно отправленных писем.
    """
    from apps.core.email_service import send_tournament_completed_email
    from apps.telegram_bot.notifications import send_to_user_by_user
    from apps.tournaments.withdraw import get_withdrawn_player_ids

    withdrawn_ids = get_withdrawn_player_ids(tournament)
    results = list(
        tournament.fan_results.select_related(
            "player", "player__user", "player__season_points"
        ).filter(
            player__is_bye=False,
        )
    )
    if not results:
        logger.info(
            "Tournament %s completed notify: results disappeared, skip",
            tournament.pk,
        )
        return 0

    podium = _build_podium(results)
    base_payload = _shared_email_payload(tournament)
    tournament_url = str(base_payload["tournament_url"])
    url_path = reverse("tournament_detail", args=[tournament.slug])
    sent = 0

    for result in results:
        player = result.player
        if player is None or player.pk in withdrawn_ids:
            continue
        user = getattr(player, "user", None)
        if user is None or getattr(player, "is_bye", False):
            continue

        try:
            place, trophy_place, place_stat = _resolve_place(result)
            fan_points = int(result.fan_points or 0)
            copy = _build_copy(
                first_name=_first_name(user),
                place=place,
                trophy_place=trophy_place,
                tournament_name=tournament.name,
                round_eliminated=result.round_eliminated,
            )
            zero_points_note = ""
            if fan_points == 0:
                copy["intro"] = copy["intro"].replace(
                    ", сезонные очки начислены.",
                    ".",
                )
                zero_points_note = "Сезонные очки не начислены"
            season_total = _season_total(player)
            context = {
                **base_payload,
                **copy,
                "user": user,
                "user_name": user.get_display_name() or user.email,
                "place": place,
                "place_stat": place_stat,
                "trophy_place": trophy_place,
                "trophy_url": (
                    f"{base_payload['base_url']}/static/images/trophy-place-{trophy_place}.png"
                    if trophy_place
                    else ""
                ),
                "fan_points": fan_points,
                "season_points": season_total,
                "points_awarded": fan_points > 0,
                "zero_points_note": zero_points_note,
                "podium": _mark_podium_you(podium, player.pk),
            }
        except Exception:
            logger.exception(
                "Completion notify context failed tournament=%s user=%s",
                tournament.pk,
                user.pk,
            )
            continue

        try:
            if send_tournament_completed_email(user, tournament, context):
                sent += 1
        except Exception:
            logger.exception(
                "Completion email failed tournament=%s user=%s",
                tournament.pk,
                user.pk,
            )

        try:
            Notification.objects.create(
                user=user,
                message=_lk_message(tournament.name, place, fan_points),
                url=url_path,
            )
        except Exception:
            logger.exception(
                "Completion LK notify failed tournament=%s user=%s",
                tournament.pk,
                user.pk,
            )

        try:
            send_to_user_by_user(
                user,
                _telegram_text(
                    tournament_name=tournament.name,
                    place=place,
                    place_stat=place_stat,
                    fan_points=fan_points,
                    trophy_place=trophy_place,
                ),
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "🔗 Страница турнира", "url": tournament_url}],
                    ],
                },
                skip_email=True,
            )
        except Exception:
            logger.exception(
                "Completion telegram notify failed tournament=%s user=%s",
                tournament.pk,
                user.pk,
            )

    logger.info(
        "Tournament %s completion notify: %s emails sent",
        tournament.pk,
        sent,
    )
    return sent


def _shared_email_payload(tournament: Tournament) -> dict[str, Any]:
    """Общие поля письма: ссылки, логотип, город, даты.

    Args:
        tournament: Турнир.

    Returns:
        dict[str, Any]: Контекст, общий для всех получателей.
    """
    from django.conf import settings

    from apps.core.email_service import _get_site_base_url

    base_url = _get_site_base_url()
    start_str = (
        tournament.start_date.strftime("%d.%m.%Y") if tournament.start_date else ""
    )
    end_str = tournament.end_date.strftime("%d.%m.%Y") if tournament.end_date else ""
    dates = " – ".join(part for part in (start_str, end_str) if part)
    support_email = getattr(settings, "DEFAULT_FROM_EMAIL", "tennis@tennisfan.ru")
    return {
        "tournament": tournament,
        "tournament_name": tournament.name,
        "tournament_url": (
            f"{base_url}{reverse('tournament_detail', args=[tournament.slug])}"
        ),
        "city": getattr(tournament, "city", "") or "",
        "dates": dates,
        "base_url": base_url,
        "logo_url": f"{base_url}/static/images/logo.png",
        "support_email": support_email,
    }


def _resolve_place(
    result: TournamentPlayerResult,
) -> tuple[int | None, int | None, str]:
    """Определить отображаемое место, кубок и подпись в блоке статистики.

    Args:
        result: Итог игрока в турнире.

    Returns:
        tuple[int | None, int | None, str]: Место, место кубка (1–3) и текст статистики.
    """
    if result.place:
        place = int(result.place)
        trophy = place if place in (1, 2, 3) else None
        return place, trophy, str(place)

    round_elim = result.round_eliminated
    if round_elim == TournamentPlayerResult.RoundEliminated.WINNER:
        return 1, 1, "1"
    if round_elim == TournamentPlayerResult.RoundEliminated.FINAL:
        return 2, 2, "2"
    return None, None, _ROUND_STAT.get(round_elim, "—")


def _build_copy(
    *,
    first_name: str,
    place: int | None,
    trophy_place: int | None,
    tournament_name: str,
    round_eliminated: str,
) -> dict[str, str]:
    """Собрать тему и тексты письма под место игрока.

    Args:
        first_name: Имя для обращения.
        place: Числовое место или None.
        trophy_place: 1–3 если положен кубок.
        tournament_name: Название турнира.
        round_eliminated: Код раунда вылета (FAN).

    Returns:
        dict[str, str]: Поля шаблона subject/headline/subhead/greeting/intro/cta.
    """
    named = bool(first_name)
    if trophy_place == 1:
        greeting = f"{first_name}, это ваше золото." if named else "Это ваше золото."
        return {
            "subject": f"TennisFan: вы победили в «{tournament_name}»",
            "headline": "Поздравляем!",
            "subhead": "Вы — победитель турнира",
            "greeting": greeting,
            "intro": (
                "Турнир завершён. Вы заняли первое место — кубок уже в вашем профиле, "
                "сезонные очки начислены."
            ),
            "trophy_note": "Кубок за 1 место уже отображается в профиле",
            "cta_text": "Таблица результатов и рейтинг уже обновлены.",
        }
    if trophy_place == 2:
        greeting = (
            f"{first_name}, поздравляем с подиумом."
            if named
            else "Поздравляем с подиумом."
        )
        return {
            "subject": f"TennisFan: серебро в «{tournament_name}»",
            "headline": "Серебро ваше",
            "subhead": "2 место в турнире",
            "greeting": greeting,
            "intro": (
                "Турнир завершён. Вы заняли второе место — кубок уже в вашем профиле, "
                "сезонные очки начислены."
            ),
            "trophy_note": "Кубок за 2 место уже отображается в профиле",
            "cta_text": "Таблица результатов и рейтинг уже обновлены.",
        }
    if trophy_place == 3:
        greeting = (
            f"{first_name}, поздравляем с подиумом."
            if named
            else "Поздравляем с подиумом."
        )
        return {
            "subject": f"TennisFan: бронза в «{tournament_name}»",
            "headline": "Бронза ваша",
            "subhead": "3 место в турнире",
            "greeting": greeting,
            "intro": (
                "Турнир завершён. Вы вошли в тройку — кубок за 3 место уже в профиле, "
                "сезонные очки начислены."
            ),
            "trophy_note": "Кубок за 3 место уже отображается в профиле",
            "cta_text": "Таблица результатов и рейтинг уже обновлены.",
        }

    greeting = f"{first_name}, спасибо за игру." if named else "Спасибо за игру."
    if place:
        subject = f"TennisFan: турнир «{tournament_name}» завершён — ваше {place} место"
        subhead = f"Ваш результат — {place} место"
    else:
        subject = f"TennisFan: турнир «{tournament_name}» завершён"
        caption = _ROUND_CAPTIONS.get(round_eliminated)
        subhead = f"Вы дошли до {caption}" if caption else "Ваш результат уже в таблице"
    return {
        "subject": subject,
        "headline": "Турнир завершён",
        "subhead": subhead,
        "greeting": greeting,
        "intro": (
            "Турнир завершён. Ваше место и сезонные очки уже в рейтинге — "
            "таблица результатов доступна на странице турнира."
        ),
        "trophy_note": "",
        "cta_text": "Следующий старт — на странице турниров.",
    }


def _build_podium(results: list[TournamentPlayerResult]) -> list[dict[str, Any]]:
    """Собрать подиум 2–1–3 для письма.

    Args:
        results: Итоги всех игроков турнира.

    Returns:
        list[dict[str, Any]]: Слоты подиума в визуальном порядке (2, 1, 3).
    """
    ids: dict[int, set[int]] = defaultdict(set)
    lines: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for result in results:
        place, _, _ = _resolve_place(result)
        if place not in (1, 2, 3):
            continue
        player = result.player
        user = getattr(player, "user", None)
        first = (getattr(user, "first_name", "") or "").strip()
        last = (getattr(user, "last_name", "") or "").strip()
        display = player.get_display_name()
        ids[place].add(player.pk)
        lines[place].append((first, last if last else display))

    slots: list[dict[str, Any]] = []
    bar_class = {1: "g", 2: "s", 3: "b"}
    for visual_place in (2, 1, 3):
        if visual_place not in ids:
            continue
        name_line1, name_line2 = _podium_name_lines(lines[visual_place])
        slots.append(
            {
                "place": visual_place,
                "player_ids": ids[visual_place],
                "name_line1": name_line1,
                "name_line2": name_line2,
                "bar_class": bar_class[visual_place],
                "is_you": False,
            }
        )
    return slots


def _podium_name_lines(people: list[tuple[str, str]]) -> tuple[str, str]:
    """Сжать имена слота подиума в две строки.

    Args:
        people: Пары (имя, фамилия) игроков этого места.

    Returns:
        tuple[str, str]: Первая и вторая строка карточки.
    """
    if not people:
        return "", ""
    if len(people) == 1:
        first, last = people[0]
        if first and last and first != last:
            return first, last
        return last or first, ""
    joined = " / ".join(
        f"{first} {last}".strip() if first else last for first, last in people
    )
    return joined, ""


def _mark_podium_you(
    podium: list[dict[str, Any]], player_id: int
) -> list[dict[str, Any]]:
    """Скопировать подиум и подсветить слот текущего игрока.

    Args:
        podium: Общий подиум турнира.
        player_id: Идентификатор получателя письма.

    Returns:
        list[dict[str, Any]]: Подиум с флагом ``is_you``.
    """
    marked: list[dict[str, Any]] = []
    for slot in podium:
        item = dict(slot)
        item["is_you"] = player_id in slot["player_ids"]
        marked.append(item)
    return marked


def _first_name(user: User) -> str:
    """Имя для обращения в письме.

    Args:
        user: Получатель.

    Returns:
        str: Имя или пустая строка.
    """
    name = (user.first_name or "").strip()
    if name:
        return name
    display = user.get_display_name()
    if display and "@" not in display:
        return display.split()[0]
    return ""


def _season_total(player: Player) -> int:
    """Текущие сезонные очки игрока.

    Args:
        player: Игрок.

    Returns:
        int: Очки сезона или 0.
    """
    try:
        return int(player.season_points.current_season_points)
    except SeasonPoints.DoesNotExist:
        return 0


def _lk_message(tournament_name: str, place: int | None, fan_points: int) -> str:
    """Текст уведомления в личный кабинет.

    Args:
        tournament_name: Название турнира.
        place: Место или None.
        fan_points: Начисленные сезонные очки.

    Returns:
        str: Сообщение не длиннее 255 символов.
    """
    place_part = f"Ваше место: {place}. " if place else ""
    points_part = f"+{fan_points} очков." if fan_points else "Очки не начислены."
    message = f"Турнир «{tournament_name}» завершён. {place_part}{points_part}"
    if len(message) > 255:
        return message[:252] + "..."
    return message


def _telegram_text(
    *,
    tournament_name: str,
    place: int | None,
    place_stat: str,
    fan_points: int,
    trophy_place: int | None,
) -> str:
    """Текст уведомления в Telegram.

    Args:
        tournament_name: Название турнира.
        place: Числовое место.
        place_stat: Подпись места, если числа нет.
        fan_points: Очки за турнир.
        trophy_place: 1–3 если есть кубок.

    Returns:
        str: HTML-текст для бота.
    """
    place_line = f"Ваше место: {place}" if place else f"Ваш результат: {place_stat}"
    points_line = (
        f"+{fan_points} сезонных очков" if fan_points else "Сезонные очки не начислены"
    )
    trophy_line = "\nКубок уже в профиле." if trophy_place else ""
    safe_name = html.escape(tournament_name)
    return (
        f"🏆 <b>Турнир завершён</b>\n\n"
        f"«{safe_name}»\n"
        f"{html.escape(place_line)}\n"
        f"{html.escape(points_line)}"
        f"{html.escape(trophy_line)}"
    )
