"""Допуск и регистрация детей с атомарным распределением мест."""

from datetime import timedelta
from typing import cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.users.models import User

from .models import (
    AuditEvent,
    ChildConsent,
    ChildProfile,
    GuardianLink,
    JuniorEntry,
    JuniorTournament,
    LegalRelease,
)
from .notifications import notify_family
from .permissions import guardian_for, require_organizer

RESERVATION_MINUTES = 20


def eligibility(
    child: ChildProfile, link: GuardianLink, tournament: JuniorTournament
) -> None:
    """Проверяет подтверждения и спортивные условия независимо от интерфейса."""
    if child.status != ChildProfile.Status.ACTIVE or not child.birth_verified_at:
        raise ValidationError(
            "Профиль ребёнка ещё не активирован или возраст не проверен."
        )
    release = LegalRelease.objects.filter(reviewed=True, is_current=True).first()
    if (
        release is None
        or not ChildConsent.objects.filter(
            guardian=link, release=release, kind="processing", withdrawn_at__isnull=True
        ).exists()
    ):
        raise ValidationError(
            "Оформите актуальное согласие на обработку данных ребёнка."
        )
    age = child.age_at(tournament.age_reference)
    if not tournament.min_age <= age <= tournament.max_age:
        raise ValidationError(
            f"Требуемый возраст: {tournament.min_age}–{tournament.max_age} лет по правилам турнира."
        )
    if child.age_at(timezone.localdate()) >= 18:
        raise ValidationError(
            "Участник уже совершеннолетний. Обратитесь для передачи профиля."
        )
    if tournament.gender != "open" and child.gender != tournament.gender:
        raise ValidationError("Профиль не соответствует категории участников.")
    if child.ball != tournament.ball:
        raise ValidationError(
            "Ступень подготовки не соответствует мячу турнира. Изменение подтверждает организатор через поддержку."
        )


def expire_reservations(tournament: JuniorTournament) -> None:
    """Освобождает истёкшие резервы; поздняя оплата не подтверждает их повторно."""
    entries = JuniorEntry.objects.filter(
        tournament=tournament, status="payment", reserved_until__lte=timezone.now()
    ).select_related("child")
    for entry in entries:
        entry.status = "withdrawn"
        entry.save(update_fields=["status"])
        AuditEvent.objects.create(
            actor=None,
            child=entry.child,
            action="entry.expired",
            details={"entry_id": entry.pk},
        )
        notify_family(
            entry.child,
            f"Резерв в «{tournament.name}» истёк. Место освобождено; не оплачивайте старую заявку.",
        )


def occupied_seats(tournament: JuniorTournament) -> int:
    return int(
        tournament.entries.filter(
            Q(status="confirmed")
            | Q(status="payment", reserved_until__gt=timezone.now())
        ).count()
    )


@transaction.atomic
def register(
    *, actor: User, child_id: int, tournament_id: int, regulation_version: int
) -> JuniorEntry:
    """Создаёт уникальную заявку и резервирует последнее место под блокировкой."""
    tournament = (
        JuniorTournament.objects.select_for_update(of=("self",))
        .select_related("season")
        .get(pk=tournament_id)
    )
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    link = guardian_for(actor, child)
    now = timezone.now()
    if (
        tournament.status != "open"
        or now >= tournament.registration_ends_at
        or now >= tournament.starts_at
    ):
        raise ValidationError("Регистрация на турнир закрыта.")
    if regulation_version != tournament.regulation_version:
        raise ValidationError("Регламент изменился. Прочтите актуальную версию.")
    eligibility(child, link, tournament)
    expire_reservations(tournament)
    old = JuniorEntry.objects.filter(tournament=tournament, child=child).first()
    if old and old.status != "withdrawn":
        raise ValidationError("Ребёнок уже записан на этот турнир.")
    if (
        old
        and hasattr(old, "payments")
        and old.payments.filter(status__in=["paid", "refund_pending"]).exists()
    ):
        raise ValidationError(
            "Предыдущая оплата ещё не урегулирована. Обратитесь к организатору."
        )
    # Очередь не обходится новой заявкой.
    waiting = tournament.entries.filter(status="waitlist").exists()
    full = occupied_seats(tournament) >= tournament.capacity
    status = (
        "waitlist"
        if full or waiting
        else ("payment" if tournament.entry_fee else "confirmed")
    )
    values = {
        "guardian": link,
        "status": status,
        "amount": tournament.entry_fee,
        "reserved_until": (
            now + timedelta(minutes=RESERVATION_MINUTES)
            if status == "payment"
            else None
        ),
        "regulation_snapshot": {
            "version": tournament.regulation_version,
            "text": tournament.regulation,
            "ball": tournament.ball,
            "min_age": tournament.min_age,
            "max_age": tournament.max_age,
            "age_rule": tournament.age_rule,
            "reference_date": str(tournament.age_reference),
            "score_format": tournament.score_format,
            "fee": str(tournament.entry_fee),
            "season": tournament.season_id,
            "rules_version": tournament.season.rules_version,
            "starts_at": tournament.starts_at.isoformat(),
            "accepted_at": now.isoformat(),
        },
    }
    if old:
        for key, value in values.items():
            setattr(old, key, value)
        old.created_at = now
        old.save()
        entry = old
    else:
        entry = JuniorEntry.objects.create(tournament=tournament, child=child, **values)
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="entry.created",
        details={"entry_id": entry.pk, "status": status},
    )
    notify_family(child, f"{tournament.name}: {entry.get_status_display()}.")
    return cast(JuniorEntry, entry)


