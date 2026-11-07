"""Настоящий переход старой схемы без повторных результатов и Telegram-отправок."""

from __future__ import annotations

from datetime import timedelta
from typing import Final
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.notifications.models import Delivery
from apps.telegram_bot.delivery import process_telegram_deliveries, request_broadcast
from apps.telegram_bot.models import (
    TelegramBroadcast,
    TelegramDeliveryState,
    TelegramUpdateReceipt,
    TournamentAnnouncement,
)
from apps.tournaments.models import Match, MatchResultProposal
from apps.tournaments.proposal_service import apply_proposal
from apps.users.models import Player

MigrationTarget = tuple[str, str]
LEGACY: Final = {
    "notifications": "0015_camp_attendance_event",
    "telegram_bot": "0002_alter_telegrambroadcast_image",
    "tournaments": "0069_groups_playoff_labels",
}
SCHEMA_ONLY: Final = {
    "notifications": "0017_telegram_delivery_queue",
    "telegram_bot": "0003_telegram_delivery_queue",
    "tournaments": "0070_matchresultproposal_applied_at",
}


def _replace_targets(
    targets: list[MigrationTarget], replacements: dict[str, str]
) -> list[MigrationTarget]:
    """Сохранить актуальную схему остальных приложений при проверке старого Telegram."""
    return [(app, replacements.get(app, name)) for app, name in targets]


@pytest.mark.django_db(transaction=True)
def test_legacy_schema_upgrade_preserves_history_and_prevents_replays() -> None:
    """Проверить DDL и backfill на 1005 принятых заявках, рассылках и старой очереди."""
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    legacy_targets = _replace_targets(latest_targets, LEGACY)
    now = timezone.now()
    sent_at = now - timedelta(hours=2)
    already_applied_at = now - timedelta(days=3)
    try:
        executor.migrate(legacy_targets)
        old_apps = executor.loader.project_state(legacy_targets).apps
        users = old_apps.get_model("users", "User")
        players = old_apps.get_model("users", "Player")
        matches = old_apps.get_model("tournaments", "Match")
        proposals = old_apps.get_model("tournaments", "MatchResultProposal")
        broadcasts = old_apps.get_model("telegram_bot", "TelegramBroadcast")
        deliveries = old_apps.get_model("notifications", "Delivery")
        first_user = users.objects.create(email="legacy-first@test.local")
        second_user = users.objects.create(email="legacy-second@test.local")
        first = players.objects.create(
            user_id=first_user.pk, total_points=3000, matches_played=4, matches_won=2
        )
        second = players.objects.create(user_id=second_user.pk, total_points=2500)
        match = matches.objects.create(
            match_type="sparring",
            player1_id=first.pk,
            player2_id=second.pk,
            status="scheduled",
            deadline=now + timedelta(days=1),
        )
        accepted = proposals.objects.bulk_create(
            [
                proposals(
                    match_id=match.pk,
                    proposer_id=first.pk,
                    status="accepted",
                    player1_set1=6,
                    player2_set1=4,
                )
                for _ in range(1005)
            ]
        )
        for status in ("pending", "rejected"):
            proposals.objects.create(
                match_id=match.pk, proposer_id=first.pk, status=status
            )
        legacy_broadcast = broadcasts.objects.create(
            text="Старая рассылка",
            image="telegram_broadcasts/legacy.jpg",
            sent_at=sent_at,
            created_by_id=first_user.pk,
        )
        draft = broadcasts.objects.create(
            text="Сохранённый черновик", created_by_id=first_user.pk
        )
        for index, status in enumerate(
            ("queued", "processing", "accepted", "failed", "skipped")
        ):
            deliveries.objects.create(
                user_id=first_user.pk,
                event_key="platform.user_message",
                channel="email" if index % 2 else "push",
                status=status,
                title="Прежнее задание",
                body="Не изменять",
                url="/users/profile/",
                attempts=index,
                provider_id="legacy-provider" if status == "accepted" else "",
                available_at=now,
                expires_at=now + timedelta(days=1),
            )
        preserved_fields = (
            "pk",
            "key",
            "channel",
            "status",
            "attempts",
            "provider_id",
            "title",
            "body",
            "url",
            "available_at",
            "expires_at",
        )
        old_deliveries = list(
            deliveries.objects.order_by("pk").values(*preserved_fields)
        )

        # Частично выполненный выпуск не должен затирать уже проставленный applied_at.
        schema_targets = _replace_targets(latest_targets, SCHEMA_ONLY)
        executor = MigrationExecutor(connection)
        executor.migrate(schema_targets)
        schema_apps = executor.loader.project_state(schema_targets).apps
        schema_apps.get_model("tournaments", "MatchResultProposal").objects.filter(
            pk=accepted[0].pk
        ).update(applied_at=already_applied_at)
        before_backfill = timezone.now()
        MigrationExecutor(connection).migrate(latest_targets)
        after_backfill = timezone.now()

        accepted_rows = MatchResultProposal.objects.filter(status="accepted")
        assert accepted_rows.count() == 1005
        assert not accepted_rows.filter(applied_at__isnull=True).exists()
        assert accepted_rows.get(pk=accepted[0].pk).applied_at == already_applied_at
        backfilled = accepted_rows.exclude(pk=accepted[0].pk)
        applied_times = set(backfilled.values_list("applied_at", flat=True))
        assert len(applied_times) == 1
        applied_at = next(iter(applied_times))
        assert applied_at is not None
        assert before_backfill <= applied_at <= after_backfill
        assert (
            MatchResultProposal.objects.filter(
                status__in=("pending", "rejected"), applied_at__isnull=True
            ).count()
            == 2
        )

        legacy = TelegramBroadcast.objects.get(pk=legacy_broadcast.pk)
        assert legacy.status == TelegramBroadcast.Status.LEGACY
        assert legacy.sent_at == legacy.prepared_at == legacy.finished_at == sent_at
        assert (legacy.text, legacy.image.name, legacy.created_by_id) == (
            "Старая рассылка",
            "telegram_broadcasts/legacy.jpg",
            first_user.pk,
        )
        assert "Старая отправка без журнала" in str(legacy)
        preserved_draft = TelegramBroadcast.objects.get(pk=draft.pk)
        assert preserved_draft.status == TelegramBroadcast.Status.DRAFT
        assert preserved_draft.text == "Сохранённый черновик"
        assert preserved_draft.prepared_at is None
        assert preserved_draft.sent_at is None
        assert (
            list(Delivery.objects.order_by("pk").values(*preserved_fields))
            == old_deliveries
        )
        assert not Delivery.objects.exclude(telegram_payload=None).exists()
        assert not TelegramDeliveryState.objects.exists()
        assert not TelegramUpdateReceipt.objects.exists()
        assert not TournamentAnnouncement.objects.exists()

        with (
            patch("apps.telegram_bot.services.is_configured", return_value=True),
            patch("apps.telegram_bot.services.send_message_or_raise") as send,
        ):
            assert not request_broadcast(legacy.pk)
            assert process_telegram_deliveries() == 0
        send.assert_not_called()
        assert not Delivery.objects.filter(channel="telegram").exists()
        assert not apply_proposal(accepted_rows.get(pk=accepted[-1].pk))
        assert Match.objects.get(pk=match.pk).status == Match.MatchStatus.SCHEDULED
        current_player = Player.objects.get(pk=first.pk)
        assert (
            current_player.total_points,
            current_player.matches_played,
            current_player.matches_won,
        ) == (3000, 4, 2)
    finally:
        # Очистка pytest всегда должна видеть полную актуальную схему, даже при отказе.
        MigrationExecutor(connection).migrate(latest_targets)
