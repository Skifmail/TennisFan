"""Разобрать «зависшие» заявки на спарринг, созданные до авто-принятия.

Запуск: python manage.py close_stale_sparring_requests --dry-run
        python manage.py close_stale_sparring_requests --auto-accept

Заявки без матча старше ``--older-than`` дней закрываются. С флагом
``--auto-accept`` по заявкам с ожидающими откликами сначала создаётся матч:
это разовый backfill для записей, у которых ещё не было дедлайна.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.sparring.models import SparringRequest, SparringResponse
from apps.sparring.services import auto_accept_sparring_response

DEFAULT_OLDER_THAN_DAYS = 14


class Command(BaseCommand):
    """Закрыть старые активные заявки и при необходимости создать матчи."""

    help = (
        "Закрыть активные заявки на спарринг без матча старше указанного "
        "срока; с --auto-accept сначала принять ожидающий отклик."
    )

    def add_arguments(self, parser) -> None:
        """Аргументы CLI."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать заявки, ничего не менять.",
        )
        parser.add_argument(
            "--auto-accept",
            action="store_true",
            help="Создать матч по первому ожидающему отклику перед закрытием.",
        )
        parser.add_argument(
            "--older-than",
            type=int,
            default=DEFAULT_OLDER_THAN_DAYS,
            help="Возраст заявки в днях (по умолчанию 14).",
        )

    def handle(self, *args, **options) -> None:
        """Обработать старые заявки и вывести итоги."""
        dry_run = bool(options.get("dry_run"))
        auto_accept = bool(options.get("auto_accept"))
        older_than = int(options.get("older_than") or DEFAULT_OLDER_THAN_DAYS)
        cutoff = timezone.now() - timedelta(days=older_than)
        requests = list(
            SparringRequest.objects.filter(
                status=SparringRequest.Status.ACTIVE,
                match__isnull=True,
                created_at__lt=cutoff,
            ).order_by("created_at")
        )

        if dry_run:
            for request in requests:
                pending = request.responses.filter(
                    status=SparringResponse.ResponseStatus.PENDING
                ).count()
                self.stdout.write(
                    f"  заявка #{request.pk}: откликов в ожидании {pending}, "
                    f"создана {request.created_at:%d.%m.%Y}"
                )
            self.stdout.write(f"Dry-run: к обработке — {len(requests)}.")
            return

        matched = 0
        closed = 0
        for request in requests:
            if (
                auto_accept
                and request.responses.filter(
                    status=SparringResponse.ResponseStatus.PENDING
                ).exists()
            ):
                try:
                    match = auto_accept_sparring_response(request.pk, force=True)
                except Exception as exc:  # noqa: BLE001 - одна заявка не ломает разбор
                    self.stderr.write(
                        f"Заявка #{request.pk}: не удалось создать матч: {exc}"
                    )
                    continue
                if match is not None:
                    matched += 1
                    continue

            request.status = SparringRequest.Status.CLOSED
            request.confirmation_deadline = None
            request.save(
                update_fields=["status", "confirmation_deadline", "updated_at"]
            )
            request.responses.filter(
                status=SparringResponse.ResponseStatus.PENDING
            ).update(status=SparringResponse.ResponseStatus.REJECTED, expires_at=None)
            closed += 1

        self.stdout.write(f"Создано матчей: {matched}. Закрыто заявок: {closed}.")
