"""Сводка фактической обработки рассылки без обещания доставки или прочтения."""

from typing import Final

from django.db.models import Count
from django.utils import timezone

from .models import Broadcast, Delivery

DELIVERY_STATES: Final = (
    "queued",
    "processing",
    "accepted",
    "skipped",
    "failed",
    "uncertain",
)


def broadcast_progress(campaign: Broadcast) -> dict[str, object]:
    """Собирает этап и количества заданий по каналам одним агрегирующим запросом.

    Args:
        campaign: Рассылка с текущим состоянием формирования аудитории.

    Returns:
        Безопасная сводка без адресов, текстов сообщений и ключей провайдера.
    """
    channels = {
        channel: dict.fromkeys(DELIVERY_STATES, 0) for channel in ("push", "email")
    }
    for row in (
        Delivery.objects.filter(broadcast=campaign)
        .values("channel", "status")
        .annotate(count=Count("pk"))
    ):
        if row["channel"] in channels and row["status"] in DELIVERY_STATES:
            channels[row["channel"]][row["status"]] = row["count"]
    waiting = sum(
        values["queued"] + values["processing"] for values in channels.values()
    )
    failed = sum(values["failed"] for values in channels.values())
    uncertain = sum(values["uncertain"] for values in channels.values())
    return {
        "stage": broadcast_stage(
            campaign, waiting=waiting, failed=failed, uncertain=uncertain
        ),
        "terminal": campaign.status in {"cancelled", "expanded"} and not waiting,
        "channels": channels,
        "processed_users": campaign.processed_users,
        "inbox_created": campaign.inbox_created,
        "counters_complete": campaign.submission_id is not None,
        "as_of": timezone.now().isoformat(),
    }


def broadcast_stage(
    campaign: Broadcast, *, waiting: int, failed: int, uncertain: int
) -> str:
    """Отличает формирование аудитории от результатов транспортов.

    Args:
        campaign: Текущая рассылка.
        waiting: Ожидающие и обрабатываемые задания.
        failed: Задания с окончательной ошибкой.
        uncertain: Задания с неопределённым исходом.

    Returns:
        Короткий понятный этап без утверждения о фактическом прочтении.
    """
    if campaign.status == "draft":
        return "Черновик"
    if campaign.status == "cancelled":
        return "Остановлена"
    if campaign.status == "queued":
        return (
            "Подготовка получателей" if campaign.processed_users else "Ожидает запуска"
        )
    if waiting:
        return "Отправляется"
    if uncertain:
        return "Требует проверки"
    return "Завершена с ошибками" if failed else "Завершена"
