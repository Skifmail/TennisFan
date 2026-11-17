"""Обработать отклики на спарринги: авто-принятие и истечение срока.

Запуск: python manage.py process_sparring_responses

Каждые 15 минут команда:
1. Автоматически принимает первый ожидающий отклик, если автор не ответил
   за 48 часов, и создаёт матч.
2. Помечает отклики с истёкшим сроком как ``expired`` и пересчитывает
   дедлайны заявок.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.sparring.models import SparringRequest, SparringResponse
from apps.sparring.services import (
    auto_accept_sparring_response,
    expire_due_sparring_responses,
)


class Command(BaseCommand):
    """Принять просроченные отклики и закрыть истёкшие."""

    help = (
        "Автоматически создать матчи по откликам, которые автор не подтвердил "
        "за 48 часов, и пометить истёкшие отклики."
    )

    def add_arguments(self, parser) -> None:
        """Аргументы CLI."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать заявки, ничего не менять.",
        )

    def handle(self, *args, **options) -> None:
        """Обработать просроченные подтверждения и истёкшие отклики."""
        dry_run = bool(options.get("dry_run"))
        now = timezone.now()
        due_requests = list(
            SparringRequest.objects.filter(
                status=SparringRequest.Status.ACTIVE,
                auto_accept=True,
                match__isnull=True,
                confirmation_deadline__lte=now,
            ).order_by("confirmation_deadline")
        )

        if dry_run:
            expired = SparringResponse.objects.filter(
                status=SparringResponse.ResponseStatus.PENDING,
                expires_at__lte=now,
            ).count()
            self.stdout.write(
                f"Dry-run: заявок к авто-принятию — {len(due_requests)}, "
                f"истёкших откликов — {expired}."
            )
            for request in due_requests:
                self.stdout.write(f"  авто-матч по заявке #{request.pk}")
            return

        accepted = 0
        for request in due_requests:
            try:
                match = auto_accept_sparring_response(request.pk)
            except Exception as exc:  # noqa: BLE001 - одна заявка не должна ломать cron
                self.stderr.write(
                    f"Заявка #{request.pk}: не удалось создать матч: {exc}"
                )
                continue
            if match is not None:
                accepted += 1

        expired = expire_due_sparring_responses(now)
        self.stdout.write(
            f"Создано матчей автоматически: {accepted}. "
            f"Истёкших откликов: {expired}."
        )
