"""Создаёт уменьшенные копии уже загруженных изображений и ставит им кэш."""

from typing import Any

from django.apps import apps
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandParser
from django.db import models

from apps.core.image_variants import ensure_display_variants


class Command(BaseCommand):
    """Обходит ImageField в базе и записывает копии для карточек и аватаров."""

    help = "Записать уменьшенные копии существующих изображений и Cache-Control исходников."

    def add_arguments(self, parser: CommandParser) -> None:
        """Добавляет ограничение числа файлов.

        Args:
            parser: Парсер аргументов команды.
        """
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Сколько файлов обработать. 0 — без ограничения.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Запускает обход изображений.

        Args:
            *args: Позиционные аргументы Django.
            **options: Разобранные аргументы, включая ``limit``.
        """
        limit = int(options.get("limit") or 0)
        processed = 0
        failed = 0
        for model in apps.get_models():
            image_fields = [
                field
                for field in model._meta.get_fields()
                if isinstance(field, models.ImageField)
            ]
            if not image_fields:
                continue
            queryset = model.objects.all()
            for instance in queryset.iterator():
                for field in image_fields:
                    if limit and processed >= limit:
                        self.stdout.write(f"Готово: {processed}, ошибок: {failed}")
                        return
                    file_obj = getattr(instance, field.name, None)
                    name = getattr(file_obj, "name", None)
                    if not name:
                        continue
                    try:
                        ensure_display_variants(
                            name,
                            storage=getattr(file_obj, "storage", default_storage),
                        )
                        processed += 1
                    except Exception as exc:
                        failed += 1
                        self.stderr.write(
                            f"{model.__name__}.{field.name} {name}: {exc}"
                        )
        self.stdout.write(f"Готово: {processed}, ошибок: {failed}")
