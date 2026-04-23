"""Тесты приложения payments."""

from datetime import date

from django.test import TestCase

from apps.clubs.models import Club
from apps.payments.views import _get_item_label
from apps.tournaments.models import Tournament


class PaymentItemLabelTestCase(TestCase):
    """Проверка формирования названий сущностей в журнале платежей."""

    def test_tournament_item_label_for_platform_tournament(self) -> None:
        """Проверить подпись для платформенного турнира.

        Args:
            None: Используются данные, созданные в тестовой базе.

        Returns:
            None: Проверяет значение, возвращаемое `_get_item_label`.
        """
        tournament = Tournament.objects.create(
            name="Весенний кубок",
            slug="vesenniy-kubok",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            entry_fee=1000,
        )
        label = _get_item_label("tournament", str(tournament.id))
        self.assertEqual(label, "Весенний кубок")

    def test_tournament_item_label_for_club_tournament(self) -> None:
        """Проверить подпись для клубного турнира.

        Args:
            None: Используются данные, созданные в тестовой базе.

        Returns:
            None: Проверяет формат строки с названием клуба и турнира.
        """
        club = Club.objects.create(
            name="Тестовый клуб",
            slug="test-club",
            city="Москва",
            address="Тестовая улица, 1",
            email="club@test.local",
            admin_name="Админ Клуба",
        )
        tournament = Tournament.objects.create(
            name="Клубный турнир",
            slug="club-tournament",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            entry_fee=1000,
            club=club,
        )
        label = _get_item_label("tournament", str(tournament.id))
        self.assertEqual(label, "Тестовый клуб: Клубный турнир")
