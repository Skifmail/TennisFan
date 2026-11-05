"""Заявка, платёж и возврат через страницы; шлюз полностью подменён."""

from unittest.mock import Mock, patch

import pytest
from django.urls import reverse

from apps.juniors.models import JuniorPayment, JuniorRefund
from tests.integration.test_junior_billing import provider_details
from tests.integration.test_junior_competition import participant, setup_tournament
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("cancellation", ["parent", "organizer"])
def test_payment_and_refund_through_views(client, settings, cancellation):
    settings.SECURE_SSL_REDIRECT = False
    settings.JUNIOR_LEAGUE_ENABLED = True
    settings.JUNIOR_PAYMENTS_ENABLED = True
    organizer, tournament, release = setup_tournament(fee="500")
    parent, child = participant(1, release)
    client.force_login(parent)
    assert (
        client.post(
            reverse("juniors:tournament", args=[tournament.pk]),
            {"child": child.pk, "regulation_version": 1, "accepted": "on"},
        ).status_code
        == 302
    )
    entry = tournament.entries.get()
    assert entry.status == "payment"
    with patch(
        "apps.juniors.billing.create_payment",
        return_value=("provider-journey", "https://yookassa.ru/pay/test"),
    ) as create:
        for _ in range(2):
            response = client.post(reverse("juniors:pay", args=[entry.pk]))
            assert response.status_code == 302
            assert response.url == "https://yookassa.ru/pay/test"
        assert create.call_count == 1
    payment = JuniorPayment.objects.get(entry=entry)
    action_url = reverse("juniors:payment_action", args=[payment.pk])
    client.force_login(make_user(email="payment-stranger@kids.test"))
    assert client.post(action_url).status_code == 404
    client.force_login(parent)
    with patch(
        "apps.juniors.billing.get_payment_details",
        return_value=provider_details(payment),
    ):
        assert client.post(action_url).status_code == 302
    entry.refresh_from_db()
    assert entry.status == "confirmed"
    if cancellation == "parent":
        assert (
            client.post(reverse("juniors:withdraw", args=[entry.pk])).status_code == 302
        )
        assert (
            client.post(
                action_url, {"action": "refund", "reason": "Не сможем приехать"}
            ).status_code
            == 302
        )
    else:
        client.force_login(organizer)
        for _ in range(2):
            assert (
                client.post(
                    reverse("juniors:manage", args=[tournament.pk]),
                    {
                        "action": "cancel",
                        "reason": "Площадка закрыта",
                    },
                ).status_code
                == 302
            )
        tournament.refresh_from_db()
        assert tournament.status == "cancelled"
    refund = JuniorRefund.objects.get(payment=payment)
    assert JuniorRefund.objects.count() == 1
    assert payment.payer_id == parent.pk
    client.force_login(organizer)
    response = Mock()
    response.json.return_value = {
        "id": "refund-journey",
        "payment_id": payment.provider_id,
        "amount": {"value": "500.00", "currency": "RUB"},
        "status": "succeeded",
    }
    with (
        patch("apps.juniors.billing._create_auth", return_value=("test", "test")),
        patch("apps.juniors.billing.requests.post", return_value=response) as post,
    ):
        for _ in range(2):
            assert (
                client.post(
                    reverse("admin:juniors_juniorrefund_changelist"),
                    {
                        "action": "approve_refunds",
                        "_selected_action": str(refund.pk),
                    },
                ).status_code
                == 302
            )
        assert post.call_count == 1
        assert post.call_args.kwargs["json"]["payment_id"] == payment.provider_id
    refund.refresh_from_db()
    payment.refresh_from_db()
    assert refund.status == "refunded"
    assert payment.status == "refunded"
