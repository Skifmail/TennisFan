"""Интеграционные тесты: заявки в клубы на панели платформы и в админке."""

from __future__ import annotations

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.clubs.models import (
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
)
from apps.users.models import User
from tests.support.factories import make_club, make_user


@override_settings(SECURE_SSL_REDIRECT=False)
class PlatformJoinRequestsDashboardTestCase(TestCase):
    """Панель платформы должна вести к конкретным заявкам на вступление."""

    def setUp(self) -> None:
        self.client = Client()
        self.staff = User.objects.create_user(
            email="platform-staff@test.local",
            password="testpass123",
            first_name="Леонид",
            last_name="Егоров",
            is_staff=True,
        )
        self.club = make_club(name="Клуб Заявок", slug="join-request-club")
        self.applicant = make_user(
            email="applicant-join@test.local",
            first_name="Анна",
            last_name="Иванова",
        )
        self.join_request = ClubJoinRequest.objects.create(
            club=self.club,
            user=self.applicant,
            status=ClubJoinRequestStatus.PENDING,
        )
        self.client.force_login(self.staff)

    def test_pending_join_request_is_clickable_and_points_to_admin(self) -> None:
        """Сводка и блок внимания ведут в админку заявок, а не в список клубов."""
        response = self.client.get(reverse("platform_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 заявка ожидает решения.")
        self.assertContains(response, "Анна Иванова")
        self.assertContains(response, "Клуб Заявок")
        self.assertContains(response, "подана")

        changelist_url = reverse("admin:clubs_clubjoinrequest_changelist")
        change_url = reverse(
            "admin:clubs_clubjoinrequest_change",
            args=[self.join_request.pk],
        )

        self.assertContains(response, changelist_url)
        self.assertContains(response, "status__exact=pending")
        self.assertContains(response, change_url)
        self.assertContains(response, 'class="club-dashboard-hero__summary-link"')
        self.assertContains(
            response,
            f'href="{changelist_url}?status__exact=pending"',
        )


class ClubJoinRequestAdminTestCase(TestCase):
    """Заявки на вступление должны быть видны и обрабатываемы в Django admin."""

    def setUp(self) -> None:
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email="join-admin@test.local",
            password="testpass123",
        )
        self.club = make_club(name="Админ-клуб заявок", slug="admin-join-club")
        self.applicant = make_user(
            email="visible-applicant@test.local",
            first_name="Павел",
            last_name="Сергеев",
        )
        self.join_request = ClubJoinRequest.objects.create(
            club=self.club,
            user=self.applicant,
            status=ClubJoinRequestStatus.PENDING,
        )
        self.client.force_login(self.admin_user)

    def test_changelist_shows_pending_join_request(self) -> None:
        response = self.client.get(
            reverse("admin:clubs_clubjoinrequest_changelist"),
            {"status__exact": ClubJoinRequestStatus.PENDING},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Админ-клуб заявок")
        self.assertContains(response, "visible-applicant@test.local")
        self.assertContains(response, "Павел Сергеев")

    def test_approve_action_adds_member(self) -> None:
        response = self.client.post(
            reverse("admin:clubs_clubjoinrequest_changelist"),
            {
                "action": "approve_selected",
                "_selected_action": [str(self.join_request.pk)],
            },
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.join_request.refresh_from_db()
        self.assertEqual(self.join_request.status, ClubJoinRequestStatus.APPROVED)

    def test_reject_action_closes_request_without_member(self) -> None:
        response = self.client.post(
            reverse("admin:clubs_clubjoinrequest_changelist"),
            {
                "action": "reject_selected",
                "_selected_action": [str(self.join_request.pk)],
            },
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.join_request.refresh_from_db()
        self.assertEqual(self.join_request.status, ClubJoinRequestStatus.REJECTED)
        self.assertFalse(
            ClubMember.objects.filter(
                club=self.club,
                user=self.applicant,
            ).exists()
        )
