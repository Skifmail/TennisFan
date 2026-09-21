"""Интеграционные тесты: создание подписки клуба из Django admin."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.sites import site
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clubs.admin import ClubSubscriptionAdmin, ClubSubscriptionInline
from apps.clubs.models import (
    Club,
    ClubPlan,
    ClubSubscription,
    ClubSubscriptionPeriod,
    ClubSubscriptionStatus,
)
from apps.users.models import User
from tests.support.factories import make_club


class ClubSubscriptionAdminCreateTestCase(TestCase):
    """Создание подписки в админке не должно требовать ручного ``started_at``."""

    def setUp(self) -> None:
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email="club-sub-admin@test.local",
            password="testpass123",
        )
        self.club = make_club(
            name="Клуб без подписки",
            slug="club-without-sub",
        )
        self.client.force_login(self.admin_user)

    def test_create_without_started_at_fills_current_time(self) -> None:
        before = timezone.now()
        ends_at = timezone.now() + timedelta(days=365)

        subscription = ClubSubscription.objects.create(
            club=self.club,
            plan=ClubPlan.PRO,
            period=ClubSubscriptionPeriod.YEARLY,
            price=Decimal("100.00"),
            ends_at=ends_at,
            auto_renew=True,
            payment_ref="",
            status=ClubSubscriptionStatus.ACTIVE,
        )

        self.assertIsNotNone(subscription.started_at)
        self.assertGreaterEqual(subscription.started_at, before)
        self.assertLessEqual(subscription.started_at, timezone.now())

    def test_admin_add_form_saves_without_started_at(self) -> None:
        """Поле ``started_at`` readonly: форма не передаёт его, как в проде."""
        factory = RequestFactory()
        request = factory.post("/admin/clubs/clubsubscription/add/")
        request.user = self.admin_user
        ends_at = timezone.now() + timedelta(days=365 * 4)

        admin = ClubSubscriptionAdmin(ClubSubscription, site)
        form_class = admin.get_form(request)
        ends_local = timezone.localtime(ends_at)
        form = form_class(
            data={
                "club": str(self.club.pk),
                "plan": ClubPlan.PRO,
                "period": ClubSubscriptionPeriod.YEARLY,
                "price": "100.00",
                "ends_at_0": ends_local.date().isoformat(),
                "ends_at_1": ends_local.strftime("%H:%M:%S"),
                "auto_renew": "on",
                "payment_provider": "",
                "payment_ref": "",
                "status": ClubSubscriptionStatus.ACTIVE,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()

        saved.refresh_from_db()
        self.assertIsNotNone(saved.started_at)
        self.assertEqual(saved.club_id, self.club.pk)
        self.assertEqual(saved.plan, ClubPlan.PRO)
        self.assertEqual(saved.status, ClubSubscriptionStatus.ACTIVE)

    def test_admin_add_view_creates_subscription_without_started_at(self) -> None:
        ends_local = timezone.localtime(timezone.now() + timedelta(days=365 * 4))
        response = self.client.post(
            reverse("admin:clubs_clubsubscription_add"),
            {
                "club": self.club.pk,
                "plan": ClubPlan.PRO,
                "period": ClubSubscriptionPeriod.YEARLY,
                "price": "100.00",
                "ends_at_0": ends_local.date().isoformat(),
                "ends_at_1": ends_local.strftime("%H:%M:%S"),
                "auto_renew": "on",
                "payment_provider": "",
                "payment_ref": "",
                "status": ClubSubscriptionStatus.ACTIVE,
                "_save": "Save",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302, response.content.decode())
        subscription = ClubSubscription.objects.get(club=self.club)
        self.assertIsNotNone(subscription.started_at)
        self.assertEqual(subscription.plan, ClubPlan.PRO)
        self.assertEqual(subscription.period, ClubSubscriptionPeriod.YEARLY)
        self.assertEqual(subscription.price, Decimal("100.00"))
        self.assertEqual(subscription.status, ClubSubscriptionStatus.ACTIVE)
        self.assertTrue(subscription.auto_renew)

    def test_club_admin_inline_saves_without_started_at(self) -> None:
        """Inline на карточке клуба тоже не передаёт readonly ``started_at``."""
        factory = RequestFactory()
        request = factory.post(f"/admin/clubs/club/{self.club.pk}/change/")
        request.user = self.admin_user
        ends_local = timezone.localtime(timezone.now() + timedelta(days=365 * 4))

        inline = ClubSubscriptionInline(Club, site)
        formset_class = inline.get_formset(request, obj=self.club)
        prefix = formset_class.get_default_prefix()
        formset = formset_class(
            data={
                f"{prefix}-TOTAL_FORMS": "1",
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
                f"{prefix}-0-plan": ClubPlan.PRO,
                f"{prefix}-0-period": ClubSubscriptionPeriod.YEARLY,
                f"{prefix}-0-price": "100.00",
                f"{prefix}-0-ends_at_0": ends_local.date().isoformat(),
                f"{prefix}-0-ends_at_1": ends_local.strftime("%H:%M:%S"),
                f"{prefix}-0-status": ClubSubscriptionStatus.ACTIVE,
            },
            instance=self.club,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        subscription = ClubSubscription.objects.get(club=self.club)
        self.assertIsNotNone(subscription.started_at)
        self.assertEqual(subscription.plan, ClubPlan.PRO)
        self.assertEqual(subscription.status, ClubSubscriptionStatus.ACTIVE)
