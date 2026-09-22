"""Интеграция: режим «Старт после набора» для платформенных турниров."""

from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tournaments.models import (
    Tournament,
    TournamentAllowedCategory,
    TournamentFormat,
    TournamentStatus,
    TournamentVariant,
)
from apps.tournaments.start_after_fill import maybe_progress_start_after_fill
from apps.users.models import SkillLevel
from tests.support.factories import make_player


def _make_fill_tournament(
    *,
    slug: str,
    min_participants: int,
    max_participants: int,
) -> Tournament:
    """Создать платформенный турнир в режиме старта после набора."""
    tournament = Tournament.objects.create(
        name=f"Fill {slug}",
        slug=slug,
        city="Москва",
        format=TournamentFormat.SINGLE_ELIMINATION,
        variant=TournamentVariant.SINGLES,
        status=TournamentStatus.UPCOMING,
        entry_fee=500,
        start_after_fill=True,
        start_date=None,
        registration_deadline=None,
        min_participants=min_participants,
        max_participants=max_participants,
        bracket_generated=False,
        is_one_day=False,
    )
    TournamentAllowedCategory.objects.create(
        tournament=tournament, category=SkillLevel.AMATEUR
    )
    return tournament


@override_settings(
    SITE_URL="https://tennisfan.ru",
    LANGUAGE_CODE="ru",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ADMIN_NOTIFICATIONS_EMAIL="admin@test.local",
)
class StartAfterFillTestCase(TestCase):
    """Минимум → письмо админу; максимум → автостарт сетки."""

    def test_min_fill_notifies_admin_once(self) -> None:
        """При наборе минимума админ получает письмо; повторно не шлём."""
        tournament = _make_fill_tournament(
            slug="fill-min", min_participants=2, max_participants=4
        )
        p1 = make_player(email_suffix="fill1")
        p2 = make_player(email_suffix="fill2")

        with patch(
            "apps.core.telegram_notify.requests.post",
            side_effect=Exception("no-tg"),
        ):
            tournament.participants.add(p1)
            tournament.refresh_from_db()
            self.assertIsNone(tournament.min_fill_reached_notified_at)

            tournament.participants.add(p2)
            tournament.refresh_from_db()
            self.assertIsNotNone(tournament.min_fill_reached_notified_at)
            self.assertFalse(tournament.bracket_generated)
            self.assertGreaterEqual(len(mail.outbox), 1)
            combined = " ".join(m.subject + m.body for m in mail.outbox).lower()
            self.assertTrue(
                "минимальн" in combined or "набран" in combined,
                msg=combined[:500],
            )

            mail.outbox.clear()
            result = maybe_progress_start_after_fill(tournament)
            self.assertEqual(result, "waiting_max")
            self.assertEqual(len(mail.outbox), 0)

    def test_max_fill_auto_starts_bracket(self) -> None:
        """При наборе максимума формируется сетка и статус становится активным."""
        tournament = _make_fill_tournament(
            slug="fill-max", min_participants=2, max_participants=4
        )
        players = [make_player(email_suffix=f"fillmax{i}") for i in range(4)]

        with (
            patch(
                "apps.core.telegram_notify.requests.post",
                side_effect=Exception("no-tg"),
            ),
            patch("apps.telegram_bot.notifications.notify_bracket_formed"),
        ):
            for player in players[:3]:
                tournament.participants.add(player)
            tournament.refresh_from_db()
            self.assertFalse(tournament.bracket_generated)
            self.assertIsNotNone(tournament.min_fill_reached_notified_at)

            tournament.participants.add(players[3])
            tournament.refresh_from_db()
            self.assertTrue(tournament.bracket_generated)
            self.assertEqual(tournament.status, TournamentStatus.ACTIVE)
            self.assertEqual(tournament.start_date, timezone.localdate())

    def test_admin_form_start_after_fill_clears_dates(self) -> None:
        """Форма админки: галочка снимает обязательность дат и требует max."""
        from apps.tournaments.admin import TournamentAdminForm

        form = TournamentAdminForm(
            data={
                "name": "Fill Form",
                "slug": "fill-form",
                "city": "Москва",
                "format": TournamentFormat.SINGLE_ELIMINATION,
                "variant": TournamentVariant.SINGLES,
                "status": TournamentStatus.UPCOMING,
                "entry_fee": "500",
                "is_one_day": False,
                "gender": "male",
                "tournament_type": "regular",
                "duration": "multi",
                "start_after_fill": True,
                "min_participants": "2",
                "max_participants": "4",
                "allowed_categories": [SkillLevel.AMATEUR],
                "fan_points_r1": "10",
                "fan_points_r2": "25",
                "fan_points_sf": "45",
                "fan_points_final": "70",
                "fan_points_winner": "100",
                "match_days_per_round": "7",
                "postpayment_deadline_hours": "12",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["registration_deadline"])
        self.assertIsNone(form.cleaned_data["start_date"])
        self.assertTrue(form.cleaned_data["start_after_fill"])

    def test_club_min_fill_notifies_club_admins(self) -> None:
        """Клубный турнир: при минимуме письмо уходит админам клуба."""
        from apps.clubs.models import (
            Club,
            ClubMember,
            ClubMemberRole,
            ClubMemberStatus,
            ClubNotificationConfig,
        )

        club = Club.objects.create(
            name="Fill Club",
            slug="fill-club",
            city="Москва",
            address="ул. 1",
            email="fillclub@test.local",
            admin_name="Админ",
        )
        admin_user = make_player(email_suffix="clubfilladmin").user
        ClubMember.objects.create(
            club=club,
            user=admin_user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        ClubNotificationConfig.objects.get_or_create(club=club)

        tournament = _make_fill_tournament(
            slug="club-fill-min", min_participants=2, max_participants=4
        )
        tournament.club = club
        tournament.save(update_fields=["club"])

        p1 = make_player(email_suffix="clubfill1")
        p2 = make_player(email_suffix="clubfill2")

        with patch(
            "apps.core.telegram_notify.requests.post",
            side_effect=Exception("no-tg"),
        ):
            tournament.participants.add(p1)
            tournament.participants.add(p2)

        tournament.refresh_from_db()
        self.assertIsNotNone(tournament.min_fill_reached_notified_at)
        recipients = {m.to[0] for m in mail.outbox if m.to}
        self.assertIn(admin_user.email, recipients)
        combined = " ".join(m.subject + m.body for m in mail.outbox).lower()
        self.assertTrue("минимальн" in combined or "набран" in combined)

    def test_club_form_start_after_fill(self) -> None:
        """Клубная форма создания: галочка очищает даты и требует max."""
        from apps.clubs.forms import ClubTournamentCreateForm
        from apps.clubs.models import Club

        club = Club.objects.create(
            name="Form Club",
            slug="form-club-fill",
            city="Москва",
            address="ул. 1",
            email="formclub@test.local",
            admin_name="Админ",
        )
        form = ClubTournamentCreateForm(
            data={
                "name": "Club Fill",
                "slug": "club-fill-form",
                "city": "Москва",
                "format": TournamentFormat.SINGLE_ELIMINATION,
                "variant": TournamentVariant.SINGLES,
                "entry_fee": "500",
                "is_one_day": False,
                "gender": "male",
                "tournament_type": "regular",
                "start_after_fill": True,
                "min_participants": "2",
                "max_participants": "4",
                "allowed_categories": [SkillLevel.AMATEUR],
                "fan_points_r1": "10",
                "fan_points_r2": "25",
                "fan_points_sf": "45",
                "fan_points_final": "70",
                "fan_points_winner": "100",
                "match_days_per_round": "7",
                "postpayment_deadline_hours": "12",
            },
            club=club,
            is_pro=False,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["start_after_fill"])
        self.assertIsNone(form.cleaned_data["start_date"])
        self.assertIsNone(form.cleaned_data["registration_deadline"])
