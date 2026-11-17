"""Выбор оплаты на корте не подменяет факт оплаты и не создаёт постоплатный долг."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.db import transaction
from django.http import HttpResponseBase
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.finance_services import reserve_member_balance
from apps.clubs.models import (
    ClubMember,
    ClubMemberBalanceTransaction,
    ClubMemberPlan,
    ClubMemberRole,
    ClubMemberStatus,
    ClubPlayerPlan,
)
from apps.payments.models import PaymentRecord
from apps.payments.views import finalize_successful_payment
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.tournaments.cancel import cancel_tournament, reopen_tournament
from apps.tournaments.entry_payment import (
    RegistrationPaymentError,
    RegistrationPaymentMethod,
    apply_registration_payment,
    confirm_onsite_payment,
    get_online_entry_payment_amount,
    get_registration_payment_options,
    has_received_entry_payment,
)
from apps.tournaments.models import (
    Tournament,
    TournamentEntryPayment,
    TournamentEntryRefundRequest,
    TournamentOnsitePayment,
    TournamentPostpaymentInvoice,
    TournamentTeam,
)
from apps.tournaments.postpayment import (
    _remove_unpaid_players,
    build_participant_payment_statuses,
    get_pending_postpayment_users,
    get_postpayment_progress,
    open_postpayment_window,
    resend_postpayment_payment_link,
    send_1h_reminders,
    settle_postpayment_with_available_fancoin,
    sync_postpayment_invoices_deadline,
    try_cover_registration_with_fancoin,
)
from apps.tournaments.views import _save_tournament_registration
from apps.tournaments.withdraw import withdraw_participant
from apps.users.models import Player, User
from tests.integration.test_payments_finalize import mute_payment_side_effects
from tests.support.factories import (
    make_club,
    make_payment_record,
    make_player,
    make_subscription,
    make_tournament,
    make_user,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def player() -> Player:
    """Участник с заполненным и подтверждённым профилем."""
    return _ready_player("onsite-player")


def _ready_player(suffix: str) -> Player:
    """Создать профиль игрока, не передавая его поля в модель пользователя."""
    result = make_player(
        email_suffix=suffix,
        email_verified=True,
        first_name="Иван",
        last_name=suffix,
        phone="+79990000000",
    )
    result.birth_date = date(1990, 1, 1)
    result.gender = "male"
    result.is_verified = True
    result.tennis_entry_level_set = True
    result.skill_level = "amateur"
    result.save(
        update_fields=[
            "birth_date",
            "gender",
            "is_verified",
            "tennis_entry_level_set",
            "skill_level",
        ]
    )
    return result


@pytest.fixture
def client(player: Player) -> Client:
    """Авторизованный участник."""
    user = player.user
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    result = Client()
    result.force_login(user)
    return result


def _cup(**kwargs: Any) -> Tournament:
    """Платное соревнование с доступным выбором оплаты на площадке."""
    tournament = make_tournament(
        entry_fee=Decimal("1000.00"), allow_onsite_payment=True, **kwargs
    )
    tournament.allowed_categories.create(category="amateur")
    return tournament


def _register(
    client: Client, tournament: Tournament, method: str = "onsite"
) -> HttpResponseBase:
    """Выбрать способ и отправить регистрацию одиночного игрока."""
    return client.post(
        reverse("tournament_register", kwargs={"slug": tournament.slug}),
        {"payment_method": method},
        secure=True,
    )


def test_get_shows_payment_choice_without_registration(
    client: Client, player: Player
) -> None:
    """Открытие выбора не резервирует место и не списывает деньги."""
    tournament = _cup(duration="single")
    response = client.get(
        reverse("tournament_register", kwargs={"slug": tournament.slug}), secure=True
    )
    assert response.status_code == 200
    assert "На корте" in response.content.decode()
    assert not tournament.participants.exists()
    assert not tournament.onsite_payments.exists()


@pytest.mark.parametrize("duration", ["single", "weekend", "multi"])
def test_onsite_registers_without_marking_fee_paid(
    client: Client, player: Player, duration: str
) -> None:
    """Время проведения не ограничивает разрешённую оплату на корте."""
    tournament = _cup(duration=duration)
    response = _register(client, tournament)
    assert response.status_code == 302
    assert tournament.participants.filter(pk=player.pk).exists()
    reservation = tournament.onsite_payments.get(user=player.user)
    assert reservation.status == "expected"
    assert reservation.amount == Decimal("1000.00")
    assert reservation.received_at is None
    assert not tournament.entry_payments.exists()
    assert not tournament.registration_coverages.exists()
    assert get_pending_postpayment_users(tournament) == []


def test_invalid_payment_method_is_rejected(client: Client, player: Player) -> None:
    """Подделанное значение не позволяет пройти мимо оплаты."""
    tournament = _cup()
    response = _register(client, tournament, "forged")
    assert response.status_code in (200, 400)
    assert not tournament.participants.exists()
    assert not tournament.onsite_payments.exists()


def test_disallowed_onsite_method_is_rejected(client: Client, player: Player) -> None:
    """Нельзя использовать оплату на корте, выключенную организатором."""
    tournament = _cup()
    tournament.allow_onsite_payment = False
    tournament.save(update_fields=["allow_onsite_payment"])
    response = _register(client, tournament)
    assert response.status_code in (200, 400)
    assert not tournament.participants.exists()


def test_both_deferred_choices_are_independent(client: Client, player: Player) -> None:
    """Постоплата остаётся отдельным выбором и ожидает открытия окна."""
    tournament = _cup(allow_postpayment=True)
    _register(client, tournament, "postpayment")
    assert tournament.participants.filter(pk=player.pk).exists()
    assert not tournament.onsite_payments.exists()
    assert not tournament.postpayment_invoices.exists()
    assert [user.pk for user in get_pending_postpayment_users(tournament)] == [
        player.user_id
    ]


def test_onsite_is_not_debited_from_ft_even_when_balance_appears(
    client: Client, player: Player
) -> None:
    """Выбранная на площадке оплата не меняется на FT скрытым списанием."""
    tournament = _cup(allow_postpayment=True)
    _register(client, tournament)
    tier = SubscriptionTier.objects.create(name="onsite-ft", price=1000)
    sub = UserSubscription.objects.create(
        user=player.user,
        tier=tier,
        start_date=timezone.now(),
        end_date=timezone.now() + timedelta(days=30),
        fancoin_balance=30,
    )
    player.user.refresh_from_db()
    assert try_cover_registration_with_fancoin(tournament, player.user) is False
    assert open_postpayment_window(tournament) == (0, 0)
    sub.refresh_from_db()
    assert sub.fancoin_balance == 30
    assert not tournament.registration_coverages.exists()
    assert not tournament.postpayment_invoices.exists()


def test_expected_onsite_survives_stale_invoice_expiry(
    client: Client, player: Player
) -> None:
    """Даже старый неоплаченный счёт не удаляет выбравшего оплату на корте."""
    tournament = _cup(allow_postpayment=True)
    _register(client, tournament)
    TournamentPostpaymentInvoice.objects.create(
        tournament=tournament,
        user=player.user,
        amount=1000,
        due_at=timezone.now() - timedelta(hours=1),
    )
    assert _remove_unpaid_players(tournament) == 0
    assert tournament.participants.filter(pk=player.pk).exists()


def test_only_manager_can_confirm_actual_receipt(
    client: Client, player: Player
) -> None:
    """Отметка получения защищена POST, CSRF и правами организатора."""
    tournament = _cup()
    _register(client, tournament)
    reservation = tournament.onsite_payments.get(user=player.user)
    url = reverse(
        "tournament_manage_confirm_onsite_payment",
        kwargs={"slug": tournament.slug, "payment_id": reservation.pk},
    )
    assert client.post(url, secure=True).status_code == 403
    admin_user = make_user(email="onsite-admin@test.local", is_staff=True)
    client.force_login(admin_user)
    assert client.get(url, secure=True).status_code == 405
    secure_client = Client(enforce_csrf_checks=True)
    secure_client.force_login(admin_user)
    assert secure_client.post(url, secure=True).status_code == 403
    assert client.post(url, secure=True).status_code == 302
    reservation.refresh_from_db()
    assert reservation.status == "received"
    assert reservation.received_by_id == admin_user.pk
    received_at = reservation.received_at
    assert received_at is not None
    client.post(url, secure=True)
    reservation.refresh_from_db()
    assert reservation.received_at == received_at
    assert not TournamentEntryPayment.objects.filter(tournament=tournament).exists()
    rows = build_participant_payment_statuses(tournament)
    assert rows[0].status == "Оплачено на корте (₽)"


def test_doubles_solo_and_join_track_each_players_choice(
    client: Client, player: Player
) -> None:
    """Каждый член пары самостоятельно выбирает способ взноса."""
    tournament = _cup(variant="doubles", max_teams=16, allow_postpayment=True)
    url = reverse("tournament_register_doubles", kwargs={"slug": tournament.slug})
    client.post(url, {"action": "solo", "payment_method": "onsite"}, secure=True)
    team = tournament.teams.get(player1=player)
    partner = _ready_player("onsite-partner")
    partner.user.email_verified = True
    partner.user.save(update_fields=["email_verified"])
    client.force_login(partner.user)
    client.post(
        url,
        {"action": "join", "team_id": team.pk, "payment_method": "postpayment"},
        secure=True,
    )
    team.refresh_from_db()
    assert team.player2_id == partner.pk
    assert tournament.onsite_payments.get().user_id == player.user_id
    assert [user.pk for user in get_pending_postpayment_users(tournament)] == [
        partner.user_id
    ]


def test_adding_partner_rolls_back_first_players_club_limit(
    client: Client, player: Player
) -> None:
    """Отказ второго игрока откатывает лимит, покрытие и создание команды."""
    club = make_club(use_player_plans=True)
    tournament = _cup(club=club, variant="doubles", max_teams=16)
    member = ClubMember.objects.create(
        club=club,
        user=player.user,
        role=ClubMemberRole.PLAYER,
        status=ClubMemberStatus.ACTIVE,
    )
    partner = _ready_player("onsite-rollback")
    partner.user.email_verified = True
    partner.user.save(update_fields=["email_verified"])
    ClubMember.objects.create(
        club=club,
        user=partner.user,
        role=ClubMemberRole.PLAYER,
        status=ClubMemberStatus.ACTIVE,
    )
    plan = ClubPlayerPlan.objects.create(
        club=club, name="Лимит", monthly_fee=1000, max_tournaments_per_month=4
    )
    ClubMemberPlan.objects.create(club_member=member, plan=plan, status="active")
    response = client.post(
        reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
        {
            "action": "add_partner",
            "partner_id": partner.pk,
            "payment_method": "covered",
            "partner_payment_method": "online",
        },
        secure=True,
    )
    assert response.status_code == 302
    assert not tournament.teams.exists()
    assert not tournament.registration_coverages.exists()
    assert not member.plan_slot_usages.filter(tournaments_used__gt=0).exists()


@pytest.mark.parametrize("doubles", [False, True])
def test_online_callback_records_previously_registered_players(
    client: Client, player: Player, doubles: bool
) -> None:
    """Онлайн-оплата учитывается и для существующего участника, и для пары."""
    tournament = _cup(variant="doubles" if doubles else "singles")
    if doubles:
        TournamentTeam.objects.create(tournament=tournament, player1=player)
    else:
        _register(client, tournament)
    record = make_payment_record(
        player.user,
        payment_type="tournament",
        item_id=str(tournament.pk),
        amount=Decimal("1000.00"),
    )
    with mute_payment_side_effects():
        finalize_successful_payment(record)
        finalize_successful_payment(record)
    assert tournament.entry_payments.filter(user=player.user).count() == 1
    assert not tournament.onsite_payments.filter(status="expected").exists()


def test_unpaid_multi_day_without_postpayment_cannot_use_ft(
    client: Client, player: Player
) -> None:
    """Наличие FT не обходится бесплатной регистрацией при обязательной предоплате."""
    tournament = _cup()
    tournament.allow_onsite_payment = False
    tournament.save(update_fields=["allow_onsite_payment"])
    tier = SubscriptionTier.objects.create(name="prepay-ft", price=1000)
    UserSubscription.objects.create(
        user=player.user,
        tier=tier,
        start_date=timezone.now(),
        end_date=timezone.now() + timedelta(days=30),
        fancoin_balance=30,
    )
    response = _register(client, tournament, "online")
    assert response.status_code == 302
    assert not tournament.participants.exists()
    assert not tournament.registration_coverages.exists()


def test_missing_payment_choice_does_not_reserve_a_place(client: Client) -> None:
    """При нескольких способах нельзя зарегистрироваться без явного выбора."""
    tournament = _cup(allow_postpayment=True)
    response = client.post(
        reverse("tournament_register", kwargs={"slug": tournament.slug}), secure=True
    )
    assert response.status_code == 400
    assert not tournament.participants.exists()
    assert not tournament.onsite_payments.exists()


def test_get_does_not_consume_or_create_club_plan_usage(
    client: Client, player: Player
) -> None:
    """Просмотр выбора с клубным тарифом не создаёт даже нулевой строки расхода."""
    club = make_club(use_player_plans=True)
    member = ClubMember.objects.create(
        club=club, user=player.user, status=ClubMemberStatus.ACTIVE
    )
    plan = ClubPlayerPlan.objects.create(
        club=club, name="Просмотр", monthly_fee=1000, max_tournaments_per_month=4
    )
    assignment = ClubMemberPlan.objects.create(
        club_member=member, plan=plan, status="active"
    )
    tournament = _cup(club=club)
    before = assignment.bonus_tournaments_balance
    response = client.get(
        reverse("tournament_register", kwargs={"slug": tournament.slug}), secure=True
    )
    assert response.status_code == 200
    assignment.refresh_from_db()
    assert assignment.bonus_tournaments_balance == before
    assert not member.plan_slot_usages.exists()
    assert not tournament.registration_coverages.exists()
    assert not tournament.participants.exists()


def test_unpaid_solo_doubles_player_is_in_the_payment_ledger(player: Player) -> None:
    """Игрок без партнёра тоже участвует в проверке долгов и отображении оплаты."""
    tournament = _cup(variant="doubles", allow_postpayment=True)
    TournamentTeam.objects.create(tournament=tournament, player1=player)
    assert [user.pk for user in get_pending_postpayment_users(tournament)] == [
        player.user_id
    ]
    assert [row.user_id for row in build_participant_payment_statuses(tournament)] == [
        player.user_id
    ]


@pytest.mark.parametrize("paid_position", [1, 2])
def test_expiry_preserves_paid_doubles_partner(
    player: Player, paid_position: int
) -> None:
    """Просрочка одного игрока оставляет оплаченного партнёра в прежней команде."""
    tournament = _cup(variant="doubles", allow_postpayment=True)
    partner = _ready_player("expiry-partner")
    team = TournamentTeam.objects.create(
        tournament=tournament, player1=player, player2=partner
    )
    paid_player, unpaid_player = (
        (player, partner) if paid_position == 1 else (partner, player)
    )
    payment = TournamentOnsitePayment.objects.create(
        tournament=tournament, user=paid_player.user, amount=1000
    )
    invoice = TournamentPostpaymentInvoice.objects.create(
        tournament=tournament,
        user=unpaid_player.user,
        amount=1000,
        due_at=timezone.now() - timedelta(minutes=1),
    )
    assert _remove_unpaid_players(tournament) == 1
    team.refresh_from_db()
    invoice.refresh_from_db()
    payment.refresh_from_db()
    assert team.player1_id == paid_player.pk
    assert team.player2_id is None
    assert payment.status == "expected"
    assert invoice.status == "expired"
    assert get_pending_postpayment_users(tournament) == []


@pytest.mark.parametrize("received", [False, True])
def test_stale_onsite_invoice_is_ignored_by_cron_reminders_and_deadlines(
    client: Client, player: Player, received: bool
) -> None:
    """Старый онлайн-счёт не оживает и не рассылается выбравшему оплату на корте."""
    tournament = _cup(
        allow_postpayment=True,
        postpayment_window_started_at=timezone.now(),
    )
    tournament.participants.add(player)
    payment = TournamentOnsitePayment.objects.create(
        tournament=tournament, user=player.user, amount=1000
    )
    if received:
        confirm_onsite_payment(tournament, payment.pk, received_by=player.user)
    invoice = TournamentPostpaymentInvoice.objects.create(
        tournament=tournament,
        user=player.user,
        amount=1000,
        due_at=timezone.now() + timedelta(minutes=30),
    )
    other = _cup(slug="real-online-debt", allow_postpayment=True)
    other.participants.add(player)
    online_invoice = TournamentPostpaymentInvoice.objects.create(
        tournament=other,
        user=player.user,
        amount=1000,
        due_at=timezone.now() + timedelta(minutes=30),
    )
    with (
        patch("apps.tournaments.postpayment.send_to_user_by_user") as send,
        patch("apps.tournaments.postpayment.create_notification"),
        patch("apps.core.email_service.send_tournament_postpayment_1h_reminder_email"),
    ):
        assert send_1h_reminders() == 1
        assert send.call_count == 1
        assert resend_postpayment_payment_link(tournament, player.user)[0] is False
    before_due = invoice.due_at
    assert sync_postpayment_invoices_deadline(tournament) == 0
    invoice.refresh_from_db()
    online_invoice.refresh_from_db()
    assert invoice.due_at == before_due
    assert invoice.reminder_1h_sent_at is None
    assert online_invoice.reminder_1h_sent_at is not None
    assert get_postpayment_progress(tournament)["pending"] == 0
    make_subscription(player.user, fancoin_balance=30)
    with (patch("apps.tournaments.postpayment._send_fancoin_settled_notification"),):
        assert settle_postpayment_with_available_fancoin() == 1
    assert not tournament.registration_coverages.exists()
    assert other.registration_coverages.filter(user=player.user).exists()


def test_receipt_cannot_target_another_tournaments_payment(
    client: Client, player: Player
) -> None:
    """ID чужого ожидания не даёт изменить его через другой турнир."""
    tournament = _cup()
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    other = _cup(slug="other-receipt")
    manager = make_user(email="other-manager@test.local", is_staff=True)
    client.force_login(manager)
    response = client.post(
        reverse(
            "tournament_manage_confirm_onsite_payment",
            kwargs={"slug": other.slug, "payment_id": payment.pk},
        ),
        secure=True,
    )
    assert response.status_code == 404
    payment.refresh_from_db()
    assert payment.status == "expected"


@pytest.mark.parametrize("received", [False, True])
def test_manager_removal_closes_only_expectations_and_requests_real_refund(
    client: Client, player: Player, received: bool
) -> None:
    """Удаление сохраняет историю; возврат запрашивается только для полученных денег."""
    tournament = _cup()
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    manager = make_user(email="removal-manager@test.local", is_staff=True)
    if received:
        confirm_onsite_payment(tournament, payment.pk, received_by=manager)
    tournament.entry_fee = Decimal("2500")
    tournament.save(update_fields=["entry_fee"])
    client.force_login(manager)
    url = reverse(
        "tournament_manage_remove_participant", kwargs={"slug": tournament.slug}
    )
    with patch("apps.telegram_bot.notifications.notify_tournament_removed_refund"):
        response = client.post(url, {"player_id": player.pk}, secure=True)
        client.post(url, {"player_id": player.pk}, secure=True)
    assert response.status_code == 302
    assert not tournament.participants.exists()
    payment.refresh_from_db()
    assert payment.status == ("received" if received else "cancelled")
    if received:
        refund = TournamentEntryRefundRequest.objects.get(onsite_payment=payment)
        assert refund.amount == Decimal("1000.00")
        assert payment.received_at is not None
    else:
        assert payment.closed_reason == "registration_removed"
        assert not TournamentEntryRefundRequest.objects.exists()
    if not received:
        with pytest.raises(RegistrationPaymentError):
            confirm_onsite_payment(tournament, payment.pk, received_by=manager)
    else:
        assert (
            confirm_onsite_payment(tournament, payment.pk, received_by=manager) is False
        )


@pytest.mark.parametrize("received", [False, True])
def test_withdrawal_preserves_cash_history(
    client: Client, player: Player, received: bool
) -> None:
    """Снятие после старта не оставляет ожидание активным и не стирает получение."""
    tournament = _cup(format="round_robin")
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    if received:
        confirm_onsite_payment(tournament, payment.pk, received_by=player.user)
    tournament.bracket_generated = True
    tournament.status = "active"
    tournament.save(update_fields=["bracket_generated", "status"])
    with (
        patch("apps.tournaments.withdraw._notify_withdrawn_user"),
        patch("apps.tournaments.withdraw._notify_opponents"),
    ):
        ok, message = withdraw_participant(tournament, player=player)
    assert ok, message
    payment.refresh_from_db()
    assert payment.status == ("received" if received else "cancelled")
    if received:
        assert (
            TournamentEntryRefundRequest.objects.get(onsite_payment=payment).amount
            == payment.amount
        )
    else:
        assert payment.closed_reason == "withdrawn"


def test_cancellation_and_reopen_keep_onsite_choice_and_amount(
    client: Client, player: Player
) -> None:
    """Возобновление добавляет новую договорённость, сохраняя отменённую и её сумму."""
    tournament = _cup(allow_postpayment=True)
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    sub = make_subscription(player.user, fancoin_balance=30)
    with patch("apps.tournaments.cancel._notify_cancelled"):
        cancel_tournament(tournament)
        cancel_tournament(tournament)
    payment.refresh_from_db()
    assert payment.status == "cancelled"
    assert payment.closed_reason == "tournament_cancelled"
    tournament.entry_fee = Decimal("3000")
    tournament.save(update_fields=["entry_fee"])
    reopen_tournament(
        tournament,
        start_date=timezone.localdate() + timedelta(days=2),
        registration_deadline=timezone.now() + timedelta(days=1),
        notify=False,
    )
    restored = tournament.onsite_payments.get(status="expected")
    assert restored.pk != payment.pk
    assert restored.amount == Decimal("1000.00")
    sub.refresh_from_db()
    assert sub.fancoin_balance == 30
    assert not tournament.registration_coverages.exists()
    assert tournament.onsite_payments.count() == 2


def test_cancellation_creates_manual_refund_without_ft_compensation(
    client: Client, player: Player
) -> None:
    """Полученный на площадке взнос не превращается в FT при отмене турнира."""
    tournament = _cup(allow_postpayment=True)
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    confirm_onsite_payment(tournament, payment.pk, received_by=player.user)
    sub = make_subscription(player.user, fancoin_balance=30)
    with patch("apps.tournaments.cancel._notify_cancelled"):
        cancel_tournament(tournament)
        cancel_tournament(tournament)
    payment.refresh_from_db()
    sub.refresh_from_db()
    assert payment.status == "received"
    assert payment.received_at is not None
    assert sub.fancoin_balance == 30
    assert (
        TournamentEntryRefundRequest.objects.filter(onsite_payment=payment).count() == 1
    )


@pytest.mark.parametrize("received", [False, True])
def test_club_self_cancellation_never_credits_unreceived_cash(
    client: Client, player: Player, received: bool
) -> None:
    """Клубная отмена не возвращает на баланс деньги, которые не были оплачены онлайн."""
    club = make_club(use_player_plans=True)
    member = ClubMember.objects.create(
        club=club, user=player.user, status=ClubMemberStatus.ACTIVE
    )
    plan = ClubPlayerPlan.objects.create(
        club=club, name="Не использован", monthly_fee=1000, max_tournaments_per_month=4
    )
    assignment = ClubMemberPlan.objects.create(
        club_member=member, plan=plan, status="active"
    )
    tournament = _cup(club=club)
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    if received:
        confirm_onsite_payment(tournament, payment.pk, received_by=player.user)
    response = client.post(
        reverse(
            "clubs:my_tournament_cancel", kwargs={"tournament_slug": tournament.slug}
        ),
        secure=True,
    )
    assert response.status_code == 302
    member.refresh_from_db()
    assignment.refresh_from_db()
    payment.refresh_from_db()
    assert member.balance == 0
    assert assignment.bonus_tournaments_balance == 0
    assert not member.plan_slot_usages.exists()
    assert payment.status == ("received" if received else "cancelled")
    assert (
        TournamentEntryRefundRequest.objects.filter(onsite_payment=payment).exists()
        is received
    )


@pytest.mark.parametrize(
    "closed_by",
    ["cancelled", "completed", "active", "bracket", "deadline", "full", "window"],
)
def test_late_callback_keeps_payment_without_reopening_registration(
    player: Player, closed_by: str
) -> None:
    """Успешный поздний платёж сохраняется, но не добавляет игрока после закрытия набора."""
    tournament = _cup()
    if closed_by in {"cancelled", "completed", "active"}:
        tournament.status = closed_by
    elif closed_by == "bracket":
        tournament.bracket_generated = True
    elif closed_by == "deadline":
        tournament.registration_deadline = timezone.now() - timedelta(minutes=1)
    elif closed_by == "full":
        tournament.max_participants = 1
        tournament.participants.add(_ready_player("filled-place"))
    else:
        tournament.postpayment_window_started_at = timezone.now()
    tournament.save()
    record = make_payment_record(
        player.user, payment_type="tournament", item_id=str(tournament.pk), amount=700
    )
    with mute_payment_side_effects():
        finalize_successful_payment(record)
        finalize_successful_payment(record)
    assert not tournament.participants.filter(pk=player.pk).exists()
    assert tournament.entry_payments.filter(user=player.user).count() == 1
    assert TournamentEntryRefundRequest.objects.get(
        tournament=tournament, user=player.user
    ).amount == Decimal("700.00")


def test_duplicate_callback_does_not_change_invoice_paid_at_or_readd_player(
    player: Player,
) -> None:
    """Повтор callback идемпотентен даже после ручного удаления участника."""
    tournament = _cup(allow_postpayment=True)
    tournament.participants.add(player)
    invoice = TournamentPostpaymentInvoice.objects.create(
        tournament=tournament,
        user=player.user,
        amount=1000,
        due_at=timezone.now() + timedelta(hours=1),
    )
    record = make_payment_record(
        player.user,
        payment_type="tournament",
        item_id=str(tournament.pk),
        metadata={"postpayment_invoice_id": str(invoice.pk)},
    )
    with mute_payment_side_effects():
        finalize_successful_payment(record)
        invoice.refresh_from_db()
        first_paid_at = invoice.paid_at
        tournament.participants.remove(player)
        finalize_successful_payment(PaymentRecord.objects.get(pk=record.pk))
    invoice.refresh_from_db()
    assert invoice.paid_at == first_paid_at
    assert not tournament.participants.filter(pk=player.pk).exists()
    assert tournament.entry_payments.filter(user=player.user).count() == 1


def test_existing_receipt_is_not_erased_when_online_payment_arrives(
    client: Client, player: Player
) -> None:
    """Второй реальный платёж не затирает ранее подтверждённые деньги на корте."""
    tournament = _cup()
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    confirm_onsite_payment(tournament, payment.pk, received_by=player.user)
    payment.refresh_from_db()
    before = (payment.amount, payment.received_at, payment.received_by_id)
    record = make_payment_record(
        player.user, payment_type="tournament", item_id=str(tournament.pk)
    )
    with mute_payment_side_effects():
        finalize_successful_payment(record)
    payment.refresh_from_db()
    assert payment.status == "received"
    assert (payment.amount, payment.received_at, payment.received_by_id) == before


def test_online_payment_is_not_started_before_postpayment_window(
    client: Client, player: Player
) -> None:
    """Прямой URL и поддельный POST не обходят отложенное окно оплаты."""
    tournament = _cup(allow_postpayment=True)
    url = reverse("payment_preview")
    response = client.get(url, {"type": "tournament", "id": tournament.pk}, secure=True)
    assert response.status_code == 302
    with patch("apps.payments.views.create_payment") as create:
        response = client.post(
            reverse("payment_process"),
            {
                "type": "tournament",
                "id": tournament.pk,
                "amount": "1000",
                "offer_accepted": "on",
            },
            secure=True,
        )
    assert response.status_code == 302
    create.assert_not_called()
    assert not PaymentRecord.objects.exists()


def test_selected_onsite_amount_uses_subscription_discount(
    client: Client, player: Player
) -> None:
    """Обещанная сумма на корте фиксирует ту же скидку, что и онлайн-взнос."""
    tournament = _cup(duration="single")
    make_subscription(player.user, tier_kwargs={"one_day_tournament_discount": 20})
    player.user.refresh_from_db()
    options = get_registration_payment_options(tournament, player.user)
    assert "800.00" in next(
        option.description for option in options if option.value == "onsite"
    )
    _register(client, tournament)
    assert tournament.onsite_payments.get().amount == Decimal("800.00")


@pytest.mark.parametrize("window", ["not_started", "ended", "not_registered"])
def test_invoice_cannot_bypass_actual_postpayment_window(
    client: Client, player: Player, window: str
) -> None:
    """Даже действующий ID счёта не открывает окно и не восстанавливает снятого игрока."""
    tournament = _cup(allow_postpayment=True, postpayment_deadline_hours=1)
    if window != "not_started":
        tournament.postpayment_window_started_at = timezone.now() - timedelta(
            hours=2 if window == "ended" else 0
        )
        tournament.save(update_fields=["postpayment_window_started_at"])
    if window != "not_registered":
        tournament.participants.add(player)
    invoice = TournamentPostpaymentInvoice.objects.create(
        tournament=tournament,
        user=player.user,
        amount=700,
        due_at=timezone.now() + timedelta(hours=1),
    )
    data = {"type": "tournament", "id": tournament.pk, "invoice": invoice.pk}
    assert client.get(reverse("payment_preview"), data, secure=True).status_code == 302
    with patch("apps.payments.views.create_payment") as create:
        response = client.post(
            reverse("payment_process"),
            {**data, "amount": "0.01", "offer_accepted": "on"},
            secure=True,
        )
    assert response.status_code == 302
    create.assert_not_called()
    assert not PaymentRecord.objects.exists()
    invoice.refresh_from_db()
    assert invoice.status == "pending"


@override_settings(YOOKASSA_SHOP_ID="test-shop", YOOKASSA_SECRET_KEY="test-secret")
def test_postpayment_amount_snapshot_is_used_in_preview_gateway_and_history(
    client: Client, player: Player
) -> None:
    """Изменение цены и поддельная сумма POST не меняют ранее выставленный счёт."""
    tournament = _cup(
        allow_postpayment=True, postpayment_window_started_at=timezone.now()
    )
    tournament.participants.add(player)
    invoice = TournamentPostpaymentInvoice.objects.create(
        tournament=tournament,
        user=player.user,
        amount=700,
        due_at=timezone.now() + timedelta(hours=1),
    )
    tournament.entry_fee = Decimal("2700")
    tournament.save(update_fields=["entry_fee"])
    data = {"type": "tournament", "id": tournament.pk, "invoice": invoice.pk}
    preview = client.get(reverse("payment_preview"), data, secure=True)
    assert preview.status_code == 200
    assert preview.context["amount"] == Decimal("700.00")
    with patch(
        "apps.payments.views.create_payment",
        return_value=("snapshot-700", "https://pay.example/test"),
    ) as create:
        response = client.post(
            reverse("payment_process"),
            {**data, "amount": "0.01", "offer_accepted": "on"},
            secure=True,
        )
    assert response.status_code == 302
    assert create.call_args.kwargs["amount"] == "700.00"
    record = PaymentRecord.objects.get(yookassa_payment_id="snapshot-700")
    assert record.amount == Decimal("700.00")
    assert record.metadata["total_amount"] == "700.00"
    assert client.session["yookassa_pending"]["total_amount"] == "700.00"


def test_unavailable_club_gateway_does_not_leave_a_balance_reservation(
    client: Client, player: Player
) -> None:
    """Отсутствие реквизитов клуба не замораживает деньги участника."""
    club = make_club()
    member = ClubMember.objects.create(
        club=club, user=player.user, status=ClubMemberStatus.ACTIVE, balance=400
    )
    tournament = _cup(club=club)
    with patch("apps.payments.views.create_payment_with_credentials") as create:
        response = client.post(
            reverse("payment_process"),
            {"type": "tournament", "id": tournament.pk, "offer_accepted": "on"},
            secure=True,
        )
    assert response.status_code == 302
    create.assert_not_called()
    member.refresh_from_db()
    assert member.balance == Decimal("400.00")
    assert not member.balance_transactions.filter(status="pending").exists()
    assert not PaymentRecord.objects.exists()


def test_successful_online_payment_confirms_only_its_balance_reservation(
    player: Player,
) -> None:
    """Успех шлюза подтверждает резерв ровно один раз и не списывает баланс повторно."""
    club = make_club()
    member = ClubMember.objects.create(
        club=club, user=player.user, status=ClubMemberStatus.ACTIVE, balance=600
    )
    tournament = _cup(club=club)
    reserved = reserve_member_balance(
        member,
        400,
        source=ClubMemberBalanceTransaction.Source.TOURNAMENT_PAYMENT,
        description="Взнос",
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
    with mute_payment_side_effects():
        finalize_successful_payment(record)
        finalize_successful_payment(PaymentRecord.objects.get(pk=record.pk))
    reserved.refresh_from_db()
    member.refresh_from_db()
    assert reserved.status == "completed"
    assert reserved.completed_at is not None
    assert member.balance == Decimal("200.00")
    assert get_online_entry_payment_amount(tournament, player.user) == Decimal(
        "1000.00"
    )


def test_online_callback_never_confirms_another_members_reservation(
    player: Player,
) -> None:
    """Несогласованные финансовые ссылки не дают подтвердить чужое списание."""
    club = make_club()
    other = _ready_player("foreign-balance")
    member = ClubMember.objects.create(
        club=club, user=other.user, status=ClubMemberStatus.ACTIVE, balance=400
    )
    tournament = _cup(club=club)
    reserved = reserve_member_balance(
        member,
        400,
        source=ClubMemberBalanceTransaction.Source.TOURNAMENT_PAYMENT,
        description="Чужой взнос",
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
    with pytest.raises(ValueError, match="резерв"):
        finalize_successful_payment(record)
    reserved.refresh_from_db()
    record.refresh_from_db()
    assert reserved.status == "pending"
    assert not tournament.entry_payments.exists()
    assert not record.metadata.get("_tournament_entry_finalized_at")


def test_returned_paid_invoice_is_not_shown_as_current_payment(player: Player) -> None:
    """Статус участника отличает завершённый возврат от сохранённого оплаченного счёта."""
    club = make_club()
    member = ClubMember.objects.create(club=club, user=player.user, status="active")
    tournament = _cup(club=club, allow_postpayment=True)
    tournament.participants.add(player)
    received_at = timezone.now() - timedelta(hours=1)
    invoice = TournamentPostpaymentInvoice.objects.create(
        tournament=tournament,
        user=player.user,
        amount=700,
        due_at=received_at + timedelta(hours=12),
        status="paid",
        paid_at=received_at,
    )
    before = tournament.postpayment_invoices.values().get()
    ClubMemberBalanceTransaction.objects.create(
        member=member,
        club=club,
        amount=700,
        source="tournament_refund",
        direction="credit",
        status="completed",
        completed_at=timezone.now(),
        reference=f"tournament:{tournament.pk}",
    )
    row = build_participant_payment_statuses(tournament)[0]
    assert row.status == "Взнос возвращён (₽)"
    assert row.status_tone != "success"
    assert "700" in row.details
    assert tournament.postpayment_invoices.values().get() == before
    assert invoice.status == "paid"


def test_club_online_refund_keeps_history_and_requires_a_new_payment(
    client: Client, player: Player
) -> None:
    """Возвращённый взнос остаётся в истории, но не даёт зарегистрироваться бесплатно."""
    club = make_club()
    member = ClubMember.objects.create(
        club=club, user=player.user, status=ClubMemberStatus.ACTIVE
    )
    tournament = _cup(club=club)
    record = make_payment_record(
        player.user, payment_type="tournament", item_id=str(tournament.pk), amount=700
    )
    with mute_payment_side_effects():
        finalize_successful_payment(record)
    payment = tournament.entry_payments.get(user=player.user)
    first_paid_at = payment.paid_at
    tournament.entry_fee = Decimal("2300")
    tournament.save(update_fields=["entry_fee"])
    cancel_url = reverse(
        "clubs:my_tournament_cancel", kwargs={"tournament_slug": tournament.slug}
    )
    assert client.post(cancel_url, secure=True).status_code == 302
    client.post(cancel_url, secure=True)
    member.refresh_from_db()
    payment.refresh_from_db()
    assert member.balance == Decimal("700.00")
    assert payment.paid_at == first_paid_at
    assert has_received_entry_payment(tournament, player.user) is False
    assert get_online_entry_payment_amount(tournament, player.user) is None
    assert "paid" not in {
        option.value
        for option in get_registration_payment_options(tournament, player.user)
    }
    assert _register(client, tournament).status_code == 302
    assert tournament.onsite_payments.get(status="expected").amount == Decimal(
        "2300.00"
    )
    client.post(cancel_url, secure=True)
    member.refresh_from_db()
    assert member.balance == Decimal("700.00")
    second = make_payment_record(
        player.user,
        yookassa_payment_id="second-entry",
        payment_type="tournament",
        item_id=str(tournament.pk),
        amount=550,
    )
    with mute_payment_side_effects():
        finalize_successful_payment(second)
        finalize_successful_payment(PaymentRecord.objects.get(pk=record.pk))
    assert has_received_entry_payment(tournament, player.user) is True
    assert get_online_entry_payment_amount(tournament, player.user) == Decimal("550.00")
    client.post(cancel_url, secure=True)
    member.refresh_from_db()
    assert member.balance == Decimal("1250.00")
    assert member.balance_transactions.filter(source="tournament_refund").count() == 2
    assert tournament.entry_payments.count() == 1


@pytest.mark.parametrize(
    "irrelevant", ["other_club", "other_tournament", "pending", "debit", "manual"]
)
def test_unrelated_balance_credit_does_not_invalidate_tournament_payment(
    player: Player, irrelevant: str
) -> None:
    """Оплату отменяет только выполненный возврат тому же игроку в том же турнире клуба."""
    club = make_club()
    member = ClubMember.objects.create(club=club, user=player.user, status="active")
    tournament = _cup(club=club)
    TournamentEntryPayment.objects.create(tournament=tournament, user=player.user)
    if irrelevant == "other_club":
        club = make_club(slug="other-refund-club")
        member = ClubMember.objects.create(club=club, user=player.user, status="active")
    ClubMemberBalanceTransaction.objects.create(
        club=club,
        member=member,
        amount=700,
        direction="debit" if irrelevant == "debit" else "credit",
        source="manual" if irrelevant == "manual" else "tournament_refund",
        status="pending" if irrelevant == "pending" else "completed",
        reference=f"tournament:{tournament.pk + 1 if irrelevant == 'other_tournament' else tournament.pk}",
    )
    assert has_received_entry_payment(tournament, player.user) is True


@pytest.mark.parametrize("first_method", ["covered", "onsite"])
def test_doubles_failure_after_first_write_rolls_back_real_usage_and_expectation(
    player: Player, first_method: str
) -> None:
    """Ошибка после первого списания откатывает тариф, ожидание взноса и всю команду."""
    club = make_club(use_player_plans=True)
    member = ClubMember.objects.create(club=club, user=player.user, status="active")
    plan = ClubPlayerPlan.objects.create(
        club=club, name="Откат", monthly_fee=1000, max_tournaments_per_month=4
    )
    assignment = ClubMemberPlan.objects.create(
        club_member=member, plan=plan, status="active"
    )
    partner = _ready_player("actual-rollback")
    ClubMember.objects.create(club=club, user=partner.user, status="active")
    tournament = _cup(club=club, variant="doubles", max_teams=16)

    def fail_after_first_write(
        current: Tournament, user: User, method: RegistrationPaymentMethod
    ) -> None:
        """Убедиться в первой записи перед отказом второго участника."""
        if user.pk == partner.user_id:
            if first_method == "covered":
                assert member.plan_slot_usages.get().tournaments_used == 1
                assert current.registration_coverages.filter(user=player.user).exists()
            else:
                assert current.onsite_payments.filter(
                    user=player.user, status="expected"
                ).exists()
            raise RegistrationPaymentError("Отказ после первой записи")
        apply_registration_payment(current, user, method)

    with (
        patch(
            "apps.tournaments.views.apply_registration_payment",
            side_effect=fail_after_first_write,
        ),
        pytest.raises(RegistrationPaymentError, match="первой записи"),
    ):
        _save_tournament_registration(
            tournament,
            player,
            first_method,
            partner=partner,
            partner_payment_method="onsite",
        )
    assignment.refresh_from_db()
    assert assignment.bonus_tournaments_balance == 0
    assert not member.plan_slot_usages.exists()
    assert not tournament.registration_coverages.exists()
    assert not tournament.onsite_payments.exists()
    assert not tournament.teams.exists()


def test_callback_side_effects_wait_for_commit_and_do_not_undo_payment(
    player: Player,
) -> None:
    """Ошибка рассылки или автозапуска после commit не отменяет сохранённый взнос."""
    tournament = _cup(
        allow_postpayment=True, postpayment_window_started_at=timezone.now()
    )
    tournament.participants.add(player)
    record = make_payment_record(
        player.user, payment_type="tournament", item_id=str(tournament.pk)
    )
    with (
        patch(
            "apps.core.telegram_notify.notify_tournament_entry_payment",
            side_effect=RuntimeError("Нет связи"),
        ) as notify,
        patch(
            "apps.core.email_service.send_tournament_entry_receipt_email",
            side_effect=RuntimeError("Нет почты"),
        ) as email,
        patch(
            "apps.payments.views.finalize_postpayment_window",
            side_effect=RuntimeError("Сетка недоступна"),
        ) as progress,
    ):
        with TestCase.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                finalize_successful_payment(record)
                notify.assert_not_called()
                email.assert_not_called()
                progress.assert_not_called()
            notify.assert_not_called()
        assert len(callbacks) == 2
        notify.assert_called_once()
        email.assert_called_once()
        progress.assert_called_once()
        finalize_successful_payment(PaymentRecord.objects.get(pk=record.pk))
        notify.assert_called_once()
    record.refresh_from_db()
    assert record.metadata.get("_tournament_entry_finalized_at")
    assert tournament.entry_payments.filter(user=player.user).count() == 1


def test_callback_transaction_failure_can_be_retried_without_partial_financial_state(
    client: Client, player: Player
) -> None:
    """Падение финализации откатывает отметку и замену оплаты, сохраняя успех шлюза."""
    tournament = _cup()
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    record = make_payment_record(
        player.user, payment_type="tournament", item_id=str(tournament.pk)
    )
    with (
        patch.object(PaymentRecord, "save", side_effect=RuntimeError("БД недоступна")),
        pytest.raises(RuntimeError, match="БД недоступна"),
    ):
        finalize_successful_payment(record)
    record.refresh_from_db()
    payment.refresh_from_db()
    assert record.status == "succeeded"
    assert not record.metadata.get("_tournament_entry_finalized_at")
    assert payment.status == "expected"
    assert not tournament.entry_payments.exists()
    with mute_payment_side_effects():
        finalize_successful_payment(PaymentRecord.objects.get(pk=record.pk))
    payment.refresh_from_db()
    record.refresh_from_db()
    assert record.metadata.get("_tournament_entry_finalized_at")
    assert payment.status == "replaced"
    assert tournament.entry_payments.count() == 1


def test_admin_onsite_receipt_is_post_csrf_protected_and_idempotent(
    client: Client, player: Player
) -> None:
    """Django admin требует явного получения, а просмотр и повторы историю не меняют."""
    tournament = _cup()
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    manager = make_user(
        email="receipt-superuser@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(manager)
    url = reverse(
        "admin:tournaments_tournamentonsitepayment_confirm_receipt", args=[payment.pk]
    )
    card = reverse(
        "admin:tournaments_tournamentonsitepayment_change", args=[payment.pk]
    )
    preview = client.get(card, secure=True)
    assert preview.status_code == 200
    assert "Подтвердить получение" in preview.content.decode()
    payment.refresh_from_db()
    assert payment.status == "expected"
    assert client.get(url, secure=True).status_code == 405
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(manager)
    assert (
        protected.post(url, secure=True, HTTP_ORIGIN="https://testserver").status_code
        == 403
    )
    page = protected.get(card, secure=True)
    token = protected.cookies["csrftoken"].value
    assert page.status_code == 200
    assert (
        protected.post(
            url,
            {"csrfmiddlewaretoken": token},
            secure=True,
            HTTP_ORIGIN="https://foreign.test",
        ).status_code
        == 403
    )
    payment.refresh_from_db()
    assert payment.status == "expected"
    assert not LogEntry.objects.filter(object_id=str(payment.pk)).exists()
    assert (
        protected.post(
            url,
            {"csrfmiddlewaretoken": token},
            secure=True,
            HTTP_ORIGIN="https://testserver",
        ).status_code
        == 302
    )
    payment.refresh_from_db()
    received_at = payment.received_at
    assert payment.status == "received"
    assert payment.received_by_id == manager.pk
    assert received_at is not None
    assert (
        protected.post(
            url,
            {"csrfmiddlewaretoken": token},
            secure=True,
            HTTP_ORIGIN="https://testserver",
        ).status_code
        == 302
    )
    payment.refresh_from_db()
    assert payment.received_at == received_at
    assert (
        LogEntry.objects.filter(
            object_id=str(payment.pk), change_message__contains="Получение"
        ).count()
        == 1
    )


def test_admin_receipt_requires_payment_and_tournament_change_permissions(
    client: Client, player: Player
) -> None:
    """Статус staff или право только на финансовую карточку не даёт подтвердить взнос."""
    tournament = _cup()
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    manager = make_user(email="limited-receipt@test.local", is_staff=True)
    client.force_login(manager)
    url = reverse(
        "admin:tournaments_tournamentonsitepayment_confirm_receipt", args=[payment.pk]
    )
    assert client.post(url, secure=True).status_code == 403
    manager.user_permissions.add(
        Permission.objects.get(codename="change_tournamentonsitepayment")
    )
    assert client.post(url, secure=True).status_code == 403
    manager.user_permissions.add(Permission.objects.get(codename="change_tournament"))
    assert client.post(url, secure=True).status_code == 302
    payment.refresh_from_db()
    assert payment.status == "received"


def test_admin_history_fields_cannot_be_overwritten_or_deleted(
    client: Client, player: Player
) -> None:
    """Даже поддельный POST суперпользователя не подменяет сумму и историю получения."""
    tournament = _cup()
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    manager = make_user(
        email="history-superuser@test.local", is_staff=True, is_superuser=True
    )
    confirm_onsite_payment(tournament, payment.pk, received_by=manager)
    payment.refresh_from_db()
    before = (
        payment.amount,
        payment.status,
        payment.received_at,
        payment.received_by_id,
    )
    client.force_login(manager)
    url = reverse("admin:tournaments_tournamentonsitepayment_change", args=[payment.pk])
    response = client.post(
        url,
        {"amount": "0", "status": "cancelled", "received_at": "", "received_by": ""},
        secure=True,
    )
    assert response.status_code == 302
    payment.refresh_from_db()
    assert (
        payment.amount,
        payment.status,
        payment.received_at,
        payment.received_by_id,
    ) == before
    delete_url = reverse(
        "admin:tournaments_tournamentonsitepayment_delete", args=[payment.pk]
    )
    assert client.post(delete_url, {"post": "yes"}, secure=True).status_code == 403
    assert TournamentOnsitePayment.objects.filter(pk=payment.pk).exists()


@pytest.mark.parametrize("model_name", ["tournament", "tvdtournament"])
def test_admin_tournament_retains_links_to_closed_onsite_history(
    client: Client, player: Player, model_name: str
) -> None:
    """История доступна из обоих разделов турниров даже после удаления участника."""
    tournament = _cup(format="weekend_day")
    _register(client, tournament)
    payment = tournament.onsite_payments.get()
    payment.status = "cancelled"
    payment.closed_reason = "registration_removed"
    payment.closed_at = timezone.now()
    payment.save(update_fields=["status", "closed_reason", "closed_at"])
    tournament.participants.remove(player)
    manager = make_user(
        email="linked-history@test.local", is_staff=True, is_superuser=True
    )
    client.force_login(manager)
    response = client.get(
        reverse(f"admin:tournaments_{model_name}_change", args=[tournament.pk]),
        secure=True,
    )
    assert response.status_code == 200
    card = reverse(
        "admin:tournaments_tournamentonsitepayment_change", args=[payment.pk]
    )
    assert card in response.content.decode()
    assert "Ожидание отменено" in response.content.decode()
