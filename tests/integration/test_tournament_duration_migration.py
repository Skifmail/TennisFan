"""Безопасное согласование продолжительности в существующей базе турниров."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date, timedelta
from importlib import import_module
from os import environ
from pathlib import Path
from typing import Any, Final

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from loguru import logger

LEGACY: Final = "0073_alter_match_sparring_response"
SCHEMA: Final = "0074_tournament_duration_labels"
NORMALIZED: Final = "0075_normalize_tournament_duration"

pytestmark = pytest.mark.django_db(transaction=True)


def _snapshot_fingerprints(path: Path) -> dict[str, tuple[int, str]]:
    """Сравнить содержимое всех таблиц копии, не выводя персональные данные."""
    fingerprints: dict[str, tuple[int, str]] = {}
    with closing(sqlite3.connect(path)) as database:
        tables = database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'django_migrations'"
        ).fetchall()
        for (table,) in tables:
            quoted_name = '"' + table.replace('"', '""') + '"'
            rows = database.execute(
                f"SELECT * FROM {quoted_name} ORDER BY rowid"
            ).fetchall()
            fingerprints[table] = (
                len(rows),
                hashlib.sha256(repr(rows).encode()).hexdigest(),
            )
    return fingerprints


def _snapshot_tournaments(path: Path) -> dict[int, dict[str, Any]]:
    """Прочитать полные записи турниров для проверки дат, настроек и timestamps."""
    with closing(sqlite3.connect(path)) as database:
        database.row_factory = sqlite3.Row
        rows = database.execute(
            "SELECT * FROM tournaments_tournament ORDER BY id"
        ).fetchall()
        return {int(row["id"]): dict(row) for row in rows}


def _migrate_snapshot(path: Path, target: str) -> None:
    """Запустить настоящую команду миграции с БД, явно ограниченной копией."""
    script = """
from os import environ

import django
from django.conf import settings
from django.core.management import call_command

from config import settings as project_settings

