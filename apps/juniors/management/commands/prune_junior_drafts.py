"""Обезличивание заброшенных черновиков после согласованного срока хранения."""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from loguru import logger

from apps.juniors.models import AuditEvent, ChildProfile


class Command(BaseCommand):
    help = "Предпросмотр или обезличивание неиспользованных детских черновиков"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("Срок хранения должен быть положительным.")
        ids = list(
            ChildProfile.objects.filter(
                status="draft",
                created_at__lt=timezone.now() - timedelta(days=days),
                entries__isnull=True,
                guardians__consents__isnull=True,
            )
            .exclude(guardians__status="verified")
            .values_list("pk", flat=True)
            .distinct()
        )
        logger.info(
            "Неиспользованных черновиков к обезличиванию: {}. Применить: {}",
            len(ids),
            options["apply"],
        )
        if not options["apply"]:
            return
        for child_id in ids:
            with transaction.atomic():
                child = ChildProfile.objects.select_for_update(of=("self",)).get(
                    pk=child_id
                )
                if (
                    child.status != "draft"
                    or child.entries.exists()
                    or child.guardians.filter(status="verified").exists()
                    or child.guardians.filter(consents__isnull=False).exists()
                ):
                    continue
                child.guardians.all().delete()
                child.first_name = "Удалённый профиль"
                child.last_name = ""
                child.birth_date = date(1900, 1, 1)
                child.city = ""
                child.gender = ""
                child.status = "archived"
                child.save()
                AuditEvent.objects.create(
                    actor=None,
                    child=child,
                    action="draft.anonymized",
                    details={"retention_days": days},
                )
