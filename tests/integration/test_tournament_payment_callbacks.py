"""Турнирные return и webhook сохраняют взнос однократно и не оживляют регистрацию."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Literal
from unittest.mock import patch

import pytest
from django.conf import settings
from django.http import HttpResponseBase
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.payments.models import PaymentRecord
from apps.tournaments.cancel import cancel_tournament
from apps.tournaments.models import (
    Tournament,
    TournamentEntryRefundRequest,
    TournamentOnsitePayment,
    TournamentPostpaymentInvoice,
    TournamentTeam,
)
from apps.users.models import Player
from tests.support.factories import make_payment_record, make_player, make_tournament

CallbackKind = Literal["return", "webhook"]

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("deny_external_network")]


@pytest.fixture
def callback_player(client: Client) -> Player:
    """Авторизовать владельца платежа без обращения к внешним сервисам."""
    player = make_player(email_suffix="tournament-callback", email_verified=True)
    client.force_login(player.user)
    return player


def _call_callback(
    client: Client, record: PaymentRecord, kind: CallbackKind
) -> HttpResponseBase:
    """Вызвать настоящий endpoint с сохранённой сервером платёжной сессией."""
    if kind == "webhook":
        return client.post(
            reverse("payment_webhook"),
            json.dumps(
                {
                    "event": "payment.succeeded",
                    "object": {"id": record.yookassa_payment_id},
                }
            ),
            content_type="application/json",
            secure=True,
        )
    session = client.session
    session["yookassa_pending"] = {
        "payment_id": record.yookassa_payment_id,
        "payment_type": "tournament",
        "item_id": record.item_id,
        "next": "",
    }
    session.save()
    session_key = session.session_key
    assert session_key is not None
    client.cookies[settings.SESSION_COOKIE_NAME] = session_key
    response = client.get(reverse("payment_return"), secure=True)
    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("payment_success"))
    assert "yookassa_pending" not in client.session
    return response


def _add_participant(tournament: Tournament, player: Player) -> TournamentTeam | None:
    """Сохранить действующего участника, включая неполную парную команду."""
    if tournament.is_doubles():
        return TournamentTeam.objects.create(tournament=tournament, player1=player)
    tournament.participants.add(player)
    return None


@pytest.mark.parametrize("first", ["return", "webhook"])
@pytest.mark.parametrize("doubles", [False, True])
@pytest.mark.parametrize("postpayment", [False, True])
def test_tournament_callback_endpoints_are_idempotent_after_removal(
    client: Client,
    callback_player: Player,
    first: CallbackKind,
    doubles: bool,
    postpayment: bool,
) -> None:
    """Оба порядка callback сохраняют сумму и даты, а повторы не возвращают удалённого."""
    tournament = make_tournament(
        start_date=timezone.localdate() + timedelta(days=7),
        variant="doubles" if doubles else "singles",
        entry_fee=1700,
        allow_onsite_payment=True,
        allow_postpayment=postpayment,
        postpayment_window_started_at=timezone.now() if postpayment else None,
    )
    team = _add_participant(tournament, callback_player)
    invoice = None
    onsite = None
    metadata: dict[str, object] = {"total_amount": "700.25"}
    if postpayment:
        invoice = TournamentPostpaymentInvoice.objects.create(
            tournament=tournament,
            user=callback_player.user,
            amount=Decimal("700.25"),
            due_at=timezone.now() + timedelta(hours=1),
        )
        metadata["postpayment_invoice_id"] = str(invoice.pk)
    else:
        onsite = TournamentOnsitePayment.objects.create(
            tournament=tournament, user=callback_player.user, amount=700
        )
    record = make_payment_record(
        callback_player.user,
        payment_type="tournament",
        item_id=str(tournament.pk),
        amount="700.25",
        status="pending",
        metadata=metadata,
    )
    with (
        patch(
            "apps.payments.yookassa_client.get_payment_status", return_value="succeeded"
        ) as get_status,
        patch("apps.payments.views._notify_successful_tournament_payment") as notify,
        patch("apps.payments.views._progress_tournament_after_payment") as progress,
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        response = _call_callback(client, record, first)
        assert response.status_code == (302 if first == "return" else 200)
        record.refresh_from_db()
        assert record.status == "succeeded"
        assert record.metadata.get("_tournament_entry_finalized_at")
        assert record.amount == Decimal("700.25")
        assert record.metadata["total_amount"] == "700.25"
        paid_history = tournament.entry_payments.values().get()
        invoice_history = (
            tournament.postpayment_invoices.values().get() if invoice else None
        )
        onsite_history = tournament.onsite_payments.values().get() if onsite else None
        if invoice is not None:
            invoice.refresh_from_db()
            assert invoice.status == "paid"
            assert invoice.paid_at is not None
            assert invoice.amount == Decimal("700.25")
        if onsite is not None:
            onsite.refresh_from_db()
            assert onsite.status == "replaced"
            assert onsite.received_at is None
        if team is not None:
            team.delete()
        else:
            tournament.participants.remove(callback_player)
        record_history = PaymentRecord.objects.filter(pk=record.pk).values().get()
        for kind in ("return", "webhook", "return", "webhook"):
            response = _call_callback(client, record, kind)
            assert response.status_code == (302 if kind == "return" else 200)
        get_status.assert_called_once_with(record.yookassa_payment_id)
        notify.assert_not_called()
        progress.assert_not_called()
    notify.assert_called_once()
    progress.assert_called_once_with(tournament.pk)
    assert tournament.entry_payments.values().get() == paid_history
    assert PaymentRecord.objects.filter(pk=record.pk).values().get() == record_history
    if invoice_history is not None:
        assert tournament.postpayment_invoices.values().get() == invoice_history
    if onsite_history is not None:
        assert tournament.onsite_payments.values().get() == onsite_history
    assert not tournament.participants.exists()
    assert not tournament.teams.exists()
    assert not TournamentEntryRefundRequest.objects.exists()


@pytest.mark.parametrize("kind", ["return", "webhook"])
@pytest.mark.parametrize("gateway_status", [None, "waiting_for_capture", "canceled"])
def test_tournament_callback_requires_server_verified_success(
    client: Client,
    callback_player: Player,
    kind: CallbackKind,
    gateway_status: str | None,
) -> None:
    """Успех в теле webhook или сессии не заменяет подтверждение шлюза."""
    tournament = make_tournament(start_date=timezone.localdate() + timedelta(days=7))
    record = make_payment_record(
        callback_player.user,
        payment_type="tournament",
        item_id=str(tournament.pk),
        status="pending",
    )
    with patch(
        "apps.payments.yookassa_client.get_payment_status", return_value=gateway_status
    ):
        _call_callback(client, record, kind)
    record.refresh_from_db()
    assert record.status == "pending"
    assert not record.metadata.get("_tournament_entry_finalized_at")
    assert not tournament.participants.exists()
    assert not tournament.entry_payments.exists()
    assert not TournamentEntryRefundRequest.objects.exists()


@pytest.mark.parametrize("kind", ["return", "webhook"])
def test_tournament_callback_retries_previously_verified_unfinalized_record(
    client: Client, callback_player: Player, kind: CallbackKind
) -> None:
    """После сбоя финализации оба endpoint обрабатывают сохранённый успех без новой проверки."""
    tournament = make_tournament(start_date=timezone.localdate() + timedelta(days=7))
    record = make_payment_record(
        callback_player.user, payment_type="tournament", item_id=str(tournament.pk)
    )
    with patch("apps.payments.yookassa_client.get_payment_status") as get_status:
        _call_callback(client, record, kind)
        _call_callback(client, record, kind)
    get_status.assert_not_called()
    record.refresh_from_db()
    assert record.metadata.get("_tournament_entry_finalized_at")
    assert tournament.participants.filter(pk=callback_player.pk).exists()
    assert tournament.entry_payments.count() == 1


@pytest.mark.parametrize("kind", ["return", "webhook"])
@pytest.mark.parametrize("doubles", [False, True])
def test_tournament_callback_after_cancellation_refunds_retained_participant(
    client: Client, callback_player: Player, kind: CallbackKind, doubles: bool
) -> None:
    """Сохранённый для возобновления участник не лишается возврата позднего взноса."""
    tournament = make_tournament(
        start_date=timezone.localdate() + timedelta(days=7),
        variant="doubles" if doubles else "singles",
        entry_fee=1000,
        allow_onsite_payment=True,
    )
    team = _add_participant(tournament, callback_player)
    onsite = TournamentOnsitePayment.objects.create(
        tournament=tournament, user=callback_player.user, amount=1000
    )
    record = make_payment_record(
        callback_player.user,
        payment_type="tournament",
        item_id=str(tournament.pk),
        amount="700.25",
        status="pending",
    )
    with patch("apps.tournaments.cancel._notify_cancelled"):
        assert cancel_tournament(tournament) is True
    onsite.refresh_from_db()
    assert onsite.status == "cancelled"
    onsite_history = tournament.onsite_payments.values().get()
    with patch(
        "apps.payments.yookassa_client.get_payment_status", return_value="succeeded"
    ):
        _call_callback(client, record, kind)
        _call_callback(client, record, "webhook" if kind == "return" else "return")
    tournament.refresh_from_db()
    record.refresh_from_db()
    assert tournament.status == "cancelled"
    assert record.status == "succeeded"
    assert record.metadata.get("_tournament_entry_finalized_at")
    assert tournament.entry_payments.count() == 1
    assert tournament.onsite_payments.values().get() == onsite_history
    if team is not None:
        assert tournament.teams.filter(pk=team.pk, player1=callback_player).exists()
    else:
        assert tournament.participants.filter(pk=callback_player.pk).exists()
    refund = TournamentEntryRefundRequest.objects.get(
        tournament=tournament, user=callback_player.user
    )
    assert refund.amount == Decimal("700.25")
    assert record.metadata["tournament_entry_refund_ref"] == refund.refund_ref


@pytest.mark.parametrize("first", ["return", "webhook"])
@pytest.mark.parametrize("doubles", [False, True])
@pytest.mark.parametrize("terminal_status", ["cancelled", "completed"])
def test_tournament_callback_distinct_late_payments_keep_separate_refund_snapshots(
    client: Client,
    callback_player: Player,
    first: CallbackKind,
    doubles: bool,
    terminal_status: str,
) -> None:
    """Разные поздние взносы получают разные возвраты, а повторы не меняют старую историю."""
    tournament = make_tournament(
        start_date=timezone.localdate() + timedelta(days=7),
        variant="doubles" if doubles else "singles",
        entry_fee=1000,
        allow_onsite_payment=True,
    )
    team = _add_participant(tournament, callback_player)
    onsite = TournamentOnsitePayment.objects.create(
        tournament=tournament,
        user=callback_player.user,
        amount="1000.00",
        status=TournamentOnsitePayment.Status.RECEIVED,
        received_at=timezone.now(),
        received_by=callback_player.user,
    )
    records = [
        make_payment_record(
            callback_player.user,
            yookassa_payment_id=f"distinct-late-{index}",
            payment_type="tournament",
            item_id=str(tournament.pk),
            amount=amount,
            status="pending",
            metadata={"total_amount": amount},
        )
        for index, amount in enumerate(("700.25", "300.75"))
    ]
    if terminal_status == "cancelled":
        with patch("apps.tournaments.cancel._notify_cancelled"):
            assert cancel_tournament(tournament)
    tournament.status = terminal_status
    tournament.entry_fee = Decimal("2700.00")
    tournament.save(update_fields=["status", "entry_fee"])
    onsite_history = TournamentOnsitePayment.objects.filter(pk=onsite.pk).values().get()
    onsite_refund_history = list(
        TournamentEntryRefundRequest.objects.filter(onsite_payment=onsite).values()
    )
    second: CallbackKind = "webhook" if first == "return" else "return"
    with (
        patch(
            "apps.payments.yookassa_client.get_payment_status", return_value="succeeded"
        ) as get_status,
        patch("apps.payments.views._notify_successful_tournament_payment") as notify,
        patch("apps.payments.views._progress_tournament_after_payment") as progress,
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        _call_callback(client, records[0], first)
        paid_history = tournament.entry_payments.values().get()
        _call_callback(client, records[1], second)
        assert tournament.entry_payments.values().get() == paid_history
        record_history = list(PaymentRecord.objects.order_by("pk").values())
        refund_history = list(
            TournamentEntryRefundRequest.objects.order_by("pk").values()
        )
        for record in reversed(records):
            for kind in (first, second, first, second):
                _call_callback(client, record, kind)
        assert get_status.call_count == 2
        notify.assert_not_called()
        progress.assert_not_called()
    assert notify.call_count == 2
    assert progress.call_count == 2
    assert list(PaymentRecord.objects.order_by("pk").values()) == record_history
    assert (
        list(TournamentEntryRefundRequest.objects.order_by("pk").values())
        == refund_history
    )
    assert (
        TournamentOnsitePayment.objects.filter(pk=onsite.pk).values().get()
        == onsite_history
    )
    assert (
        list(
            TournamentEntryRefundRequest.objects.filter(onsite_payment=onsite).values()
        )
        == onsite_refund_history
    )
    online_refunds = TournamentEntryRefundRequest.objects.filter(
        onsite_payment__isnull=True
    )
    assert sorted(online_refunds.values_list("amount", flat=True)) == [
        Decimal("300.75"),
        Decimal("700.25"),
    ]
    for record in records:
        record.refresh_from_db()
        refund = online_refunds.get(
            refund_ref=record.metadata["tournament_entry_refund_ref"]
        )
        assert refund.amount == record.amount
        assert Decimal(record.metadata["total_amount"]) == record.amount
    assert tournament.entry_payments.values().get() == paid_history
    if team is not None:
        assert tournament.teams.filter(pk=team.pk, player1=callback_player).exists()
    else:
        assert tournament.participants.filter(pk=callback_player.pk).exists()
