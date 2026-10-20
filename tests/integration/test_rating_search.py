"""Поиск игроков на странице рейтинга по имени и фамилии."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.users.models import Player
from tests.support.factories import make_player


class RatingNameSearchTests(TestCase):
    """Поиск в рейтинге понимает полное имя, порядок слов и лишние пробелы."""

    def setUp(self) -> None:
        self.alexander_shataylo = self._make_rated_player(
            suffix="shataylo",
            first_name="Александр",
            last_name="Шатайло",
            city="Москва",
            points=5000.0,
        )
        self.alexander_smirnov = self._make_rated_player(
            suffix="smirnov",
            first_name="Александр",
            last_name="Смирнов",
            city="Казань",
            points=4000.0,
        )
        self.anna_shataylo = self._make_rated_player(
            suffix="anna",
            first_name="Анна",
            last_name="Шатайло",
            city="Москва",
            points=3000.0,
        )

    def _make_rated_player(
        self,
        *,
        suffix: str,
        first_name: str,
        last_name: str,
        city: str,
        points: float,
    ) -> Player:
        """Создать видимого в рейтинге игрока с заполненным именем."""
        player = make_player(
            email_suffix=suffix,
            first_name=first_name,
            last_name=last_name,
            points=points,
        )
        player.city = city
        player.is_verified = True
        player.save(update_fields=["city", "is_verified"])
        return player

    def _found_names(self, query: str) -> set[str]:
        """Вернуть имена игроков из выдачи рейтинга по запросу."""
        response = self.client.get(reverse("rating"), {"q": query}, secure=True)
        self.assertEqual(response.status_code, 200)
        context = response.context
        assert context is not None
        rating_data = context["rating_data"]
        return {str(row["name"]) for row in rating_data}

    def test_finds_by_first_name(self) -> None:
        """Одно имя находит всех игроков с таким именем."""
        self.assertEqual(
            self._found_names("Александр"),
            {"Александр Шатайло", "Александр Смирнов"},
        )

    def test_ignores_trailing_and_repeated_spaces(self) -> None:
        """Пробел в конце и лишние пробелы внутри не ломают поиск."""
        self.assertEqual(
            self._found_names("  Александр   "),
            {"Александр Шатайло", "Александр Смирнов"},
        )
        self.assertEqual(
            self._found_names("Александр   Шатайло"),
            {"Александр Шатайло"},
        )

    def test_finds_full_name_in_any_order_and_case(self) -> None:
        """Полное имя находится в любом порядке слов и регистре."""
        for query in (
            "Александр Шатайло",
            "Шатайло Александр",
            "александр шатайло",
            "ШАТАЙЛО АЛЕКСАНДР",
        ):
            with self.subTest(query=query):
                self.assertEqual(
                    self._found_names(query),
                    {"Александр Шатайло"},
                )

    def test_full_name_does_not_match_other_players(self) -> None:
        """Второе слово запроса не даёт совпадений по другому игроку."""
        self.assertEqual(
            self._found_names("Александр Шатайло"),
            {"Александр Шатайло"},
        )
        self.assertNotIn("Анна Шатайло", self._found_names("Александр Шатайло"))
        self.assertNotIn("Александр Смирнов", self._found_names("Александр Шатайло"))

    def test_empty_query_returns_all_players(self) -> None:
        """Пустой запрос показывает всех видимых игроков."""
        self.assertEqual(
            self._found_names("   "),
            {"Александр Шатайло", "Александр Смирнов", "Анна Шатайло"},
        )
