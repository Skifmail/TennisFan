"""Команда для загрузки городов в справочник City из CSV-файла.

Ожидается CSV с колонкой ``city`` (заголовок в первой строке).

Пример запуска:
    python manage.py load_cities_from_csv --path /path/to/city.csv
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import City


class Command(BaseCommand):
    """Загружает города в модель City из CSV-файла.

    Args:
        path: Путь к CSV-файлу (через опцию ``--path``).

    Raises:
        CommandError: Если файл не найден или не содержит колонки ``city``.
    """

    help = "Загрузка городов в City из CSV с колонкой 'city'."

    def add_arguments(self, parser: Any) -> None:
        """Добавляет аргументы командной строки.

        Args:
            parser: Парсер аргументов Django.
        """

        parser.add_argument(
            "--path",
            dest="path",
            type=str,
            required=True,
            help="Путь к CSV-файлу со списком городов (должна быть колонка 'city').",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Основная логика загрузки городов из CSV.

        Args:
            *args: Позиционные аргументы (не используются).
            **options: Опции командной строки, включая ``path``.
        """

        raw_path = options.get("path") or ""
        path = Path(raw_path).expanduser().resolve()

        if not path.exists() or not path.is_file():
            raise CommandError(f"CSV-файл не найден: {path}")

        created = 0
        skipped = 0
        seen: set[str] = set()

        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            if not fieldnames or "city" not in fieldnames:
                raise CommandError("В CSV-файле нет колонки 'city'.")

            for row in reader:
                name = (row.get("city") or "").strip()
                if not name:
                    skipped += 1
                    continue
                if name in seen:
                    skipped += 1
                    continue
                seen.add(name)

                obj, created_flag = City.objects.get_or_create(name=name)
                if created_flag:
                    created += 1
                else:
                    skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Города загружены из {path}. Создано: {created}, пропущено: {skipped}."
            )
        )