@transaction.atomic
def withdraw(*, actor: User, entry_id: int) -> JuniorEntry:
    """Снимает ребёнка до старта; оплата не теряет связь с исходным плательщиком."""
    entry = JuniorEntry.objects.select_related("child").get(pk=entry_id)
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=entry.tournament_id
    )
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=entry.child_id)
    guardian_for(actor, child)
    entry = JuniorEntry.objects.select_for_update(of=("self",)).get(pk=entry_id)
    if tournament.status in ("active", "completed"):
        raise ValidationError(
            "После старта снятие оформляет организатор с фиксацией результата."
        )
    entry.status = "withdrawn"
    entry.reserved_until = None
    entry.save(update_fields=["status", "reserved_until"])
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="entry.withdrawn",
        details={"entry_id": entry.pk},
    )
    notify_family(child, f"Заявка на «{tournament.name}» снята.")
    return cast(JuniorEntry, entry)


@transaction.atomic
def publish_tournament(*, actor: User, tournament_id: int) -> JuniorTournament:
    """Открывает регистрацию после проверки полного регламента."""
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=tournament_id
    )
    require_organizer(actor, tournament)
    if (
        tournament.status not in ("draft", "closed")
        or tournament.registration_ends_at <= timezone.now()
    ):
        raise ValidationError("Этот турнир нельзя открыть для регистрации.")
    tournament.full_clean()
    tournament.status = "open"
    tournament.save(update_fields=["status"])
    AuditEvent.objects.create(
        actor=actor,
        action="tournament.opened",
        details={"tournament_id": tournament.pk},
    )
    return cast(JuniorTournament, tournament)


@transaction.atomic
def promote_waitlist(*, actor: User, entry_id: int) -> JuniorEntry:
    """Предлагает свободное место по очереди после повторной проверки допуска."""
    entry = JuniorEntry.objects.select_related("child", "guardian").get(pk=entry_id)
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=entry.tournament_id
    )
    entry.child = ChildProfile.objects.select_for_update(of=("self",)).get(
        pk=entry.child_id
    )
    require_organizer(actor, tournament)
    if tournament.status != "open" or tournament.registration_ends_at <= timezone.now():
        raise ValidationError("Регистрация уже закрыта.")
    expire_reservations(tournament)
    first = (
        tournament.entries.filter(status="waitlist")
        .order_by("created_at", "pk")
        .first()
    )
    if first is None or first.pk != entry.pk:
        raise ValidationError("Предложите место первому участнику листа ожидания.")
    if occupied_seats(tournament) >= tournament.capacity:
        raise ValidationError("Свободных мест пока нет.")
    guardian_for(entry.guardian.user, entry.child)
    eligibility(entry.child, entry.guardian, tournament)
    entry.status = "payment" if entry.amount else "confirmed"
    entry.reserved_until = (
        timezone.now() + timedelta(minutes=RESERVATION_MINUTES)
        if entry.amount
        else None
    )
    entry.save(update_fields=["status", "reserved_until"])
    AuditEvent.objects.create(
        actor=actor,
        child=entry.child,
        action="entry.promoted",
        details={"entry_id": entry.pk},
    )
    notify_family(
        entry.child,
        f"Освободилось место в «{tournament.name}». Проверьте заявку и срок оплаты.",
    )
    return cast(JuniorEntry, entry)
