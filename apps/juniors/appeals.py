"""Возражения представителей и решения организаторов с журналом."""

from typing import cast

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.users.models import User

from .models import AuditEvent, JuniorAppeal, JuniorMatch, JuniorTournament
from .notifications import notify_family
from .permissions import guardian_for, require_organizer


@transaction.atomic
def submit_appeal(
    *, actor: User, child_id: int, match_id: int, message: str
) -> JuniorAppeal:
    """Позволяет представителю оспорить только матч своего ребёнка."""
    match = JuniorMatch.objects.select_related("entry1__child", "entry2__child").get(
        pk=match_id
    )
    tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=match.tournament_id
    )
    entries = [
        entry
        for entry in (match.entry1, match.entry2)
        if entry and entry.child_id == child_id
    ]
    if not entries:
        raise ValidationError("Ребёнок не участвует в этом матче.")
    link = guardian_for(actor, entries[0].child)
    if (
        match.status not in ("completed", "walkover")
        or not message.strip()
        or len(message) > 2000
    ):
        raise ValidationError(
            "Укажите возражение до 2000 символов по опубликованному результату."
        )
    appeal, created = JuniorAppeal.objects.get_or_create(
        match=match,
        guardian=link,
        status="open",
        defaults={"tournament": tournament, "message": message.strip()},
    )
    if created:
        AuditEvent.objects.create(
            actor=actor,
            child=entries[0].child,
            action="appeal.created",
            details={"appeal_id": appeal.pk},
        )
        from apps.notifications.services import create_notification

        create_notification(
            event_key="junior.updated",
            user=tournament.organizer,
            message=f"Детская лига: возражение по матчу №{match.pk}.",
            url=f"/kids/tournaments/{tournament.pk}/manage/",
        )
    return cast(JuniorAppeal, appeal)


@transaction.atomic
def resolve_appeal(*, actor: User, appeal_id: int, response: str) -> JuniorAppeal:
    """Закрывает возражение решением; результат при необходимости исправляется отдельно."""
    appeal = JuniorAppeal.objects.select_related("tournament", "guardian__child").get(
        pk=appeal_id
    )
    JuniorTournament.objects.select_for_update(of=("self",)).get(
        pk=appeal.tournament_id
    )
    appeal = (
        JuniorAppeal.objects.select_for_update(of=("self",))
        .select_related("tournament", "guardian__child")
        .get(pk=appeal_id)
    )
    require_organizer(actor, appeal.tournament)
    if appeal.status != "open":
        raise ValidationError(
            "Возражение уже рассмотрено. Для нового вопроса создаётся новое обращение."
        )
    if not response.strip() or len(response) > 2000:
        raise ValidationError("Введите решение до 2000 символов.")
    appeal.status = "resolved"
    appeal.response = response.strip()
    appeal.reviewed_by = actor
    appeal.save(update_fields=["status", "response", "reviewed_by"])
    AuditEvent.objects.create(
        actor=actor,
        child=appeal.guardian.child,
        action="appeal.resolved",
        details={"appeal_id": appeal.pk},
    )
    notify_family(
        appeal.guardian.child,
        "Возражение по результату рассмотрено. Решение доступно в кабинете.",
    )
    return cast(JuniorAppeal, appeal)
