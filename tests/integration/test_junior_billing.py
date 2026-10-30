"""Платёжные повторы, поздняя оплата и возврат исходному плательщику."""

from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from apps.juniors.billing import begin_payment, execute_refund, reconcile_payment
from apps.juniors.competition import register
from apps.juniors.models import JuniorEntry, JuniorPayment
from tests.integration.test_junior_competition import participant, setup_tournament

pytestmark = pytest.mark.django_db


def provider_details(payment):
    return {
        "id": payment.provider_id,
        "status": "succeeded",
        "paid": True,
        "amount": {"value": "500.00", "currency": "RUB"},
        "metadata": {"junior_payment_id": str(payment.pk)},
    }


@override_settings(JUNIOR_PAYMENTS_ENABLED=True)
def test_payment_is_idempotent_and_confirms_only_its_child() -> None:
    _, tournament, release = setup_tournament(fee="500")
    user, kid = participant(1, release)
    entry = register(
        actor=user, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )
    with patch(
        "apps.juniors.billing.create_payment",
        return_value=("provider-1", "https://yookassa.ru/pay/1"),
    ) as create:
        payment = begin_payment(
            actor=user,
            entry_id=entry.pk,
            return_url="https://tennisfan.ru/kids/payments/",
        )
        assert (
            begin_payment(
                actor=user,
                entry_id=entry.pk,
                return_url="https://tennisfan.ru/kids/payments/",
            ).pk
            == payment.pk
        )
        assert create.call_count == 1
        assert create.call_args.kwargs["idempotency_key"] == str(payment.pk)
        assert "ЗакрытаяФамилия" not in str(create.call_args)
    with patch(
        "apps.juniors.billing.get_payment_details",
        return_value=provider_details(payment),
    ):
        assert reconcile_payment(payment.pk).status == "paid"
        assert reconcile_payment(payment.pk).status == "paid"
    entry.refresh_from_db()
    assert entry.status == "confirmed"
    assert JuniorPayment.objects.count() == 1


@override_settings(JUNIOR_PAYMENTS_ENABLED=True)
def test_late_payment_requires_refund_and_does_not_take_place() -> None:
    organizer, tournament, release = setup_tournament(fee="500")
    user, kid = participant(1, release)
    entry = register(
        actor=user, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )
    past = timezone.now() - timedelta(minutes=1)
    JuniorEntry.objects.filter(pk=entry.pk).update(reserved_until=past)
    payment = JuniorPayment.objects.create(
        entry=entry,
        payer=user,
        amount=500,
        reservation=past,
        provider_id="provider-late",
        status="pending",
    )
    with patch(
        "apps.juniors.billing.get_payment_details",
        return_value=provider_details(payment),
    ):
        assert reconcile_payment(payment.pk).status == "refund_pending"
    entry.refresh_from_db()
    assert entry.status != "confirmed"
    response = Mock()
    response.json.return_value = {
        "id": "refund-1",
        "payment_id": payment.provider_id,
        "amount": {"value": "500.00", "currency": "RUB"},
        "status": "succeeded",
    }
    with (
        patch("apps.juniors.billing._create_auth", return_value=("test", "test")),
        patch("apps.juniors.billing.requests.post", return_value=response) as post,
    ):
        refund = execute_refund(actor=organizer, refund_id=payment.refund.pk)
        assert refund.status == "refunded"
        assert post.call_args.kwargs["json"]["payment_id"] == payment.provider_id
        execute_refund(actor=organizer, refund_id=refund.pk)
        assert post.call_count == 1


def test_payment_does_not_trust_wrong_amount() -> None:
    _, tournament, release = setup_tournament(fee="500")
    user, kid = participant(1, release)
    entry = register(
        actor=user, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )
    payment = JuniorPayment.objects.create(
        entry=entry,
        payer=user,
        amount=500,
        reservation=entry.reserved_until,
        provider_id="wrong",
    )
    details = provider_details(payment)
    details["amount"]["value"] = "1.00"
    with (
        patch("apps.juniors.billing.get_payment_details", return_value=details),
        pytest.raises(ValidationError),
    ):
        reconcile_payment(payment.pk)
