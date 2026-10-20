"""Модерация заявок стрингеров и уведомления администрации."""

from functools import partial
from typing import cast

from django.conf import settings
from django.core.mail import get_connection
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from apps.notifications.infrastructure.email import EventEmail
from apps.notifications.services import center_enabled, create_notification
from apps.users.models import User

from .models import (
    StringerApplication,
    StringerCompany,
    StringerCompanyContact,
    StringerCompanyPhoto,
    StringerPage,
)


def _send_email(address: str, message: str, url: str) -> None:
    """Отправляет письмо после фиксации заявки, сохраняя её при сбое SMTP."""
    try:
        EventEmail(
            subject="Новая заявка стрингера",
            body=f"{message}\n\n{settings.SITE_URL.rstrip('/')}{url}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[address],
            event_key="stringer.application_submitted",
            capture_event=False,
            connection=get_connection(timeout=3),
        ).send()
    except Exception:
        logger.exception("Не удалось отправить уведомление о заявке стрингера")


def notify_stringer_application(application: StringerApplication) -> None:
    """Уведомляет активных модераторов через ЛК и существующую очередь доставки."""
    url = reverse("admin:content_stringerapplication_change", args=[application.pk])
    message = f"Новая заявка стрингера: {application.name}. Требуется рассмотрение."
    recipients = [
        user
        for user in User.objects.filter(is_active=True, is_staff=True)
        if user.has_perm("content.change_stringerapplication")
    ]
    addresses: set[str] = set()
    for user in recipients:
        create_notification(
            event_key="stringer.application_submitted",
            user=user,
            message=message,
            url=url,
        )
        if user.email:
            addresses.add(user.email.casefold())
            if not center_enabled():
                transaction.on_commit(partial(_send_email, user.email, message, url))
    admin_email = settings.ADMIN_NOTIFICATIONS_EMAIL
    if admin_email and admin_email.casefold() not in addresses:
        transaction.on_commit(partial(_send_email, admin_email, message, url))


@transaction.atomic
def review_stringer_application(
    application_id: int, *, actor: User, approve: bool
) -> StringerApplication:
    """Однократно публикует или отклоняет заявку под блокировкой строки."""
    if (
        not actor.is_active
        or not actor.is_staff
        or not actor.has_perm("content.change_stringerapplication")
    ):
        raise PermissionError("Недостаточно прав для рассмотрения заявки.")
    application = cast(
        StringerApplication,
        StringerApplication.objects.select_for_update().get(pk=application_id),
    )
    if application.status != StringerApplication.Status.PENDING:
        return application
    if approve:
        company = StringerCompany.objects.create(
            stringer_page=StringerPage.get_singleton(),
            name=application.name,
            address=application.address,
            price=application.price,
            description=application.description,
            is_active=True,
        )
        for kind, value in (
            ("phone", application.phone),
            ("telegram", application.telegram),
            ("whatsapp", application.whatsapp),
            ("max", application.max_contact),
        ):
            if value:
                StringerCompanyContact.objects.create(
                    company=company, contact_type=kind, value=value
                )
        for order, photo in enumerate(application.photos.order_by("pk")):
            StringerCompanyPhoto.objects.create(
                company=company,
                image=photo.image.name,
                caption=photo.caption,
                order=order,
            )
        application.company = company
        application.status = StringerApplication.Status.APPROVED
    else:
        application.status = StringerApplication.Status.REJECTED
    application.reviewed_by = actor
    application.reviewed_at = timezone.now()
    application.save(update_fields=["company", "status", "reviewed_by", "reviewed_at"])
    return application
