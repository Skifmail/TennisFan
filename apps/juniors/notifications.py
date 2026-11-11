"""Уведомления представителям и проверяющим через единый центр без дублирования push."""

from functools import partial

from django.conf import settings
from django.core.mail import get_connection
from django.db import transaction
from django.urls import reverse
from loguru import logger

from apps.notifications.infrastructure.email import EventEmail
from apps.notifications.services import create_notification
from apps.users.models import User

from .models import ChildProfile, GuardianLink, JuniorEntry, JuniorMatch


def notify_representative(
    user: User, child: ChildProfile, message: str, *, url: str = ""
) -> None:
    """Сохраняет событие ЛК, а центр ставит разрешённые email и push в очередь."""
    create_notification(
        event_key="junior.updated",
        user=user,
        message=f"{child.first_name}: {message}"[:255],
        url=url or f"/kids/family/{child.pk}/",
    )


def notify_family(child: ChildProfile, message: str) -> None:
    """Выбирает действующих получателей без раскрытия документов и полных ФИО."""
    for link in child.guardians.filter(
        status=GuardianLink.Status.VERIFIED,
        receive_notifications=True,
        user__is_active=True,
    ).select_related("user"):
        notify_representative(link.user, child, message)


def notify_entry(entry: JuniorEntry, message: str) -> None:
    """Уведомляет заявителя до проверки и проверенных представителей без повторов."""
    notify_family(entry.child, message)
    link = entry.guardian
    if link.status == "pending" and link.receive_notifications and link.user.is_active:
        notify_representative(link.user, entry.child, message)


def notify_match(match: JuniorMatch, message: str) -> None:
    """Отправляет семьям прямую ссылку на матч без повторов для общего представителя."""
    child_ids = [entry.child_id for entry in (match.entry1, match.entry2) if entry]
    seen: set[int] = set()
    for link in GuardianLink.objects.filter(
        child_id__in=child_ids,
        status="verified",
        receive_notifications=True,
        user__is_active=True,
    ).select_related("user", "child"):
        if link.user_id in seen:
            continue
        seen.add(link.user_id)
        notify_representative(
            link.user, link.child, message, url=f"/kids/matches/{match.pk}/"
        )


def _send_pending_guardian_email(address: str, message: str, url: str) -> None:
    """Отправляет письмо после фиксации заявки, сохраняя её при сбое SMTP."""
    try:
        EventEmail(
            subject="Детская лига: представитель на проверке",
            body=f"{message}\n\n{settings.SITE_URL.rstrip('/')}{url}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[address],
            event_key="junior.guardian_pending",
            capture_event=False,
            connection=get_connection(timeout=3),
        ).send()
    except Exception:
        logger.exception("Не удалось отправить письмо о проверке представителя")


def notify_pending_guardian(link: GuardianLink) -> None:
    """Сообщает проверяющим о новой заявке через ЛК, push и общий почтовый адрес."""
    child = link.child
    url = reverse("admin:juniors_guardianlink_changelist")
    message = (
        f"На проверку: {child.first_name} {child.last_name} — {link.user.email}. "
        "Подтвердите полномочия законного представителя."
    )[:255]
    for user in User.objects.filter(is_active=True, is_staff=True):
        if not user.has_perm("juniors.verify_representative"):
            continue
        create_notification(
            event_key="junior.guardian_pending", user=user, message=message, url=url
        )
    admin_email = settings.ADMIN_NOTIFICATIONS_EMAIL.strip()
    if admin_email:
        transaction.on_commit(
            partial(_send_pending_guardian_email, admin_email, message, url)
        )
