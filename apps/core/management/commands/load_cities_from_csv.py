"""Команда для загрузки населённых пунктов и координат в справочник City из CSV.

Ожидается CSV в формате КЛАДР/ФИАС с колонками:
    - ``city`` / ``city_type`` (город)
    - ``settlement`` / ``settlement_type`` (село, деревня, пгт и т.д.)
    - ``region`` / ``region_type`` (субъект РФ)
    - ``geo_lat`` / ``geo_lon`` (координаты, опционально)

Если заполнен ``settlement``, в справочник попадает он, а не родительский город.

Пример запуска:
    python manage.py load_cities_from_csv --path static/documents/city.csv
    python manage.py load_cities_from_csv --path static/documents/settlements.csv
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.localities import parse_kladr_row, upsert_locality


class Command(BaseCommand):
    """Загружает города и прочие населённые пункты в модель City из CSV."""

    help = (
        "Загрузка населённых пунктов и координат в City из KLADR-CSV "
        "(колонки city/settlement, типы и geo_lat/geo_lon)."
    )

    def add_arguments(self, parser: Any) -> None:
        """Добавляет аргументы командной строки."""

        parser.add_argument(
            "--path",
            dest="path",
            type=str,
            required=True,
            help="Путь к CSV-файлу (KLADR: city и/или settlement).",
        )
        parser.add_argument(
            "--force-update",
            dest="force_update",
            action="store_true",
            help="Обновлять координаты даже если в базе они уже заполнены.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Основная логика загрузки населённых пунктов из CSV."""

        raw_path = options.get("path") or ""
        path = Path(raw_path).expanduser().resolve()

        if not path.exists() or not path.is_file():
            raise CommandError(f"CSV-файл не найден: {path}")

        created = 0
        updated = 0
        skipped = 0
        without_coords = 0
        seen: set[tuple[str, str, str]] = set()
        force_update = bool(options.get("force_update"))

        with path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            if "city" not in fieldnames and "settlement" not in fieldnames:
                raise CommandError("В CSV-файле нет колонок 'city' или 'settlement'.")

            for row in reader:
                parsed = parse_kladr_row(row)
                if parsed is None:
                    skipped += 1
                    continue
                key = (
                    parsed["name"].casefold(),
                    parsed["region"].casefold(),
                    parsed["settlement_type"],
                )
                if key in seen:
                    skipped += 1
                    continue
                seen.add(key)

                lat = parsed.get("lat")
                lng = parsed.get("lng")
                if lat is None or lng is None:
                    without_coords += 1

                _obj, created_flag = upsert_locality(
                    name=parsed["name"],
                    settlement_type=parsed["settlement_type"],
                    region=parsed["region"],
                    lat=lat,
                    lng=lng,
                    force_update=force_update,
                )
                if created_flag:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Населённые пункты загружены из {path}. "
                f"Создано: {created}, обновлено: {updated}, "
                f"без координат в CSV: {without_coords}, пропущено: {skipped}."
            )
        )
