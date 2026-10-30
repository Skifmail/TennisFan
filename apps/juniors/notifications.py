"""Уведомления представителям через единый центр без дублирования push."""

from apps.notifications.services import create_notification
from apps.users.models import User

from .models import ChildProfile, GuardianLink


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
