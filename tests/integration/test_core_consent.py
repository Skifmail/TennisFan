"""Интеграционные тесты: фиксация согласий пользователя."""

from django.test import RequestFactory, TestCase

from apps.clubs.models import ClubLegalDocument
from apps.core.consent_utils import record_club_consent, record_platform_consent
from apps.core.models import UserConsent
from tests.support.factories import make_club, make_user


class RecordPlatformConsentTestCase(TestCase):
    """Согласие с документами платформы."""

    def setUp(self) -> None:
        self.user = make_user(email="consent-platform@test.local")
        self.factory = RequestFactory()

    def _request(self, **meta: str) -> object:
        request = self.factory.get("/")
        request.user = self.user
        request.META.update(meta)
        return request

    def test_creates_consent_with_forwarded_ip(self) -> None:
        request = self._request(HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1")

        consent, created = record_platform_consent(
            request,
            UserConsent.ConsentType.PLATFORM_OFFER,
            "v2026-01",
        )

        self.assertTrue(created)
        self.assertEqual(consent.user_id, self.user.pk)
        self.assertEqual(consent.document_version, "v2026-01")
        self.assertEqual(consent.ip_address, "203.0.113.9")
        self.assertIsNone(consent.club_id)

    def test_second_call_is_idempotent(self) -> None:
        request = self._request(REMOTE_ADDR="192.168.1.1")
        record_platform_consent(
            request,
            UserConsent.ConsentType.PRIVACY_POLICY,
            "1.0",
        )

        _, created = record_platform_consent(
            request,
            UserConsent.ConsentType.PRIVACY_POLICY,
            "1.0",
        )

        self.assertFalse(created)
        self.assertEqual(
            UserConsent.objects.filter(
                user=self.user,
                consent_type=UserConsent.ConsentType.PRIVACY_POLICY,
                document_version="1.0",
            ).count(),
            1,
        )


class RecordClubConsentTestCase(TestCase):
    """Согласие с офертой клуба."""

    def setUp(self) -> None:
        self.user = make_user(email="consent-club@test.local")
        self.club = make_club(name="Клуб согласий", slug="consent-club")
        self.factory = RequestFactory()

    def test_uses_club_legal_document_version(self) -> None:
        ClubLegalDocument.objects.create(
            club=self.club,
            title="Оферта",
            content="<p>Условия</p>",
            version="3.2",
            is_published=True,
        )
        request = self.factory.get("/")
        request.user = self.user
        request.META["REMOTE_ADDR"] = "10.20.30.40"

        consent, created = record_club_consent(request, self.club)

        self.assertTrue(created)
        self.assertEqual(consent.club_id, self.club.pk)
        self.assertEqual(consent.consent_type, UserConsent.ConsentType.CLUB_OFFER)
        self.assertEqual(consent.document_version, "3.2")

    def test_defaults_version_when_no_legal_document(self) -> None:
        request = self.factory.get("/")
        request.user = self.user

        consent, _ = record_club_consent(request, self.club)

        self.assertEqual(consent.document_version, "1.0")
