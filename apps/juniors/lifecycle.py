"""Смена представителей, исправления, отмены и передача истории взрослому."""

from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.users.models import Player, User

from .competition import eligibility
from .models import (
    AuditEvent,
    ChildConsent,
    ChildProfile,
    GuardianLink,
    JuniorEntry,
    JuniorRefund,
    JuniorTournament,
)
from .notifications import notify_family
from .permissions import require_organizer, require_verifier


@transaction.atomic
def reassign_entry(
    *, actor: User, entry_id: int, guardian_id: int, reason: str
) -> JuniorEntry:
    """Меняет ответственного представителя заявки, сохраняя исходного плательщика."""
    entry = JuniorEntry.objects.get(pk=entry_id)
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=entry.tournament_id
    )
    require_organizer(actor, tournament)
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=entry.child_id)
    entry = JuniorEntry.objects.select_for_update(of=("self",)).get(pk=entry_id)
    link = GuardianLink.objects.filter(
        pk=guardian_id, child=child, status="verified"
    ).first()
    if link is None:
        raise ValidationError("Новый представитель не имеет подтверждённых полномочий.")
    if not reason.strip() or tournament.status not in ("open", "closed", "active"):
        raise ValidationError("Укажите причину смены представителя действующей заявки.")
    if entry.payments.filter(status__in=["new", "pending"]).exists():
        raise ValidationError("Сначала урегулируйте незавершённую попытку оплаты.")
    eligibility(child, link, tournament)
    previous = entry.guardian_id
    entry.guardian = link
    entry.save(update_fields=["guardian"])
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="entry.representative_changed",
        details={
            "entry_id": entry.pk,
            "previous": previous,
            "new": link.pk,
            "reason": reason,
        },
    )
    notify_family(
        child, f"Ответственный представитель заявки на «{tournament.name}» обновлён."
    )
    return cast(JuniorEntry, entry)


@transaction.atomic
def revoke_guardian(*, actor: User, link_id: int, reason: str) -> GuardianLink:
    """Прекращает полномочия без удаления платежей и истории ребёнка."""
    require_verifier(actor)
    link = GuardianLink.objects.get(pk=link_id)
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=link.child_id)
    if not reason.strip():
        raise ValidationError("Укажите основание прекращения полномочий.")
    link.status = "revoked"
    link.reason = reason.strip()
    link.save(update_fields=["status", "reason"])
    ChildConsent.objects.filter(guardian=link, withdrawn_at__isnull=True).update(
        withdrawn_at=timezone.now()
    )
    if not child.guardians.filter(status="verified").exists():
        child.status = "suspended"
        child.save(update_fields=["status", "updated_at"])
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="guardian.revoked",
        details={"link_id": link.pk, "reason": reason},
    )
    return cast(GuardianLink, link)


@transaction.atomic
def add_representative(
    *, actor: User, child_id: int, user_id: int, relation: str
) -> GuardianLink:
    """Создаёт связь второго представителя без автоматической передачи доступа."""
    require_verifier(actor)
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    user = User.objects.get(pk=user_id, is_active=True)
    if child.status == "archived" or relation not in GuardianLink.Relation.values:
        raise ValidationError("Профиль в архиве или неверно указано представительство.")
    link, created = GuardianLink.objects.get_or_create(
        child=child, user=user, defaults={"relation": relation}
    )
    if not created:
        raise ValidationError(
            "У этого взрослого уже есть связь с ребёнком. Рассмотрите её отдельно."
        )
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="guardian.added_pending",
        details={"link_id": link.pk},
    )
    return cast(GuardianLink, link)


@transaction.atomic
def correct_profile(
    *, actor: User, child_id: int, data: dict[str, Any], evidence: str, reason: str
) -> ChildProfile:
    """Изменяет проверенные данные только после проверки и без нарушения текущих заявок."""
    require_verifier(actor)
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    if not evidence.strip() or not reason.strip() or child.status == "archived":
        raise ValidationError(
            "Укажите проверенные основания и причину изменения активного профиля."
        )
    allowed = {"first_name", "last_name", "birth_date", "city", "gender", "ball"}
    for key, value in data.items():
        if key in allowed and value not in (None, ""):
            setattr(child, key, value)
    child.full_clean()
    if "birth_date" in data:
        for entry in child.entries.filter(
            tournament__status="completed"
        ).select_related("tournament"):
            if (
                not entry.tournament.min_age
                <= child.age_at(entry.tournament.age_reference)
                <= entry.tournament.max_age
            ):
                raise ValidationError(
                    "Исправленная дата нарушает допуск к прошлому турниру. Сначала урегулируйте его результаты и рейтинг."
                )
    for entry in child.entries.filter(
        status__in=["confirmed", "payment"],
        tournament__status__in=["open", "closed", "active"],
    ).select_related("guardian", "tournament"):
        eligibility(child, entry.guardian, entry.tournament)
    if "birth_date" in data:
        child.birth_verified_at = timezone.now()
    child.save()
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="child.corrected",
        details={
            "fields": sorted(set(data) & allowed),
            "reason": reason,
            "evidence": evidence,
        },
    )
    notify_family(
        child,
        "Проверенные данные профиля обновлены. История прошлых категорий сохранена.",
    )
    return cast(ChildProfile, child)


