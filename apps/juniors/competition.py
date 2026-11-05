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
)
from .notifications import notify_entry
from .permissions import guardian_for, require_organizer

RESERVATION_MINUTES = 20


def eligibility(
    child: ChildProfile,
    link: GuardianLink,
    tournament: JuniorTournament,
    *,
    preliminary: bool = False,
) -> None:
    """Проверяет подтверждения и спортивные условия независимо от интерфейса."""
    if link.status not in ("pending", "verified") or child.status not in (
        "draft",
        "active",
    ):
        raise ValidationError(
            "Профиль или полномочия представителя недоступны для записи."
        )
    if not preliminary and (
        link.status != "verified"
        or child.status != ChildProfile.Status.ACTIVE
        or not child.birth_verified_at
    ):
        raise ValidationError(
            "Профиль ребёнка ещё не активирован или возраст не проверен."
        )
    from .services import ensure_default_legal_release

    release = ensure_default_legal_release()
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
        Q(status="payment", reserved_until__lte=timezone.now())
        | Q(status="preliminary", tournament__starts_at__lte=timezone.now()),
        tournament=tournament,
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
        notify_entry(
            entry,
            f"Срок подтверждения заявки на «{tournament.name}» истёк. Место освобождено.",
        )


def occupied_seats(tournament: JuniorTournament) -> int:
    return int(
        tournament.entries.filter(
            Q(status__in=["confirmed", "preliminary"])
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
    if tournament.club_id and tournament.entry_fee:
        raise ValidationError(
            "Платная запись на детский турнир клуба пока недоступна. Обратитесь к организатору."
        )
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    link = guardian_for(actor, child, verified=False)
    now = timezone.now()
    if (
        tournament.status != "open"
        or now >= tournament.registration_ends_at
        or now >= tournament.starts_at
    ):
        raise ValidationError("Регистрация на турнир закрыта.")
    if regulation_version != tournament.regulation_version:
        raise ValidationError("Регламент изменился. Прочтите актуальную версию.")
    eligibility(child, link, tournament, preliminary=True)
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
        else admission_status(child, link, bool(tournament.entry_fee))
    )
    values = {
        "guardian": link,
        "status": status,
        "amount": tournament.entry_fee,
        "reminder_sent_at": None,
        "reserved_until": (
            min(now + timedelta(minutes=RESERVATION_MINUTES), tournament.starts_at)
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
    notify_entry(entry, f"{tournament.name}: {entry.get_status_display()}.")
    return cast(JuniorEntry, entry)


@transaction.atomic
def withdraw(*, actor: User, entry_id: int) -> JuniorEntry:
    """Снимает ребёнка до старта; оплата не теряет связь с исходным плательщиком."""
    entry = JuniorEntry.objects.select_related("child").get(pk=entry_id)
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=entry.tournament_id
    )
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=entry.child_id)
    guardian_for(actor, child, verified=False)
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
    notify_entry(entry, f"Заявка на «{tournament.name}» снята.")
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
    if (
        tournament.status not in ("open", "closed")
        or tournament.starts_at <= timezone.now()
    ):
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
    guardian_for(entry.guardian.user, entry.child, verified=False)
    eligibility(entry.child, entry.guardian, tournament, preliminary=True)
    entry.status = admission_status(entry.child, entry.guardian, bool(entry.amount))
    entry.reserved_until = (
        min(
            timezone.now() + timedelta(minutes=RESERVATION_MINUTES),
            tournament.starts_at,
        )
        if entry.status == "payment"
        else None
    )
    entry.save(update_fields=["status", "reserved_until"])
    AuditEvent.objects.create(
        actor=actor,
        child=entry.child,
        action="entry.promoted",
        details={"entry_id": entry.pk},
    )
    notify_entry(
        entry,
        f"Освободилось место в «{tournament.name}». Проверьте заявку и срок оплаты.",
    )
    return cast(JuniorEntry, entry)


def admission_status(child: ChildProfile, link: GuardianLink, paid: bool) -> str:
    """Отделяет предварительную заявку от допуска к оплате и участию."""
    if (
        link.status != "verified"
        or child.status != "active"
        or not child.birth_verified_at
    ):
        return "preliminary"
    return "payment" if paid else "confirmed"


def refresh_preliminary_entries(*, actor: User, child_id: int) -> None:
    """Обновляет заявки после проверки или согласия, сохраняя порядок блокировок."""
    for entry_id in JuniorEntry.objects.filter(
        child_id=child_id, status="preliminary"
    ).values_list("pk", flat=True):
        _refresh_preliminary_entry(actor=actor, entry_id=entry_id)


@transaction.atomic
def _refresh_preliminary_entry(*, actor: User, entry_id: int) -> None:
    """Повторно проверяет допуск и открывает оплату только подтверждённой семье."""
    source = JuniorEntry.objects.get(pk=entry_id)
    tournament = JuniorTournament.objects.select_for_update().get(
        pk=source.tournament_id
    )
    child = ChildProfile.objects.select_for_update().get(pk=source.child_id)
    entry = (
        JuniorEntry.objects.select_for_update()
        .select_related("guardian")
        .get(pk=entry_id)
    )
    if entry.status != "preliminary":
        return
    reason = ""
    try:
        if (
            tournament.status not in ("open", "closed")
            or tournament.starts_at <= timezone.now()
        ):
            raise ValidationError("Срок проверки перед турниром истёк.")
        eligibility(child, entry.guardian, tournament, preliminary=True)
    except ValidationError as error:
        reason = " ".join(error.messages)
        entry.status = "withdrawn"
    else:
        entry.status = admission_status(child, entry.guardian, bool(entry.amount))
        if entry.status == "preliminary":
            return
    entry.reserved_until = (
        min(
            timezone.now() + timedelta(minutes=RESERVATION_MINUTES),
            tournament.starts_at,
        )
        if entry.status == "payment"
        else None
    )
    entry.save(update_fields=["status", "reserved_until"])
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="entry.admitted" if not reason else "entry.withdrawn",
        details={"entry_id": entry.pk, "reason": reason},
    )
    notify_entry(entry, f"{tournament.name}: {entry.get_status_display()}. {reason}")
