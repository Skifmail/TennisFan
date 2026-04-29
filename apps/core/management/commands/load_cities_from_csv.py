"""Команда для загрузки городов и координат в справочник City из CSV-файла.

Ожидается CSV с колонками:
    - ``city`` (название города)
    - ``geo_lat`` (широта, опционально)
    - ``geo_lon`` (долгота, опционально)

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
    """Загружает города и координаты в модель City из CSV-файла.

    Args:
        path: Путь к CSV-файлу (через опцию ``--path``).

    Raises:
        CommandError: Если файл не найден или не содержит колонки ``city``.
    """

    help = (
        "Загрузка городов и координат в City из CSV "
        "с колонками 'city', 'geo_lat', 'geo_lon'."
    )

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
            help=(
                "Путь к CSV-файлу со списком городов " "(должна быть колонка 'city')."
            ),
        )
        parser.add_argument(
            "--force-update",
            dest="force_update",
            action="store_true",
            help="Обновлять координаты даже если в базе они уже заполнены.",
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
        updated = 0
        skipped = 0
        without_coords = 0
        seen: set[str] = set()
        force_update = bool(options.get("force_update"))

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

                geo_lat_raw = (row.get("geo_lat") or "").strip()
                geo_lon_raw = (row.get("geo_lon") or "").strip()
                lat: float | None = None
                lng: float | None = None
                if geo_lat_raw and geo_lon_raw:
                    try:
                        lat = float(geo_lat_raw)
                        lng = float(geo_lon_raw)
                    except ValueError:
                        lat = None
                        lng = None

                if lat is None or lng is None:
                    without_coords += 1

                defaults: dict[str, Any] = {}
                if lat is not None and lng is not None:
                    defaults = {"lat": lat, "lng": lng}

                obj, created_flag = City.objects.get_or_create(
                    name=name, defaults=defaults
                )
                if created_flag:
                    created += 1
                else:
                    # Обновляем координаты только если они отсутствуют
                    # или если явно задан флаг принудительного обновления.
                    if (
                        lat is not None
                        and lng is not None
                        and (force_update or obj.lat is None or obj.lng is None)
                    ):
                        obj.lat = lat
                        obj.lng = lng
                        obj.save(update_fields=["lat", "lng"])
                        updated += 1
                    else:
                        skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Города загружены из {path}. "
                f"Создано: {created}, обновлено координат: {updated}, "
                f"без координат в CSV: {without_coords}, пропущено: {skipped}."
            )
        )
