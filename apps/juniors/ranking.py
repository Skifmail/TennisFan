"""Прозрачный сезонный рейтинг TennisFan, независимый от взрослого FAN."""

from collections import defaultdict
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.users.models import User

from .models import (
    AuditEvent,
    JuniorAward,
    JuniorRankingSnapshot,
    JuniorSeason,
    JuniorTournament,
)
from .notifications import notify_family
from .permissions import require_organizer
from .services import public_child_label


def category_key(tournament: JuniorTournament) -> str:
    return f"{tournament.min_age}-{tournament.max_age}:{tournament.age_rule}:{tournament.gender}:singles"


def category_label(category: str) -> str:
    """Возвращает понятную подпись категории без внутренних кодов."""
    ages, rule, gender, _ = category.split(":")
    gender_name = {"open": "Все участники", "male": "Мальчики", "female": "Девочки"}[
        gender
    ]
    date_rule = "на 31 декабря" if rule == "year_end" else "на дату начала"
    return f"{ages.replace('-', '–')} лет · {date_rule} · {gender_name}"


def tournament_places(tournament: JuniorTournament) -> list[dict[str, Any]]:
    """Определяет места; при полном равенстве круговых показателей места разделяются."""
    entries = list(
        tournament.entries.filter(status="confirmed").select_related("child")
    )
    matches = list(tournament.matches.all())
    stats: dict[int, dict[str, Any]] = {
        entry.pk: {"entry": entry, "wins": 0, "played": 0, "difference": 0, "scored": 0}
        for entry in entries
    }
    for match in matches:
        if match.status in ("completed", "walkover") and match.winner_id in stats:
            stats[match.winner_id]["wins"] += 1
        if match.status == "completed":
            for entry_id, side in ((match.entry1_id, 0), (match.entry2_id, 1)):
                if entry_id in stats:
                    stats[entry_id]["played"] += 1
                    stats[entry_id]["difference"] += sum(
                        pair[side] - pair[1 - side] for pair in match.scores
                    )
                    stats[entry_id]["scored"] += sum(
                        pair[side] for pair in match.scores
                    )
    if tournament.format == "round_robin":
        ordered = sorted(
            stats.values(),
            key=lambda row: (
                -row["wins"],
                -row["difference"],
                -row["scored"],
                row["entry"].pk,
            ),
        )
        previous = None
        place = 0
        for index, row in enumerate(ordered, 1):
            key = (row["wins"], row["difference"], row["scored"])
            if key != previous:
                place = index
            row["place"] = place
            previous = key
        return ordered
    max_round = max((match.round_number for match in matches), default=1)
    final = next(
        (m for m in matches if m.round_number == max_round and m.position == 0), None
    )
    for row in stats.values():
        entry_id = row["entry"].pk
        loss = next(
            (
                m
                for m in matches
                if entry_id in (m.entry1_id, m.entry2_id)
                and m.status in ("completed", "walkover")
                and m.winner_id != entry_id
            ),
            None,
        )
        row["place"] = (
            1
            if final and final.winner_id == entry_id
            else (2 ** (max_round - loss.round_number) + 1 if loss else len(entries))
        )
    return sorted(stats.values(), key=lambda row: (row["place"], row["entry"].pk))


@transaction.atomic
def finalize(*, actor: User, tournament_id: int) -> JuniorTournament:
    """Один раз подводит итоги, повторное выполнение не дублирует очки."""
    tournament = (
        JuniorTournament.objects.select_for_update(of=("self",))
        .select_related("season")
        .get(pk=tournament_id)
    )
    require_organizer(actor, tournament)
    if tournament.status == "completed":
        return cast(JuniorTournament, tournament)
    if (
        tournament.status != "active"
        or not tournament.matches.exists()
        or tournament.matches.filter(status="pending").exists()
    ):
        raise ValidationError("Все матчи должны быть завершены до подведения итогов.")
    if (
        hasattr(tournament, "appeals")
        and tournament.appeals.filter(status="open").exists()
    ):
        raise ValidationError("Сначала рассмотрите возражения представителей.")
    tournament.awards.all().delete()
    season = tournament.season
    season = JuniorSeason.objects.select_for_update(of=("self",)).get(pk=season.pk)
    for row in tournament_places(tournament):
        place = row["place"]
        points = (
            season.points_winner
            if place == 1
            else (
                season.points_finalist
                if place == 2
                else (
                    season.points_semifinalist
                    if place <= 4
                    else season.points_participation
                )
            )
        )
        # Свободные круги и неявки не создают очков за несыгранный турнир.
        if not row["played"] or not tournament.ranked:
            points = 0
        JuniorAward.objects.create(
            tournament=tournament,
            child=row["entry"].child,
            season=season,
            place=place,
            points=points,
            ball=tournament.ball,
            category=category_key(tournament),
            city=tournament.city,
            rules_version=season.rules_version,
        )
    tournament.status = "completed"
    tournament.save(update_fields=["status"])
    for row in leaderboard(
        season=season, ball=tournament.ball, category=category_key(tournament)
    ):
        JuniorRankingSnapshot.objects.create(
            child_id=row["child_id"],
            tournament=tournament,
            season=season,
            ball=tournament.ball,
            category=category_key(tournament),
            position=row["place"],
            points=row["points"],
        )
    AuditEvent.objects.create(
        actor=actor,
        action="tournament.finalized",
        details={"tournament_id": tournament.pk, "rules_version": season.rules_version},
    )
    for entry in tournament.entries.filter(status="confirmed").select_related("child"):
        notify_family(
            entry.child,
            f"Подведены итоги «{tournament.name}». Результаты и очки доступны в кабинете.",
        )
    return cast(JuniorTournament, tournament)


def leaderboard(
    *, season: JuniorSeason, ball: str, category: str, city: str = ""
) -> list[dict[str, Any]]:
    """Суммирует лучшие N результатов только внутри одной соревновательной категории."""
    awards = JuniorAward.objects.filter(
        season=season, ball=ball, category=category, tournament__status="completed"
    ).select_related("child", "tournament")
    if city:
        awards = awards.filter(city=city)
    grouped: dict[int, list[JuniorAward]] = defaultdict(list)
    for award in awards:
        grouped[award.child_id].append(award)
    rows: list[dict[str, Any]] = []
    for items in grouped.values():
        best = sorted(items, key=lambda item: (-item.points, item.place, item.pk))[
            : season.best_results
        ]
        points = sum(item.points for item in best)
        if points:
            rows.append(
                {
                    "child_id": items[0].child_id,
                    "label": public_child_label(items[0].child),
                    "points": points,
                    "count": len(best),
                }
            )
    rows.sort(key=lambda row: (-row["points"], row["child_id"]))
    previous, place = None, 0
    for index, row in enumerate(rows, 1):
        if row["points"] != previous:
            place = index
        row["place"] = place
        previous = row["points"]
    return rows
