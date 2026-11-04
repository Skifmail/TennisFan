"""Операции семейного кабинета с проверкой прав и журналом действий."""

from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.users.models import User

from .legal import ACCURACY_CONFIRMATION, REPRESENTATIVE_CONFIRMATION
from .models import AuditEvent, ChildConsent, ChildProfile, GuardianLink, LegalRelease
from .notifications import notify_representative
from .permissions import guardian_for, require_verifier


def _declarations(data: dict[str, Any]) -> dict[str, str]:
    if data.get("representative") is not True or data.get("accurate") is not True:
        raise ValidationError(
            "Подтвердите, что вы законный представитель ребёнка и указали достоверные сведения."
        )
    return {
        "representative": REPRESENTATIVE_CONFIRMATION,
        "accurate": ACCURACY_CONFIRMATION,
    }


@transaction.atomic
def create_child(*, actor: User, data: dict[str, Any], relation: str) -> ChildProfile:
    """Создаёт закрытый черновик и связь, ожидающую проверки."""
    if not actor.is_authenticated or not actor.is_active:
        raise ValidationError("Войдите в аккаунт законного представителя.")
    if not actor.email or not actor.email_verified:
        raise ValidationError(
            "Подтвердите email в своём аккаунте перед добавлением ребёнка."
        )
    if relation not in GuardianLink.Relation.values:
        raise ValidationError("Укажите основание представительства.")
    declarations = _declarations(data)
    release = data.get("legal_release")
    if data.get("processing_accepted") is not True or not isinstance(
        release, LegalRelease
    ):
        raise ValidationError(
            "До сохранения профиля подтвердите отдельное согласие на обработку данных ребёнка."
        )
    release = (
        LegalRelease.objects.select_for_update()
        .filter(pk=release.pk, reviewed=True, is_current=True)
        .first()
    )
    if release is None:
        raise ValidationError("Версия согласия устарела. Обновите страницу.")
    allowed = {"first_name", "last_name", "birth_date", "city", "gender", "ball"}
    child = ChildProfile(
        **{key: value for key, value in data.items() if key in allowed}
    )
    child.full_clean()
    child.save()
    link = GuardianLink.objects.create(child=child, user=actor, relation=relation)
    ChildConsent.objects.create(
        guardian=link,
        release=release,
        kind=ChildConsent.Kind.PROCESSING,
        text_snapshot=release.processing_text,
    )
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="consent.accepted",
        details={
            "kind": "processing",
            "version": release.version,
            "stage": "profile_creation",
        },
    )
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="child.created",
        details={"declarations": declarations, "relation": relation},
    )
    return cast(ChildProfile, child)


@transaction.atomic
def update_draft(*, actor: User, child_id: int, data: dict[str, Any]) -> ChildProfile:
    """Позволяет исправить собственный черновик до проверки документов."""
    declarations = _declarations(data)
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    link = guardian_for(actor, child, verified=False)
    if (
        child.status != ChildProfile.Status.DRAFT
        or link.status == GuardianLink.Status.VERIFIED
    ):
        raise ValidationError("Данные проверенного профиля изменяются через поддержку.")
    for field in ("first_name", "last_name", "birth_date", "city", "gender", "ball"):
        if field in data:
            setattr(child, field, data[field])
    child.full_clean()
    child.save()
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="child.updated",
        details={"declarations": declarations},
    )
    return cast(ChildProfile, child)


