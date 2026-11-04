"""Подготовка неактивной версии согласий без имитации юридической проверки."""

from django.core.management.base import BaseCommand

from apps.juniors.legal import document_text, release_version
from apps.juniors.models import LegalRelease


class Command(BaseCommand):
    help = "Создать неактивный проект согласий в разделе «Детская лига»"

    def handle(self, *args, **options):
        release, created = LegalRelease.objects.get_or_create(
            version=release_version(),
            defaults={
                "processing_text": document_text("processing"),
                "publication_text": document_text("publication"),
                "reviewed": False,
                "is_current": False,
            },
        )
        self.stdout.write(
            f"Версия {release.version}: {'создана' if created else 'уже существует'}. "
            "Текущие согласия не изменены. Проверка и активация выполняются отдельно."
        )
