"""Уведомления представителям через единый центр без дублирования push."""

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