@transaction.atomic
def verify_guardian(
    *, actor: User, link_id: int, approved: bool, evidence: str, reason: str = ""
) -> GuardianLink:
    """Фиксирует очную проверку личности, совершеннолетия, полномочий и возраста ребёнка."""
    require_verifier(actor)
    link = GuardianLink.objects.select_related("child").get(pk=link_id)
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=link.child_id)
    link = GuardianLink.objects.select_for_update(of=("self",)).get(pk=link.pk)
    if actor.pk == link.user_id:
        raise ValidationError("Нельзя подтверждать собственные полномочия.")
    if approved and not evidence.strip():
        raise ValidationError(
            "Укажите способ проверки личности, совершеннолетия и полномочий."
        )
    if not approved and not reason.strip():
        raise ValidationError("Укажите понятную причину отказа.")
    if approved:
        child.full_clean()
        child.birth_verified_at = timezone.now()
        child.save(update_fields=["birth_verified_at", "updated_at"])
    link.status = (
        GuardianLink.Status.VERIFIED if approved else GuardianLink.Status.REJECTED
    )
    link.verified_by = actor
    link.verified_at = timezone.now()
    link.evidence = evidence.strip()
    link.reason = reason.strip()
    link.save()
    if approved and child.status in (
        ChildProfile.Status.DRAFT,
        ChildProfile.Status.SUSPENDED,
    ):
        if ChildConsent.objects.filter(
            guardian=link,
            kind="processing",
            withdrawn_at__isnull=True,
            release__reviewed=True,
            release__is_current=True,
        ).exists():
            child.status = ChildProfile.Status.ACTIVE
            child.save(update_fields=["status", "updated_at"])
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="guardian.verified" if approved else "guardian.rejected",
        details={"link_id": link.pk, "reason": reason},
    )
    notify_representative(
        link.user,
        child,
        (
            "Проверка представителя пройдена. Статус профиля и согласий доступен в кабинете."
            if approved
            else "Проверка представителя отклонена. Причина доступна в кабинете."
        ),
    )
    return cast(GuardianLink, link)


@transaction.atomic
def accept_consent(
    *, actor: User, child_id: int, release_id: int, kind: str
) -> ChildConsent:
    """Получает конкретное согласие без повторного запроса при входе."""
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    link = guardian_for(actor, child)
    release = LegalRelease.objects.filter(
        pk=release_id, reviewed=True, is_current=True
    ).first()
    if release is None or kind not in ChildConsent.Kind.values:
        raise ValidationError("Версия согласия устарела. Обновите страницу.")
    if (
        kind == ChildConsent.Kind.PUBLICATION
        and child.status != ChildProfile.Status.ACTIVE
    ):
        raise ValidationError(
            "Публикация имени станет доступна после проверки представителя и активации профиля."
        )
    text = (
        release.processing_text
        if kind == ChildConsent.Kind.PROCESSING
        else release.publication_text
    )
    if not text.strip():
        raise ValidationError("Текст согласия не подготовлен. Обратитесь в поддержку.")
    consent, created = ChildConsent.objects.get_or_create(
        guardian=link,
        release=release,
        kind=kind,
        withdrawn_at__isnull=True,
        defaults={"text_snapshot": text},
    )
    if created:
        AuditEvent.objects.create(
            actor=actor,
            child=child,
            action="consent.accepted",
            details={"kind": kind, "version": release.version},
        )
    if (
        kind == ChildConsent.Kind.PROCESSING
        and child.status in (ChildProfile.Status.DRAFT, ChildProfile.Status.SUSPENDED)
        and child.birth_verified_at
    ):
        child.status = ChildProfile.Status.ACTIVE
        child.save(update_fields=["status", "updated_at"])
    return cast(ChildConsent, consent)


@transaction.atomic
def withdraw_consent(*, actor: User, child_id: int, kind: str) -> None:
    """Прекращает публикацию для ребёнка либо приостанавливает новые записи."""
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    guardian_for(actor, child, verified=False)
    if kind not in ChildConsent.Kind.values:
        raise ValidationError("Неизвестный вид согласия.")
    # Спор о публикации решается в пользу закрытости, независимо от второго представителя.
    ChildConsent.objects.filter(
        guardian__child=child, kind=kind, withdrawn_at__isnull=True
    ).update(withdrawn_at=timezone.now())
    if kind == ChildConsent.Kind.PROCESSING:
        child.status = ChildProfile.Status.SUSPENDED
        child.save(update_fields=["status", "updated_at"])
    AuditEvent.objects.create(
        actor=actor, child=child, action="consent.withdrawn", details={"kind": kind}
    )


def publication_is_allowed(child: ChildProfile) -> bool:
    """Проверяет разрешение для всех открытых страниц, включая прошлые результаты."""
    return bool(
        child.status == ChildProfile.Status.ACTIVE
        and ChildConsent.objects.filter(
            guardian__child=child,
            guardian__status=GuardianLink.Status.VERIFIED,
            guardian__user__is_active=True,
            kind=ChildConsent.Kind.PUBLICATION,
            withdrawn_at__isnull=True,
            release__reviewed=True,
        ).exists()
    )


def public_child_label(child: ChildProfile) -> str:
    """Возвращает имя и фамилию только при действующем разрешении."""
    return str(child) if publication_is_allowed(child) else child.public_label
