"""Проведение круговых турниров и сеток на выбывание с проверкой допуска."""

import random
import secrets
from typing import cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.users.models import User

from .competition import eligibility, expire_reservations
from .models import AuditEvent, ChildProfile, JuniorEntry, JuniorMatch, JuniorTournament
from .notifications import notify_family
from .permissions import guardian_for, require_organizer
from .rules import validate_score


def _advance(match: JuniorMatch) -> None:
    """Переносит победителя в фиксированный слот следующего круга."""
    if match.tournament.format != "elimination" or not match.winner_id:
        return
    target = JuniorMatch.objects.filter(
        tournament=match.tournament,
        round_number=match.round_number + 1,
        position=match.position // 2,
    ).first()
    if target:
        field = "entry1" if match.position % 2 == 0 else "entry2"
        setattr(target, field, match.winner)
        target.save(update_fields=[field])


@transaction.atomic
def start_tournament(*, actor: User, tournament_id: int) -> JuniorTournament:
    """Повторно проверяет допуск всех участников и один раз формирует сетку."""
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=tournament_id
    )
    require_organizer(actor, tournament)
    if tournament.status not in ("open", "closed") or tournament.matches.exists():
        raise ValidationError("Этот турнир уже запущен либо ещё не опубликован.")
    if timezone.now() < tournament.registration_ends_at:
        raise ValidationError("Сначала дождитесь окончания регистрации.")
    expire_reservations(tournament)
    if tournament.entries.filter(status="payment").exists():
        raise ValidationError(
            "Есть неоплаченные действующие резервы. Дождитесь оплаты или окончания резерва."
        )
    entries = list(
        tournament.entries.filter(status="confirmed").select_related(
            "child", "guardian__user"
        )
    )
    if len(entries) < 2:
        raise ValidationError(
            "Для старта требуется минимум два подтверждённых участника."
        )
    for entry in entries:
        entry.child = ChildProfile.objects.select_for_update(of=("self",)).get(
            pk=entry.child_id
        )
        guardian_for(entry.guardian.user, entry.child)
        eligibility(entry.child, entry.guardian, tournament)
    seed = secrets.randbits(32)
    random.Random(seed).shuffle(entries)
    if tournament.format == "round_robin":
        position = 0
        for index, first in enumerate(entries):
            for opponent in entries[index + 1 :]:
                JuniorMatch.objects.create(
                    tournament=tournament,
                    entry1=first,
                    entry2=opponent,
                    position=position,
                )
                position += 1
    else:
        size = 1 << (len(entries) - 1).bit_length()
        first_count = size // 2
        for pos in range(first_count):
            second: JuniorEntry | None = (
                entries[first_count + pos] if first_count + pos < len(entries) else None
            )
            JuniorMatch.objects.create(
                tournament=tournament,
                position=pos,
                entry1=entries[pos],
                entry2=second,
                status="pending" if second else "bye",
                winner=None if second else entries[pos],
            )
        round_number, count = 2, first_count // 2
        while count:
            for pos in range(count):
                JuniorMatch.objects.create(
                    tournament=tournament, round_number=round_number, position=pos
                )
            count //= 2
            round_number += 1
        for match in tournament.matches.filter(status="bye"):
            _advance(match)
    tournament.status = "active"
    tournament.save(update_fields=["status"])
    for entry in entries:
        notify_family(entry.child, f"Опубликована сетка «{tournament.name}».")
    AuditEvent.objects.create(
        actor=actor,
        action="tournament.started",
        details={
            "tournament_id": tournament.pk,
            "draw_seed": seed,
            "entries": [e.pk for e in entries],
        },
    )
    return cast(JuniorTournament, tournament)


def _invalidate_descendants(match: JuniorMatch, actor: User) -> None:
    target = JuniorMatch.objects.filter(
        tournament=match.tournament,
        round_number=match.round_number + 1,
        position=match.position // 2,
    ).first()
    if target is None or match.tournament.format != "elimination":
        return
    _invalidate_descendants(target, actor)
    AuditEvent.objects.create(
        actor=actor,
        action="match.invalidated",
        details={
            "match_id": target.pk,
            "scores": target.scores,
            "winner": target.winner_id,
        },
    )
    field = "entry1" if match.position % 2 == 0 else "entry2"
    setattr(target, field, None)
    target.winner = None
    target.scores = []
    target.status = "pending"
    target.completed_at = None
    target.save()


