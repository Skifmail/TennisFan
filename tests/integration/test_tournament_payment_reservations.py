"""Подтверждение взноса не присваивает чужой резерв и не переписывает финансовый снимок."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.clubs.finance_services import (
    cancel_reserved_balance,
    confirm_reserved_balance,
    reserve_member_balance,
)
from apps.clubs.models import ClubMember, ClubMemberBalanceTransaction
from apps.payments.models import PaymentRecord
from apps.payments.views import finalize_successful_payment
from apps.tournaments.cancel import cancel_tournament
from apps.tournaments.entry_payment import get_online_entry_payment_amount
from apps.tournaments.models import Tournament, TournamentEntryRefundRequest
from tests.support.factories import (
    make_club,
    make_payment_record,
    make_player,
    make_tournament,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("deny_external_network")]

BalancePayment = tuple[
    ClubMember, Tournament, ClubMemberBalanceTransaction, PaymentRecord
]


@pytest.fixture
def balance_payment() -> BalancePayment:
    """Сохранить внешний взнос и настоящий частичный резерв клубного баланса."""
    player = make_player(email_suffix="reservation-entry")
    club = make_club()
    member = ClubMember.objects.create(
        club=club, user=player.user, status="active", balance=600
    )
    tournament = make_tournament(
        club=club,
        entry_fee=1000,
        start_date=timezone.localdate() + timedelta(days=7),
    )
    reserved = reserve_member_balance(
        member,
        400,
        source=ClubMemberBalanceTransaction.Source.TOURNAMENT_PAYMENT,
        description="Резерв турнирного взноса",
        reference=f"tournament:{tournament.pk}",
        metadata={"tournament_id": tournament.pk},
    )
    assert reserved is not None
    record = make_payment_record(
        player.user,
        payment_type="tournament",
        item_id=str(tournament.pk),
        amount=600,
        metadata={
            "total_amount": "1000.00",
            "balance_amount": "400.00",
            "balance_transaction_id": str(reserved.pk),
        },
    )
    return member, tournament, reserved, record


@pytest.mark.parametrize(
    "invalid",
    [
        "cancelled",
        "credit",
        "source",
        "other_tournament",
        "other_club",
        "wrong_amount",
        "missing_id",
        "nan_amount",
        "negative_amount",
    ],
)
def test_tournament_payment_reservations_reject_invalid_links_atomically(
    balance_payment: BalancePayment, invalid: str
) -> None:
    """Недействительная ссылка не меняет резерв, регистрацию и сохранённый успех шлюза."""
    member, tournament, reserved, record = balance_payment
    if invalid == "cancelled":
        cancel_reserved_balance(reserved)
    elif invalid == "credit":
        reserved.direction = "credit"
        reserved.save(update_fields=["direction"])
    elif invalid == "source":
        reserved.source = "manual"
        reserved.save(update_fields=["source"])
    elif invalid == "other_tournament":
        reserved.reference = f"tournament:{tournament.pk + 1}"
        reserved.save(update_fields=["reference"])
    elif invalid == "other_club":
        reserved.club = make_club(slug="foreign-reservation-club")
        reserved.save(update_fields=["club"])
    else:
        metadata = dict(record.metadata)
        if invalid == "missing_id":
            metadata["balance_transaction_id"] = ""
        else:
            metadata["balance_amount"] = {
                "wrong_amount": "399.99",
                "nan_amount": "NaN",
                "negative_amount": "-400.00",
            }[invalid]
        record.metadata = metadata
        record.save(update_fields=["metadata"])
    reserve_before = (
        ClubMemberBalanceTransaction.objects.filter(pk=reserved.pk).values().get()
    )
    member_before = ClubMember.objects.filter(pk=member.pk).values().get()
    record_before = PaymentRecord.objects.filter(pk=record.pk).values().get()
    with pytest.raises(ValueError, match="резерв"):
        finalize_successful_payment(record)
    assert (
        ClubMemberBalanceTransaction.objects.filter(pk=reserved.pk).values().get()
        == reserve_before
    )
    assert ClubMember.objects.filter(pk=member.pk).values().get() == member_before
    assert PaymentRecord.objects.filter(pk=record.pk).values().get() == record_before
    assert record.status == "succeeded"
    assert not tournament.entry_payments.exists()
    assert not tournament.participants.exists()
    assert not TournamentEntryRefundRequest.objects.exists()


@pytest.mark.parametrize("already_confirmed", [False, True])
def test_tournament_payment_reservations_confirm_once_without_second_balance_debit(
    balance_payment: BalancePayment, already_confirmed: bool
) -> None:
    """Подтверждённый до сбоя резерв допускает повтор, сохраняя дату и полную сумму взноса."""
    member, tournament, reserved, record = balance_payment
    if already_confirmed:
        confirm_reserved_balance(reserved)
    completed_at = reserved.completed_at
    finalize_successful_payment(record)
    reserved.refresh_from_db()
    member.refresh_from_db()
    assert reserved.status == "completed"
    assert reserved.completed_at is not None
    if already_confirmed:
        assert reserved.completed_at == completed_at
    reserve_before = (
        ClubMemberBalanceTransaction.objects.filter(pk=reserved.pk).values().get()
    )
    record_before = PaymentRecord.objects.filter(pk=record.pk).values().get()
    finalize_successful_payment(PaymentRecord.objects.get(pk=record.pk))
    assert (
        ClubMemberBalanceTransaction.objects.filter(pk=reserved.pk).values().get()
        == reserve_before
    )
    assert PaymentRecord.objects.filter(pk=record.pk).values().get() == record_before
    assert member.balance == Decimal("200.00")
    assert get_online_entry_payment_amount(tournament, record.user) == Decimal(
        "1000.00"
    )
    assert tournament.entry_payments.count() == 1


def test_tournament_payment_reservations_roll_back_confirmation_until_finalization_succeeds(
    balance_payment: BalancePayment,
) -> None:
    """Сбой после подтверждения откатывает резерв, а повтор завершает тот же взнос."""
    member, tournament, reserved, record = balance_payment
    reserve_before = (
        ClubMemberBalanceTransaction.objects.filter(pk=reserved.pk).values().get()
    )
    with (
        patch(
            "apps.tournaments.models.TournamentEntryPayment.objects.get_or_create",
            side_effect=RuntimeError("Отказ после подтверждения резерва"),
        ),
        pytest.raises(RuntimeError, match="после подтверждения"),
    ):
        finalize_successful_payment(record)
    assert (
        ClubMemberBalanceTransaction.objects.filter(pk=reserved.pk).values().get()
        == reserve_before
    )
    record.refresh_from_db()
    assert record.status == "succeeded"
    assert not record.metadata.get("_tournament_entry_finalized_at")
    assert not tournament.participants.exists()
    finalize_successful_payment(record)
    reserved.refresh_from_db()
    member.refresh_from_db()
    assert reserved.status == "completed"
    assert member.balance == Decimal("200.00")
    assert tournament.entry_payments.count() == 1


@pytest.mark.parametrize("terminal_status", ["cancelled", "completed"])
def test_tournament_payment_reservations_late_refund_uses_total_snapshot(
    balance_payment: BalancePayment, terminal_status: str
) -> None:
    """Поздний внешний успех учитывает резерв, но возвращается по старой, а не новой цене."""
    member, tournament, reserved, record = balance_payment
    tournament.participants.add(record.user.player)
    if terminal_status == "cancelled":
        with patch("apps.tournaments.cancel._notify_cancelled"):
            assert cancel_tournament(tournament)
    tournament.status = terminal_status
    tournament.entry_fee = Decimal("2700.00")
    tournament.save(update_fields=["status", "entry_fee"])
    finalize_successful_payment(record)
    refund = TournamentEntryRefundRequest.objects.get(
        tournament=tournament, user=record.user
    )
    assert refund.amount == Decimal("1000.00")
    record.refresh_from_db()
    reserved.refresh_from_db()
    member.refresh_from_db()
    assert record.amount == Decimal("600.00")
    assert record.metadata["total_amount"] == "1000.00"
    assert record.metadata["tournament_entry_refund_ref"] == refund.refund_ref
    assert reserved.status == "completed"
    assert member.balance == Decimal("200.00")
    assert tournament.participants.filter(user=record.user).exists()
    finalize_successful_payment(record)
    assert TournamentEntryRefundRequest.objects.count() == 1
