"""Операции семейного кабинета с проверкой прав и журналом действий."""

from typing import Any, cast

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.users.models import User

from .legal import ACCURACY_CONFIRMATION, REPRESENTATIVE_CONFIRMATION
from .models import AuditEvent, ChildConsent, ChildProfile, GuardianLink, LegalRelease
from .notifications import notify_pending_guardian, notify_representative
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
    _update_photo(child, data, actor)
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
    notify_pending_guardian(link)
    return cast(ChildProfile, child)


@transaction.atomic
def update_draft(*, actor: User, child_id: int, data: dict[str, Any]) -> ChildProfile:
    """Редактирует профиль семьи, сохраняя проверенные сведения и допуск по мячу."""
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    link = guardian_for(actor, child, verified=False)
    if child.status == ChildProfile.Status.ARCHIVED:
        raise ValidationError("Архивный профиль нельзя редактировать.")
    locked = (
        bool(child.birth_verified_at)
        or child.status != "draft"
        or link.status == "verified"
    )
    identity = ("first_name", "last_name", "birth_date", "gender")
    if locked and any(
        key in data and data[key] != getattr(child, key) for key in identity
    ):
        raise ValidationError(
            "ФИО, дату рождения и пол после проверки исправляет администратор. Напишите на tennis@tennisfan.ru."
        )
    if (
        data.get("ball", child.ball) != child.ball
        and child.entries.filter(
            status__in=["preliminary", "confirmed", "payment", "waitlist"],
            tournament__status__in=["open", "closed", "active"],
        ).exists()
    ):
        raise ValidationError(
            "Нельзя менять мяч, пока есть действующая заявка на турнир. Сначала снимите заявку или обратитесь к организатору."
        )
    changed = []
    for field in (*identity, "city", "ball"):
        if field in data and data[field] != getattr(child, field):
            setattr(child, field, data[field])
            changed.append(field)
    _update_photo(child, data, actor)
    child.full_clean()
    child.save()
    AuditEvent.objects.create(
        actor=actor, child=child, action="child.updated", details={"fields": changed}
    )
    return cast(ChildProfile, child)


def _update_photo(child: ChildProfile, data: dict[str, Any], actor: User) -> None:
    """Сохраняет закрытое фото и подтверждение, удаляет заменённый файл после транзакции."""
    from .photos import prepare_photo

    old_name = child.photo.name
    photo = data.get("photo")
    if data.get("remove_photo"):
        child.photo = ""
    elif photo and photo != child.photo:
        if data.get("photo_accepted") is not True:
            raise ValidationError(
                "Подтвердите разрешение на фото для семейного кабинета."
            )
        child.photo = prepare_photo(photo)
        AuditEvent.objects.create(
            actor=actor,
            child=child,
            action="photo.accepted",
            details={
                "text": "Я разрешаю хранить фото ребёнка и показывать его только в семейном кабинете"
            },
        )
    else:
        return
    if old_name:
        storage = child.photo.storage
        transaction.on_commit(lambda: storage.delete(old_name))


@transaction.atomic
def _verify_guardian(
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
def _accept_consent(
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


@transaction.atomic
def activate_legal_release(*, actor: User, expected_version: str) -> LegalRelease:
    """Подключает готовую редакцию по решению владельца с сохранением прежних согласий."""
    from .legal import document_bundle

    if (
        not actor.is_active
        or not actor.is_staff
        or not actor.has_perm("juniors.change_legalrelease")
    ):
        raise PermissionDenied("Нет права включения согласий.")
    version, processing, publication = document_bundle()
    if expected_version != version:
        raise ValidationError(
            "Документы обновились. Откройте страницу ещё раз и ознакомьтесь с новой редакцией."
        )
    if not processing.strip() or not publication.strip():
        raise ValidationError("Не удалось получить полные тексты согласий.")
    release, _ = LegalRelease.objects.get_or_create(
        version=version,
        defaults={"processing_text": processing, "publication_text": publication},
    )
    release = LegalRelease.objects.select_for_update().get(pk=release.pk)
    if release.processing_text != processing or release.publication_text != publication:
        raise ValidationError(
            "Сохранённая редакция отличается от документов сайта. Обратитесь в поддержку."
        )
    if release.is_current and release.reviewed:
        return cast(LegalRelease, release)
    LegalRelease.objects.filter(is_current=True).exclude(pk=release.pk).update(
        is_current=False
    )
    release.reviewed = True
    release.is_current = True
    release.full_clean()
    release.save(update_fields=["reviewed", "is_current"])
    AuditEvent.objects.create(
        actor=actor,
        action="legal.activated",
        details={"version": version, "approval": "Утверждено платформой"},
    )
    return cast(LegalRelease, release)


@transaction.atomic
def ensure_default_legal_release() -> LegalRelease:
    """Подключает документы поставки, если платформа ещё не назначила текущую редакцию."""
    from .legal import document_bundle

    current = LegalRelease.objects.filter(is_current=True, reviewed=True).first()
    if current is not None:
        return cast(LegalRelease, current)
    version, processing, publication = document_bundle()
    release, _ = LegalRelease.objects.get_or_create(
        version=version,
        defaults={"processing_text": processing, "publication_text": publication},
    )
    release = LegalRelease.objects.select_for_update().get(pk=release.pk)
    current = LegalRelease.objects.filter(is_current=True, reviewed=True).first()
    if current is not None:
        return cast(LegalRelease, current)
    if release.processing_text != processing or release.publication_text != publication:
        raise ValidationError("Сохранённая редакция отличается от документов поставки.")
    LegalRelease.objects.filter(is_current=True).exclude(pk=release.pk).update(
        is_current=False
    )
    release.reviewed = True
    release.is_current = True
    release.full_clean()
    release.save(update_fields=["reviewed", "is_current"])
    AuditEvent.objects.create(action="legal.defaults", details={"version": version})
    return cast(LegalRelease, release)


def verify_guardian(
    *, actor: User, link_id: int, approved: bool, evidence: str, reason: str = ""
) -> GuardianLink:
    """Подтверждает представителя, затем обновляет его предварительные заявки."""
    link = _verify_guardian(
        actor=actor,
        link_id=link_id,
        approved=approved,
        evidence=evidence,
        reason=reason,
    )
    from .competition import refresh_preliminary_entries

    refresh_preliminary_entries(actor=actor, child_id=link.child_id)
    return cast(GuardianLink, link)


def accept_consent(
    *, actor: User, child_id: int, release_id: int, kind: str
) -> ChildConsent:
    """Сохраняет личное согласие и обновляет допуск предварительных заявок."""
    consent = _accept_consent(
        actor=actor, child_id=child_id, release_id=release_id, kind=kind
    )
    if kind == ChildConsent.Kind.PROCESSING:
        from .competition import refresh_preliminary_entries

        refresh_preliminary_entries(actor=actor, child_id=child_id)
    return cast(ChildConsent, consent)
