"""Интеграционные тесты: назначение администратора клуба из Django admin."""

from __future__ import annotations

from django.contrib.admin.sites import site
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from apps.clubs.admin import ClubAdmin
from apps.clubs.models import (
    Club,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubRating,
    ClubStatus,
    PlatformAuditAction,
    PlatformAuditLog,
)
from apps.users.models import User
from tests.support.factories import make_club, make_user


def _attach_messages(request) -> None:
    """Подключить storage сообщений для ``ModelAdmin.message_user``."""
    request.session = "session"
    request._messages = FallbackStorage(request)


class ClubAdminAssignAdministratorTestCase(TestCase):
    """На карточке клуба можно выбрать пользователя и сделать его админом."""

    def setUp(self) -> None:
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email="platform-admin@test.local",
            password="testpass123",
        )
        self.club = make_club(
            name="Клуб без администратора",
            slug="club-needs-admin",
        )
        self.candidate = make_user(email="club-owner@test.local")
        self.client.force_login(self.admin_user)

    def test_change_page_contains_new_admin_field(self) -> None:
        response = self.client.get(
            reverse("admin:clubs_club_change", args=[self.club.pk]),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Добавить администратора")
        self.assertContains(response, "new_admin")

    def test_save_model_creates_club_admin(self) -> None:
        factory = RequestFactory()
        request = factory.post(f"/admin/clubs/club/{self.club.pk}/change/")
        request.user = self.admin_user
        _attach_messages(request)

        model_admin = ClubAdmin(Club, site)
        form_class = model_admin.get_form(request, obj=self.club)
        form = form_class(
            data={
                "name": self.club.name,
                "slug": self.club.slug,
                "city": self.club.city,
                "address": self.club.address,
                "email": self.club.email,
                "phone": self.club.phone,
                "admin_name": self.club.admin_name,
                "description": self.club.description,
                "is_public": "on",
                "use_player_plans": "on",
                "status": ClubStatus.ACTIVE,
                "new_admin": str(self.candidate.pk),
            },
            instance=self.club,
        )

        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        model_admin.save_model(request, obj, form, change=True)

        member = ClubMember.objects.get(club=self.club, user=self.candidate)
        self.assertEqual(member.role, ClubMemberRole.ADMIN)
        self.assertEqual(member.status, ClubMemberStatus.ACTIVE)
        self.assertTrue(
            ClubRating.objects.filter(club=self.club, member=member).exists()
        )
        self.assertTrue(
            PlatformAuditLog.objects.filter(
                club=self.club,
                action=PlatformAuditAction.CLUB_ADMIN_ASSIGNED,
                actor=self.admin_user,
            ).exists()
        )

    def test_save_model_promotes_existing_player(self) -> None:
        ClubMember.objects.create(
            club=self.club,
            user=self.candidate,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        factory = RequestFactory()
        request = factory.post(f"/admin/clubs/club/{self.club.pk}/change/")
        request.user = self.admin_user
        _attach_messages(request)

        model_admin = ClubAdmin(Club, site)
        form_class = model_admin.get_form(request, obj=self.club)
        form = form_class(
            data={
                "name": self.club.name,
                "slug": self.club.slug,
                "city": self.club.city,
                "address": self.club.address,
                "email": self.club.email,
                "phone": self.club.phone,
                "admin_name": self.club.admin_name,
                "description": self.club.description,
                "is_public": "on",
                "use_player_plans": "on",
                "status": ClubStatus.ACTIVE,
                "new_admin": str(self.candidate.pk),
            },
            instance=self.club,
        )

        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        model_admin.save_model(request, obj, form, change=True)

        member = ClubMember.objects.get(club=self.club, user=self.candidate)
        self.assertEqual(member.role, ClubMemberRole.ADMIN)
        self.assertEqual(
            ClubMember.objects.filter(club=self.club, user=self.candidate).count(),
            1,
        )

    def test_save_without_new_admin_does_not_create_member(self) -> None:
        factory = RequestFactory()
        request = factory.post(f"/admin/clubs/club/{self.club.pk}/change/")
        request.user = self.admin_user
        _attach_messages(request)

        model_admin = ClubAdmin(Club, site)
        form_class = model_admin.get_form(request, obj=self.club)
        form = form_class(
            data={
                "name": self.club.name,
                "slug": self.club.slug,
                "city": self.club.city,
                "address": self.club.address,
                "email": self.club.email,
                "phone": self.club.phone,
                "admin_name": self.club.admin_name,
                "description": self.club.description,
                "is_public": "on",
                "use_player_plans": "on",
                "status": ClubStatus.ACTIVE,
            },
            instance=self.club,
        )

        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        model_admin.save_model(request, obj, form, change=True)

        self.assertFalse(ClubMember.objects.filter(club=self.club).exists())
