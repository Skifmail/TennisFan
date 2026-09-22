"""Юнит-тесты одобрения и отклонения заявок на вступление в клуб."""

from __future__ import annotations

from django.core import mail
from django.test import TestCase, override_settings

from apps.clubs.models import (
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubRating,
)
from apps.clubs.notifications import send_join_request_notification
from apps.clubs.services import approve_club_join_request, reject_club_join_request
from apps.users.models import Notification
from tests.support.factories import make_club, make_user


class ClubJoinRequestServiceTestCase(TestCase):
    """Сервис должен добавлять игрока в клуб и закрывать заявку."""

    def setUp(self) -> None:
        self.club = make_club(name="Клуб сервиса заявок", slug="join-service-club")
        self.applicant = make_user(
            email="join-service-player@test.local",
            first_name="Олег",
            last_name="Смирнов",
        )
        self.reviewer = make_user(email="join-service-admin@test.local")
        self.join_request = ClubJoinRequest.objects.create(
            club=self.club,
            user=self.applicant,
            status=ClubJoinRequestStatus.PENDING,
        )

    def test_approve_creates_member_rating_and_notification(self) -> None:
        member = approve_club_join_request(
            self.join_request,
            reviewed_by=self.reviewer,
        )

        self.join_request.refresh_from_db()
        self.assertEqual(self.join_request.status, ClubJoinRequestStatus.APPROVED)
        self.assertEqual(self.join_request.reviewed_by_id, self.reviewer.pk)
        self.assertIsNotNone(self.join_request.reviewed_at)
        self.assertEqual(member.role, ClubMemberRole.PLAYER)
        self.assertEqual(member.status, ClubMemberStatus.ACTIVE)
        self.assertTrue(
            ClubRating.objects.filter(club=self.club, member=member).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.applicant,
                message__contains="одобрена",
            ).exists()
        )

    def test_reject_closes_request_without_membership(self) -> None:
        reject_club_join_request(self.join_request, reviewed_by=self.reviewer)

        self.join_request.refresh_from_db()
        self.assertEqual(self.join_request.status, ClubJoinRequestStatus.REJECTED)
        self.assertEqual(self.join_request.reviewed_by_id, self.reviewer.pk)
        self.assertFalse(
            ClubMember.objects.filter(club=self.club, user=self.applicant).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.applicant,
                message__contains="отклонена",
            ).exists()
        )

    def test_second_approve_raises(self) -> None:
        approve_club_join_request(self.join_request, reviewed_by=self.reviewer)

        with self.assertRaises(ValueError):
            approve_club_join_request(self.join_request, reviewed_by=self.reviewer)

    def test_approve_does_not_demote_existing_admin(self) -> None:
        ClubMember.objects.create(
            club=self.club,
            user=self.applicant,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )

        member = approve_club_join_request(
            self.join_request,
            reviewed_by=self.reviewer,
        )

        self.assertEqual(member.role, ClubMemberRole.ADMIN)
        self.assertEqual(
            Notification.objects.filter(user=self.applicant).count(),
            0,
        )


@override_settings(
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
    ADMIN_NOTIFICATIONS_EMAIL="platform-admin@test.local",
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_ADMIN_CHAT_ID="",
    TELEGRAM_ADMIN_CHAT_IDS=[],
)
class ClubJoinRequestEmailNotificationTestCase(TestCase):
    """Новая заявка должна уходить письмом админу клуба и платформы."""

    def setUp(self) -> None:
        self.club = make_club(name="Клуб писем", slug="join-email-club")
        self.club_admin = make_user(
            email="club-admin@test.local",
            first_name="Ирина",
            last_name="Админова",
        )
        ClubMember.objects.create(
            club=self.club,
            user=self.club_admin,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        self.applicant = make_user(
            email="join-applicant@test.local",
            first_name="Павел",
            last_name="Заявкин",
        )

    def test_sends_email_to_club_admin_and_platform_admin(self) -> None:
        send_join_request_notification(
            self.club,
            self.applicant,
            invites_url="https://tennisfan.ru/club/join-email-club/invites/",
            platform_admin_url="https://tennisfan.ru/admin/clubs/clubjoinrequest/?status__exact=pending",
            comment="Хочу играть в турнирах",
        )

        recipients = {tuple(message.to) for message in mail.outbox}
        self.assertIn(("club-admin@test.local",), recipients)
        self.assertIn(("platform-admin@test.local",), recipients)
        club_mail = next(m for m in mail.outbox if m.to == ["club-admin@test.local"])
        self.assertIn("Новая заявка в клуб", club_mail.subject)
        self.assertIn("Павел Заявкин", club_mail.alternatives[0][0])
        self.assertIn("Открыть заявки", club_mail.alternatives[0][0])
