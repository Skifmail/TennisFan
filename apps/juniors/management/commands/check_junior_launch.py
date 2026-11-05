"""Проверка конфигурации детской лиги без изменения данных и внешних запросов."""

import json
import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count

from apps.juniors.legal import document_bundle
from apps.juniors.models import LegalRelease


class Command(BaseCommand):
    """Собирает обезличенный отчёт для проверки контейнера на рабочем сервере."""

    help = "Проверяет настройки, миграции, документы и приватное хранилище детской лиги; ничего не меняет"

    def handle(self, *args: Any, **options: Any) -> None:
        """Выводит JSON без персональных данных, реквизитов и секретов."""
        executor = MigrationExecutor(connection)
        pending = [
            f"{migration.app_label}.{migration.name}"
            for migration, _ in executor.migration_plan(
                executor.loader.graph.leaf_nodes()
            )
        ]
        tables = set(connection.introspection.table_names())
        legal: dict[str, Any] = {
            "current_count": None,
            "texts_present": False,
            "matches_bundled_documents": False,
        }
        if "juniors_legalrelease" in tables:
            current = LegalRelease.objects.filter(is_current=True, reviewed=True)
            legal["current_count"] = current.count()
            release = current.first()
            if release is not None:
                version, processing, publication = document_bundle()
                legal.update(
                    {
                        "texts_present": bool(
                            release.processing_text.strip()
                            and release.publication_text.strip()
                        ),
                        "matches_bundled_documents": release.version == version
                        and release.processing_text == processing
                        and release.publication_text == publication,
                    }
                )
        root = Path(settings.JUNIOR_PRIVATE_MEDIA_ROOT).resolve()
        public_roots = [
            Path(path).resolve()
            for path in (settings.MEDIA_ROOT, settings.STATIC_ROOT)
            if path
        ]
        jobs = settings.CRONJOBS
        delivery_counts: list[dict[str, Any]] = []
        if "notifications_delivery" in tables:
            from apps.notifications.models import Delivery

            delivery_counts = list(
                Delivery.objects.filter(event_key="junior.updated")
                .values("channel", "status")
                .annotate(count=Count("pk"))
                .order_by("channel", "status")
            )
        report = {
            "read_only_audit": True,
            "league_access": "public",
            "database_vendor": connection.vendor,
            "pending_migrations": pending,
            "flags": {
                "payments_enabled": settings.JUNIOR_PAYMENTS_ENABLED,
                "notification_center_enabled": settings.NOTIFICATION_CENTER_ENABLED,
            },
            "legal": legal,
            "private_photos": {
                "directory_exists": root.is_dir(),
                "process_can_write": root.is_dir() and os.access(root, os.W_OK),
                "outside_public_roots": not any(
                    root == public or root.is_relative_to(public)
                    for public in public_roots
                ),
            },
            "configured_jobs": {
                "process_junior_league": any(
                    "process_junior_league" in str(job) for job in jobs
                ),
                "process_notifications": any(
                    "process_notifications" in str(job) for job in jobs
                ),
            },
            "junior_notification_deliveries": delivery_counts,
            "not_verified_by_this_command": [
                "actual_cron_execution",
                "smtp_and_push_receipt",
                "payment_and_refund",
                "persistent_volume_and_backup_restore",
                "postgres_concurrent_requests",
            ],
        }
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
