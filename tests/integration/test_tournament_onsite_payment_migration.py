"""Миграция оплаты на корте сохраняет старый календарь, регистрации и деньги."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import closing
from datetime import date, time, timedelta
from decimal import Decimal
from os import environ
from pathlib import Path
from typing import Any, Final, Literal

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from loguru import logger

from tests.integration.test_tournament_duration_migration import (
    _migrate_snapshot,
    _snapshot_fingerprints,
    _snapshot_tournaments,
)

PREVIOUS: Final = "0076_tournament_start_time"
ONSITE: Final = "0077_tournament_onsite_payment"

pytestmark = pytest.mark.django_db(transaction=True)


def _snapshot_rows(
    path: Path,
    table: Literal[
        "auth_permission",
        "django_content_type",
        "tournaments_tournamententryrefundrequest",
    ],
) -> dict[int, dict[str, Any]]:
    """Прочитать разрешённую таблицу копии для точной сверки прежних строк."""
    with closing(sqlite3.connect(path)) as database:
        database.row_factory = sqlite3.Row
        rows = database.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall()
        return {int(row["id"]): dict(row) for row in rows}


def test_onsite_upgrade_constraints_and_rollback_keep_existing_history() -> None:
    """Реальная схема защищает получение и уникальность, не изменяя прежние записи."""
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    other_targets = [target for target in latest_targets if target[0] != "tournaments"]
    previous_targets = [*other_targets, ("tournaments", PREVIOUS)]
    onsite_targets = [*other_targets, ("tournaments", ONSITE)]
    now = timezone.now()
    try:
        executor.migrate(previous_targets)
        old_apps = (
            MigrationExecutor(connection).loader.project_state(previous_targets).apps
        )
        users = old_apps.get_model("users", "User")
        players = old_apps.get_model("users", "Player")
        tournaments = old_apps.get_model("tournaments", "Tournament")
        user = users.objects.create(email="onsite-migration-player@test.local")
        partner_user = users.objects.create(email="onsite-migration-partner@test.local")
        confirmer = users.objects.create(email="onsite-migration-manager@test.local")
        player = players.objects.create(user_id=user.pk)
        partner = players.objects.create(user_id=partner_user.pk)
        tournament = tournaments.objects.create(
            name="Существующий турнир",
            slug="onsite-migration-history",
            city="Москва",
            variant="doubles",
            duration="weekend",
            start_date=date(2026, 11, 21),
            start_time=time(11, 30),
            end_date=date(2026, 11, 22),
            entry_fee=1500,
            allow_postpayment=True,
            postpayment_window_started_at=now,
            registration_deadline=now - timedelta(days=1),
            status="cancelled",
        )
        tournament.participants.set([player, partner])
        tournament_before = tournaments.objects.filter(pk=tournament.pk).values().get()
        history_models = {
            name: old_apps.get_model("tournaments", name)
            for name in (
                "TournamentPostpaymentInvoice",
                "TournamentEntryPayment",
                "TournamentRegistrationCoverage",
                "TournamentTeam",
                "Match",
                "TournamentEntryRefundRequest",
            )
        }
        history_models["TournamentTeam"].objects.create(
            tournament_id=tournament.pk, player1_id=player.pk, player2_id=partner.pk
        )
        history_models["Match"].objects.create(
            tournament_id=tournament.pk,
            player1_id=player.pk,
            player2_id=partner.pk,
            deadline=now + timedelta(days=1),
        )
        for participant, status in ((user, "paid"), (partner_user, "pending")):
            history_models["TournamentPostpaymentInvoice"].objects.create(
                tournament_id=tournament.pk,
                user_id=participant.pk,
                amount=Decimal("700.25"),
                due_at=now + timedelta(hours=9),
                status=status,
                paid_at=now if status == "paid" else None,
                reminder_1h_sent_at=now,
            )
        history_models["TournamentEntryPayment"].objects.create(
            tournament_id=tournament.pk, user_id=user.pk
        )
        history_models["TournamentRegistrationCoverage"].objects.create(
            tournament_id=tournament.pk,
            user_id=partner_user.pk,
            coverage_type="club_plan_slot",
        )
        history_models["TournamentEntryRefundRequest"].objects.create(
            tournament_id=tournament.pk,
            user_id=user.pk,
            amount=Decimal("700.25"),
            refund_ref="REF-ONSITE-LEGACY",
        )
        history_models["PaymentRecord"] = old_apps.get_model(
            "payments", "PaymentRecord"
        )
        history_models["PaymentRecord"].objects.create(
            user_id=user.pk,
            payment_type="tournament",
            item_id=str(tournament.pk),
            amount=Decimal("700.25"),
            metadata={"history": "unchanged"},
        )
        history: dict[str, list[dict[str, Any]]] = {
            name: list(model.objects.order_by("pk").values())
            for name, model in history_models.items()
        }
        participant_ids = list(tournament.participants.values_list("pk", flat=True))

        MigrationExecutor(connection).migrate(onsite_targets)
        new_apps = (
            MigrationExecutor(connection).loader.project_state(onsite_targets).apps
        )
        onsite = new_apps.get_model("tournaments", "TournamentOnsitePayment")
        new_tournaments = new_apps.get_model("tournaments", "Tournament")
        row = new_tournaments.objects.filter(pk=tournament.pk).values().get()
        assert row.pop("allow_onsite_payment") is False
        assert row == tournament_before
        assert not onsite.objects.exists()
        refunds = new_apps.get_model("tournaments", "TournamentEntryRefundRequest")
        assert (
            refunds.objects.get(refund_ref="REF-ONSITE-LEGACY").onsite_payment_id
            is None
        )
        assert {
            name: list(model.objects.order_by("pk").values())
            for name, model in history_models.items()
        } == history

        payment = onsite.objects.create(
            tournament_id=tournament.pk, user_id=user.pk, amount=Decimal("700.25")
        )
        assert payment.status == "expected"
        assert payment.received_at is None
        assert payment.received_by_id is None
        with pytest.raises(IntegrityError), transaction.atomic():
            onsite.objects.create(
                tournament_id=tournament.pk, user_id=user.pk, amount=1
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            onsite.objects.create(
                tournament_id=tournament.pk, user_id=partner_user.pk, amount=-1
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            onsite.objects.filter(pk=payment.pk).update(status="received")
        with pytest.raises(IntegrityError), transaction.atomic():
            onsite.objects.filter(pk=payment.pk).update(received_at=now)
        for status in ("cancelled", "replaced"):
            onsite.objects.create(
                tournament_id=tournament.pk,
                user_id=user.pk,
                amount=700,
                status=status,
                closed_at=now,
            )
        onsite.objects.filter(pk=payment.pk).update(
            status="received", received_at=now, received_by_id=confirmer.pk
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            onsite.objects.create(
                tournament_id=tournament.pk,
                user_id=user.pk,
                amount=700,
                status="received",
                received_at=now,
            )
        refund = refunds.objects.create(
            tournament_id=tournament.pk,
            user_id=user.pk,
            amount=Decimal("700.25"),
            refund_ref="REF-ONSITE-RECEIVED",
            onsite_payment_id=payment.pk,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            refunds.objects.create(
                tournament_id=tournament.pk,
                user_id=user.pk,
                amount=700,
                refund_ref="REF-ONSITE-DUPLICATE",
                onsite_payment_id=payment.pk,
            )
        with pytest.raises(ProtectedError):
            onsite.objects.get(pk=payment.pk).delete()
        with pytest.raises(ProtectedError):
            new_tournaments.objects.get(pk=tournament.pk).delete()
        new_users = new_apps.get_model("users", "User")
        with pytest.raises(ProtectedError):
            new_users.objects.get(pk=user.pk).delete()
        new_users.objects.get(pk=confirmer.pk).delete()
        payment.refresh_from_db()
        assert payment.received_by_id is None
        assert payment.received_at == now
        assert payment.amount == Decimal("700.25")
        assert payment.status == "received"

        # Откат схемы применяется только к тестовой базе: старая история и сама
        # заявка сохраняются, а новая таблица получения удаляется штатным DOWN.
        MigrationExecutor(connection).migrate(previous_targets)
        assert (
            tournaments.objects.filter(pk=tournament.pk).values().get()
            == tournament_before
        )
        assert (
            list(tournament.participants.values_list("pk", flat=True))
            == participant_ids
        )
        for name, model in history_models.items():
            ids = [original["id"] for original in history[name]]
            assert (
                list(model.objects.filter(pk__in=ids).order_by("pk").values())
                == history[name]
            )
        old_refund = history_models["TournamentEntryRefundRequest"].objects.get(
            pk=refund.pk
        )
        assert old_refund.amount == Decimal("700.25")
        assert old_refund.refund_ref == "REF-ONSITE-RECEIVED"
        assert (
            "tournaments_tournamentonsitepayment"
            not in connection.introspection.table_names()
        )
    finally:
        MigrationExecutor(connection).migrate(latest_targets)


@pytest.mark.skipif(
    not environ.get("TOURNAMENT_DURATION_DB_SNAPSHOT"),
    reason="Копия существующей SQLite БД проверяется отдельным запуском",
)
def test_onsite_upgrade_and_rollback_on_database_snapshot(tmp_path: Path) -> None:
    """Повторное применение и откат новой схемы не меняют ни одной старой записи."""
    snapshot = Path(environ["TOURNAMENT_DURATION_DB_SNAPSHOT"]).resolve()
    assert snapshot.is_file()
    original_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    database_path = tmp_path / "onsite-copy.sqlite3"
    shutil.copy2(snapshot, database_path)
    _migrate_snapshot(database_path, PREVIOUS)
    before = _snapshot_tournaments(database_path)
    fingerprints = _snapshot_fingerprints(database_path)
    permissions_before = _snapshot_rows(database_path, "auth_permission")
    content_types_before = _snapshot_rows(database_path, "django_content_type")
    refunds_before = _snapshot_rows(
        database_path, "tournaments_tournamententryrefundrequest"
    )
    _migrate_snapshot(database_path, ONSITE)
    after = _snapshot_tournaments(database_path)
    assert after.keys() == before.keys()
    for pk, row in after.items():
        assert not row["allow_onsite_payment"]
        assert {
            key: value for key, value in row.items() if key != "allow_onsite_payment"
        } == before[pk]
    # post_migrate добавляет только ContentType новой модели и четыре её права.
    # Все старые права проверяем построчно, а не исключаем из проверки сохранности.
    permissions_after = _snapshot_rows(database_path, "auth_permission")
    content_types_after = _snapshot_rows(database_path, "django_content_type")
    assert {
        pk: permissions_after[pk] for pk in permissions_before
    } == permissions_before
    assert {
        pk: content_types_after[pk] for pk in content_types_before
    } == content_types_before
    new_content_types = [
        row for pk, row in content_types_after.items() if pk not in content_types_before
    ]
    assert len(new_content_types) == 1
    new_content_type = new_content_types[0]
    assert (new_content_type["app_label"], new_content_type["model"]) == (
        "tournaments",
        "tournamentonsitepayment",
    )
    new_permissions = [
        row for pk, row in permissions_after.items() if pk not in permissions_before
    ]
    assert len(new_permissions) == 4
    assert {row["content_type_id"] for row in new_permissions} == {
        new_content_type["id"]
    }
    assert {row["codename"] for row in new_permissions} == {
        f"{action}_tournamentonsitepayment"
        for action in ("add", "change", "delete", "view")
    }
    refunds_after = _snapshot_rows(
        database_path, "tournaments_tournamententryrefundrequest"
    )
    assert {
        pk: {key: value for key, value in row.items() if key != "onsite_payment_id"}
        for pk, row in refunds_after.items()
    } == refunds_before
    service_tables = {"auth_permission", "django_content_type"}
    changed_tables = {
        "tournaments_tournament",
        "tournaments_tournamententryrefundrequest",
        *service_tables,
    }
    upgraded = _snapshot_fingerprints(database_path)
    assert upgraded.keys() - fingerprints.keys() == {
        "tournaments_tournamentonsitepayment"
    }
    assert {
        table: fingerprint
        for table, fingerprint in fingerprints.items()
        if table not in changed_tables
    } == {
        table: fingerprint
        for table, fingerprint in upgraded.items()
        if table in fingerprints and table not in changed_tables
    }
    with closing(sqlite3.connect(database_path)) as database:
        assert database.execute(
            "SELECT COUNT(*) FROM tournaments_tournamentonsitepayment"
        ).fetchone() == (0,)
        assert database.execute(
            "SELECT COUNT(*) FROM tournaments_tournamententryrefundrequest WHERE onsite_payment_id IS NOT NULL"
        ).fetchone() == (0,)
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    _migrate_snapshot(database_path, ONSITE)
    assert _snapshot_fingerprints(database_path) == upgraded
    _migrate_snapshot(database_path, PREVIOUS)
    # Django намеренно не удаляет ContentType и права при откате миграции.
    assert _snapshot_fingerprints(database_path) == {
        table: upgraded[table] if table in service_tables else fingerprint
        for table, fingerprint in fingerprints.items()
    }
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == original_hash
    logger.info(
        "Схема оплаты на корте проверена на копии: {} турниров; старая история сохранена",
        len(before),
    )
