"""Юнит-тесты назначения администратора клуба."""

from __future__ import annotations

from django.test import TestCase

from apps.clubs.models import (
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubRating,
)
from apps.clubs.services import assign_club_administrator
from tests.support.factories import make_club, make_user


class AssignClubAdministratorTestCase(TestCase):
    """Сервис должен создавать, повышать и активировать администратора клуба."""

    def setUp(self) -> None:
        self.club = make_club(name="Клуб без админа", slug="club-no-admin")
        self.user = make_user(email="future-admin@test.local")

    def test_creates_active_admin_and_rating(self) -> None:
        member, kind = assign_club_administrator(self.club, self.user)

        self.assertEqual(kind, "created")
        self.assertEqual(member.club_id, self.club.pk)
        self.assertEqual(member.user_id, self.user.pk)
        self.assertEqual(member.role, ClubMemberRole.ADMIN)
        self.assertEqual(member.status, ClubMemberStatus.ACTIVE)
        self.assertIsNotNone(member.joined_at)
        self.assertTrue(
            ClubRating.objects.filter(club=self.club, member=member).exists()
        )

    def test_promotes_existing_player(self) -> None:
        ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )

        member, kind = assign_club_administrator(self.club, self.user)

        self.assertEqual(kind, "promoted")
        member.refresh_from_db()
        self.assertEqual(member.role, ClubMemberRole.ADMIN)
        self.assertEqual(member.status, ClubMemberStatus.ACTIVE)
        self.assertEqual(
            ClubMember.objects.filter(club=self.club, user=self.user).count(),
            1,
        )

    def test_reactivates_removed_admin(self) -> None:
        ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.REMOVED,
        )

        member, kind = assign_club_administrator(self.club, self.user)

        self.assertEqual(kind, "promoted")
        member.refresh_from_db()
        self.assertEqual(member.role, ClubMemberRole.ADMIN)
        self.assertEqual(member.status, ClubMemberStatus.ACTIVE)
        self.assertIsNotNone(member.joined_at)

    def test_already_admin_is_idempotent(self) -> None:
        existing = ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )

        member, kind = assign_club_administrator(self.club, self.user)

        self.assertEqual(kind, "already_admin")
        self.assertEqual(member.pk, existing.pk)
        self.assertEqual(
            ClubMember.objects.filter(club=self.club, user=self.user).count(),
            1,
        )

    def test_closes_pending_join_request(self) -> None:
        ClubJoinRequest.objects.create(
            club=self.club,
            user=self.user,
            status=ClubJoinRequestStatus.PENDING,
        )

        assign_club_administrator(self.club, self.user, reviewed_by=self.user)

        join_request = ClubJoinRequest.objects.get(club=self.club, user=self.user)
        self.assertEqual(join_request.status, ClubJoinRequestStatus.APPROVED)
        self.assertEqual(join_request.reviewed_by_id, self.user.pk)
        self.assertIsNotNone(join_request.reviewed_at)
        member = ClubMember.objects.get(club=self.club, user=self.user)
        self.assertEqual(member.role, ClubMemberRole.ADMIN)
