"""Юнит-тесты публичных меток статуса турнира."""

from datetime import date

from django.test import SimpleTestCase
from django.utils import timezone

from apps.tournaments.models import Tournament, TournamentStatus
from apps.tournaments.platform_home import get_tournament_public_status_label


class TournamentPublicStatusLabelTestCase(SimpleTestCase):
    """Метка набора учитывает заполненность и постоплату, не только status."""

    def _tournament(self, **kwargs) -> Tournament:
        defaults = {
            "name": "Status test",
            "slug": "status-test",
            "city": "Москва",
            "start_date": date.today(),
            "format": "round_robin",
            "status": TournamentStatus.UPCOMING,
            "max_participants": 8,
            "allow_postpayment": True,
        }
        defaults.update(kwargs)
        return Tournament(**defaults)

    def test_upcoming_open_is_recruiting(self) -> None:
        t = self._tournament()
        t.is_full_annotated = False
        self.assertEqual(get_tournament_public_status_label(t), "Идёт набор")

    def test_upcoming_without_court_keeps_short_badge(self) -> None:
        t = self._tournament()
        t.is_full_annotated = False
        t.court_id = None
        self.assertEqual(get_tournament_public_status_label(t), "Идёт набор")

    def test_upcoming_full_shows_no_seats(self) -> None:
        t = self._tournament()
        t.is_full_annotated = True
        self.assertEqual(get_tournament_public_status_label(t), "Мест нет")

    def test_postpayment_window_closes_registration_label(self) -> None:
        t = self._tournament()
        t.is_full_annotated = False
        t.postpayment_window_started_at = timezone.now()
        self.assertEqual(
            get_tournament_public_status_label(t),
            "Регистрация закрыта",
        )

    def test_active_is_in_game(self) -> None:
        t = self._tournament(status=TournamentStatus.ACTIVE)
        self.assertEqual(get_tournament_public_status_label(t), "В игре")