@transaction.atomic
def transfer_to_adult(
    *, actor: User, child_id: int, user_id: int, evidence: str
) -> ChildProfile:
    """Передаёт историю существующему проверенному взрослому аккаунту после 18 лет."""
    require_verifier(actor)
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    target = User.objects.select_for_update(of=("self",)).get(
        pk=user_id, is_active=True
    )
    if (
        child.age_at(timezone.localdate()) < 18
        or not evidence.strip()
        or not target.email_verified
    ):
        raise ValidationError(
            "Требуются совершеннолетие, проверка личности и подтверждённый аккаунт получателя."
        )
    if child.adult_user_id:
        raise ValidationError("История уже передана взрослому владельцу.")
    if child.entries.filter(
        status__in=["confirmed", "payment"],
        tournament__status__in=["open", "closed", "active"],
    ).exists():
        raise ValidationError("Сначала урегулируйте действующие детские заявки.")
    player, created = Player.objects.get_or_create(
        user=target,
        defaults={
            "birth_date": child.birth_date,
            "city": child.city,
            "gender": child.gender,
        },
    )
    if not created and player.birth_date != child.birth_date:
        raise ValidationError(
            "Дата рождения взрослого профиля не совпадает. Требуется отдельная проверка."
        )
    child.adult_user = target
    child.status = "archived"
    child.save(update_fields=["adult_user", "status", "updated_at"])
    ChildConsent.objects.filter(
        guardian__child=child, withdrawn_at__isnull=True
    ).update(withdrawn_at=timezone.now())
    child.guardians.update(
        status="revoked", reason="История передана совершеннолетнему владельцу"
    )
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="child.transferred",
        details={"target_user_id": target.pk, "evidence": evidence},
    )
    return cast(ChildProfile, child)


@transaction.atomic
def cancel_tournament(
    *, actor: User, tournament_id: int, reason: str
) -> JuniorTournament:
    """Отменяет соревнование, снимает начисления и создаёт запросы полного возврата."""
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=tournament_id
    )
    require_organizer(actor, tournament)
    if not reason.strip():
        raise ValidationError("Укажите причину отмены.")
    if tournament.status == "cancelled":
        return cast(JuniorTournament, tournament)
    for entry in tournament.entries.select_related("child"):
        notify_family(
            entry.child,
            f"Турнир «{tournament.name}» отменён. Оплаченные взносы переданы на возврат.",
        )
        for payment in entry.payments.filter(status__in=["paid", "refund_pending"]):
            payment.status = "refund_pending"
            payment.save(update_fields=["status"])
            JuniorRefund.objects.get_or_create(
                payment=payment, defaults={"reason": f"Отмена турнира: {reason}"[:255]}
            )
    tournament.entries.update(status="withdrawn", reserved_until=None)
    tournament.awards.all().delete()
    from .models import JuniorRankingSnapshot
    from .ranking import category_key

    JuniorRankingSnapshot.objects.filter(
        season=tournament.season,
        ball=tournament.ball,
        category=category_key(tournament),
    ).update(valid=False)
    tournament.status = "cancelled"
    tournament.save(update_fields=["status"])
    AuditEvent.objects.create(
        actor=actor,
        action="tournament.cancelled",
        details={"tournament_id": tournament.pk, "reason": reason},
    )
    return cast(JuniorTournament, tournament)


@transaction.atomic
def reschedule_tournament(
    *, actor: User, tournament_id: int, starts_at, reason: str
) -> JuniorTournament:
    """Переносит ещё не начавшийся турнир после проверки возраста всех заявленных детей."""
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=tournament_id
    )
    require_organizer(actor, tournament)
    if (
        tournament.status not in ("draft", "open", "closed")
        or starts_at <= timezone.now()
        or not reason.strip()
    ):
        raise ValidationError(
            "Перенос возможен до старта на будущую дату с указанием причины."
        )
    if (
        starts_at < tournament.registration_ends_at
        or not tournament.season.starts_on
        <= timezone.localdate(starts_at)
        <= tournament.season.ends_on
    ):
        raise ValidationError(
            "Новая дата должна быть после окончания регистрации и внутри сезона."
        )
    previous = tournament.starts_at
    tournament.starts_at = starts_at
    for entry in tournament.entries.filter(
        status__in=["confirmed", "payment"]
    ).select_related("child", "guardian"):
        eligibility(entry.child, entry.guardian, tournament)
    tournament.save(update_fields=["starts_at"])
    tournament.entries.update(reminder_sent_at=None)
    for entry in tournament.entries.exclude(status="withdrawn").select_related("child"):
        notify_family(
            entry.child,
            f"Дата «{tournament.name}» изменена. Проверьте расписание и условия возврата.",
        )
    AuditEvent.objects.create(
        actor=actor,
        action="tournament.rescheduled",
        details={
            "tournament_id": tournament.pk,
            "previous": previous.isoformat(),
            "new": starts_at.isoformat(),
            "reason": reason,
        },
    )
    return cast(JuniorTournament, tournament)