@transaction.atomic
def record_result(
    *,
    actor: User,
    match_id: int,
    scores: list[list[int]],
    walkover_side: int | None = None,
    reason: str = "",
) -> JuniorMatch:
    """Сохраняет результат; исправления очищают зависимые матчи с журналом прежнего счёта."""
    match = JuniorMatch.objects.select_related("tournament").get(pk=match_id)
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=match.tournament_id
    )
    require_organizer(actor, tournament)
    match = JuniorMatch.objects.select_for_update(of=("self",)).get(pk=match_id)
    if (
        tournament.status not in ("active", "completed")
        or not match.entry1_id
        or not match.entry2_id
        or match.status == "bye"
    ):
        raise ValidationError("Матч ещё не готов к вводу результата.")
    correcting = match.status in ("completed", "walkover")
    if correcting and not reason.strip():
        raise ValidationError(
            "Укажите причину исправления. Зависимые результаты будут сброшены."
        )
    if walkover_side is not None:
        if walkover_side not in (1, 2) or not reason.strip():
            raise ValidationError(
                "Для технического результата укажите победившую сторону и основание."
            )
        winner = walkover_side
        scores = []
    else:
        winner = validate_score(tournament.score_format, scores)
    old = {"match_id": match.pk, "scores": match.scores, "winner": match.winner_id}
    if correcting:
        _invalidate_descendants(match, actor)
    if tournament.status == "completed":
        # Рейтинг завершённого турнира аннулируется до повторного подведения итогов.
        if hasattr(tournament, "awards"):
            tournament.awards.all().delete()
        from .models import JuniorRankingSnapshot
        from .ranking import category_key

        JuniorRankingSnapshot.objects.filter(
            season=tournament.season,
            ball=tournament.ball,
            category=category_key(tournament),
        ).update(valid=False)
        tournament.status = "active"
        tournament.save(update_fields=["status"])
    match.winner_id = match.entry1_id if winner == 1 else match.entry2_id
    match.status = "walkover" if walkover_side else "completed"
    match.scores = scores
    match.reason = reason.strip()
    match.completed_at = timezone.now()
    match.save()
    _advance(match)
    AuditEvent.objects.create(
        actor=actor,
        action="match.corrected" if correcting else "match.recorded",
        details=old
        | {"new_scores": scores, "new_winner": match.winner_id, "reason": reason},
    )
    for entry in (match.entry1, match.entry2):
        if entry is None:
            continue
        notify_family(
            entry.child,
            f"{'Исправлен' if correcting else 'Опубликован'} результат матча в «{tournament.name}».",
        )
    return cast(JuniorMatch, match)


@transaction.atomic
def schedule_match(*, actor: User, match_id: int, scheduled_at) -> JuniorMatch:
    """Публикует расписание готового матча с уведомлением представителей."""
    match = JuniorMatch.objects.select_related(
        "tournament", "entry1__child", "entry2__child"
    ).get(pk=match_id)
    JuniorTournament.objects.select_for_update(of=("self",)).get(pk=match.tournament_id)
    require_organizer(actor, match.tournament)
    if (
        match.status != "pending"
        or not match.entry1_id
        or not match.entry2_id
        or scheduled_at < match.tournament.starts_at
    ):
        raise ValidationError(
            "Расписание задаётся готовому матчу не раньше начала турнира."
        )
    match.scheduled_at = scheduled_at
    match.save(update_fields=["scheduled_at"])
    for entry in (match.entry1, match.entry2):
        if entry is None:
            continue
        notify_family(
            entry.child,
            f"Расписание матча в «{match.tournament.name}» обновлено. Проверьте время в кабинете.",
        )
    AuditEvent.objects.create(
        actor=actor,
        action="match.scheduled",
        details={"match_id": match.pk, "scheduled_at": scheduled_at.isoformat()},
    )
    return cast(JuniorMatch, match)