settings.configure(
    **{
        name: getattr(project_settings, name)
        for name in dir(project_settings)
        if name.isupper() and name != "DATABASES"
    },
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": environ["TOURNAMENT_DURATION_COPY_DB"],
        }
    },
)
django.setup()
call_command(
    "migrate", "tournaments", environ["TOURNAMENT_DURATION_TARGET"],
    interactive=False, verbosity=0,
)
"""
    environment = environ.copy()
    environment["TOURNAMENT_DURATION_COPY_DB"] = str(path)
    environment["TOURNAMENT_DURATION_TARGET"] = target
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_duration_upgrade_preserves_dates_participants_and_payment_history() -> None:
    """Реальная миграция исправляет флаги, но не переписывает календарь и деньги."""
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    other_targets = [target for target in latest_targets if target[0] != "tournaments"]
    legacy_targets = [*other_targets, ("tournaments", LEGACY)]
    schema_targets = [*other_targets, ("tournaments", SCHEMA)]
    new_targets = [*other_targets, ("tournaments", NORMALIZED)]
    start = date(2026, 11, 21)
    now = timezone.now()
    try:
        executor.migrate(legacy_targets)
        old_apps = executor.loader.project_state(legacy_targets).apps
        tournaments = old_apps.get_model("tournaments", "Tournament")
        invoices = old_apps.get_model("tournaments", "TournamentPostpaymentInvoice")
        cases: list[tuple[str, dict[str, Any], str, date | None, bool]] = [
            ("multi", {}, "multi", None, False),
            ("legacy-single", {"is_one_day": True}, "single", start, False),
            ("explicit-single", {"duration": "single"}, "single", start, False),
            (
                "explicit-weekend",
                {"duration": "weekend", "is_one_day": True},
                "weekend",
                start + timedelta(days=1),
                False,
            ),
            (
                "weekend-one-day",
                {"duration": "weekend", "end_date": start},
                "weekend",
                start,
                False,
            ),
            (
                "single-with-two-dates",
                {"duration": "single", "end_date": start + timedelta(days=1)},
                "weekend",
                start + timedelta(days=1),
                False,
            ),
            (
                "single-with-long-calendar",
                {"is_one_day": True, "end_date": start + timedelta(days=7)},
                "multi",
                start + timedelta(days=7),
                False,
            ),
            (
                "weekend-with-long-calendar",
                {"duration": "weekend", "end_date": start + timedelta(days=3)},
                "multi",
                start + timedelta(days=3),
                False,
            ),
            (
                "single-with-postpayment",
                {"duration": "single", "allow_postpayment": True},
                "multi",
                None,
                True,
            ),
            (
                "free-single",
                {"duration": "single", "entry_fee": 0, "allow_postpayment": True},
                "single",
                start,
                False,
            ),
            (
                "single-with-open-window",
                {"duration": "single", "postpayment_window_started_at": now},
                "multi",
                None,
                False,
            ),
            (
                "fill-without-dates",
                {"is_one_day": True, "start_after_fill": True, "start_date": None},
                "single",
                None,
                False,
            ),
            (
                "invalid-legacy-end",
                {"duration": "single", "end_date": start - timedelta(days=1)},
                "single",
                start - timedelta(days=1),
                False,
            ),
        ]
        original_tournaments: dict[str, dict[str, Any]] = {}
        for index, (slug, options, _, _, _) in enumerate(cases):
            defaults: dict[str, Any] = {
                "name": f"Старый турнир {slug}",
                "slug": slug,
                "city": "Москва",
                "format": (
                    "single_elimination",
                    "olympic_consolation",
                    "round_robin",
                    "weekend_day",
                )[index % 4],
                "start_date": start,
                "entry_fee": 1000,
                "registration_deadline": now + timedelta(days=30),
            }
            tournaments.objects.create(**{**defaults, **options})
            original_tournaments[slug] = (
                tournaments.objects.filter(slug=slug).values().get()
            )

        # Отдельный случай с действующим долгом при выключенной старой галочке.
        debt = tournaments.objects.create(
            name="Сохранённый долг",
            slug="single-with-pending-invoice",
            city="Москва",
            duration="single",
            is_one_day=True,
            variant="doubles",
            start_date=start,
            entry_fee=1000,
        )
        users = old_apps.get_model("users", "User")
        players = old_apps.get_model("users", "Player")
        members = [
            players.objects.create(
                user_id=users.objects.create(
                    email=f"duration-history-{i}@test.local"
                ).pk
            )
            for i in range(4)
        ]
        debt.participants.set(members)
        teams = old_apps.get_model("tournaments", "TournamentTeam")
        teams.objects.create(
            tournament_id=debt.pk, player1=members[0], player2=members[1]
        )
        teams.objects.create(
            tournament_id=debt.pk, player1=members[2], player2=members[3]
        )
        for member, status in zip(
            members, ("pending", "paid", "expired", "cancelled"), strict=True
        ):
            invoices.objects.create(
                tournament_id=debt.pk,
                user_id=member.user_id,
                amount=1000,
                due_at=now + timedelta(hours=12),
                status=status,
            )
        coverages = old_apps.get_model("tournaments", "TournamentRegistrationCoverage")
        coverages.objects.create(
            tournament_id=debt.pk,
            user_id=members[1].user_id,
            coverage_type="admin_granted",
        )
        payments = old_apps.get_model("tournaments", "TournamentEntryPayment")
        payments.objects.create(tournament_id=debt.pk, user_id=members[1].user_id)
        matches = old_apps.get_model("tournaments", "Match")
        matches.objects.create(
            tournament_id=debt.pk,
            player1_id=members[0].pk,
            player2_id=members[2].pk,
            round_index=1,
            deadline=now + timedelta(days=7),
        )
        financial_models = (invoices, coverages, payments, teams, matches)
        history = [
            list(model.objects.order_by("pk").values()) for model in financial_models
        ]
        participant_ids = list(
            debt.participants.order_by("pk").values_list("pk", flat=True)
        )

        # Больше размера одной пачки: проверяем, что backfill не теряет хвост.
        tournaments.objects.bulk_create(
            [
                tournaments(
                    name="Однодневный турнир старой версии",
                    slug=f"legacy-batch-{index}",
                    city="Москва",
                    is_one_day=True,
                    start_date=start,
                    entry_fee=1000,
                )
                for index in range(1005)
            ]
        )

        executor = MigrationExecutor(connection)
        executor.migrate(schema_targets)
        for slug, original in original_tournaments.items():
            assert tournaments.objects.filter(slug=slug).values().get() == original
        MigrationExecutor(connection).migrate(new_targets)
        for slug, _, expected_duration, expected_end, expected_postpayment in cases:
            row = tournaments.objects.get(slug=slug)
            assert (
                row.duration,
                row.is_one_day,
                row.end_date,
                row.allow_postpayment,
            ) == (
                expected_duration,
                expected_duration == "single",
                expected_end,
                expected_postpayment,
            )
            for field in (
                "start_date",
                "registration_deadline",
                "entry_fee",
                "format",
                "created_at",
                "updated_at",
            ):
                assert getattr(row, field) == original_tournaments[slug][field]
        debt.refresh_from_db()
        assert (debt.duration, debt.is_one_day, debt.allow_postpayment) == (
            "multi",
            False,
            False,
        )
        assert debt.end_date is None
        assert [
            list(model.objects.order_by("pk").values()) for model in financial_models
        ] == history
        assert (
            list(debt.participants.order_by("pk").values_list("pk", flat=True))
            == participant_ids
        )
        assert (
            tournaments.objects.filter(
                slug__startswith="legacy-batch-",
                duration="single",
                is_one_day=True,
                end_date=start,
            ).count()
            == 1005
        )

        # Повторный backfill и откат кода не меняют уже согласованные данные.
        normalized_rows = list(tournaments.objects.order_by("pk").values())
        migration = import_module(
            "apps.tournaments.migrations.0075_normalize_tournament_duration"
        )
        new_apps = MigrationExecutor(connection).loader.project_state(new_targets).apps
        with connection.schema_editor(atomic=False) as editor:
            migration.normalize_tournament_duration(new_apps, editor)
        assert list(tournaments.objects.order_by("pk").values()) == normalized_rows
        MigrationExecutor(connection).migrate(legacy_targets)
        assert list(tournaments.objects.order_by("pk").values()) == normalized_rows
        assert [
            list(model.objects.order_by("pk").values()) for model in financial_models
        ] == history
    finally:
        MigrationExecutor(connection).migrate(latest_targets)


@pytest.mark.skipif(
    not environ.get("TOURNAMENT_DURATION_DB_SNAPSHOT"),
    reason="Копия существующей SQLite БД проверяется отдельным запуском",
)
def test_duration_upgrade_on_database_snapshot(tmp_path: Path) -> None:
    """Обновление реальной копии сохраняет явный календарь и всю связанную историю."""
    snapshot = Path(environ["TOURNAMENT_DURATION_DB_SNAPSHOT"]).resolve()
    assert snapshot.is_file()
    original_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    database_path = tmp_path / "duration-copy.sqlite3"
    shutil.copy2(snapshot, database_path)

    # Необходимые предшествующие миграции тоже применяются только к копии.
    _migrate_snapshot(database_path, LEGACY)
    before = _snapshot_tournaments(database_path)
    fingerprints = _snapshot_fingerprints(database_path)
    with closing(sqlite3.connect(database_path)) as database:
        pending_ids = {
            int(row[0])
            for row in database.execute(
                "SELECT DISTINCT tournament_id FROM tournaments_tournamentpostpaymentinvoice "
                "WHERE status = 'pending'"
            )
        }

    _migrate_snapshot(database_path, SCHEMA)
    assert _snapshot_fingerprints(database_path) == fingerprints
    _migrate_snapshot(database_path, NORMALIZED)
    normalized = _snapshot_tournaments(database_path)
    assert normalized.keys() == before.keys()
    adjustable_fields = {"duration", "is_one_day", "end_date", "allow_postpayment"}
    for pk, old in before.items():
        row = normalized[pk]
        assert {
            field: value
            for field, value in row.items()
            if field not in adjustable_fields
        } == {
            field: value
            for field, value in old.items()
            if field not in adjustable_fields
        }
        assert bool(row["is_one_day"]) is (row["duration"] == "single")
        if old["end_date"] is not None:
            assert row["end_date"] == old["end_date"]
        if old["allow_postpayment"] and (
            old["entry_fee"] > 0
            or old["postpayment_window_started_at"] is not None
            or pk in pending_ids
        ):
            assert row["allow_postpayment"]
        if row["duration"] == "single":
            assert not row["allow_postpayment"]
            assert row["postpayment_window_started_at"] is None
            assert pk not in pending_ids

    after = _snapshot_fingerprints(database_path)
    assert {
        table: value
        for table, value in after.items()
        if table != "tournaments_tournament"
    } == {
        table: value
        for table, value in fingerprints.items()
        if table != "tournaments_tournament"
    }
    _migrate_snapshot(database_path, NORMALIZED)
    assert _snapshot_fingerprints(database_path) == after
    _migrate_snapshot(database_path, LEGACY)
    assert _snapshot_fingerprints(database_path) == after
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == original_hash
    logger.info(
        "Проверена копия: {} турниров, {} таблиц, {} изменённых записей; даты и история сохранены",
        len(before),
        len(after),
        sum(before[pk] != row for pk, row in normalized.items()),
    )
