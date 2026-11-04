"""Тесты названий формата «Группы + плей-офф»."""

from django.test import SimpleTestCase

from apps.content.rules_defaults import RULES_SECTION_TITLES
from apps.tournaments.models import (
    MANUAL_PAIRING_FORMATS_NOTE,
    Tournament,
    TournamentFormat,
)


class TournamentFormatLabelTests(SimpleTestCase):
    """Проверяет, что формат не путается с однодневной длительностью."""

    def test_weekend_day_format_value_is_stable(self) -> None:
        """Код формата остаётся weekend_day: на нём лежат существующие турниры."""
        self.assertEqual(TournamentFormat.WEEKEND_DAY.value, "weekend_day")

    def test_weekend_day_label_names_structure(self) -> None:
        """Подпись формата описывает структуру, а не длительность."""
        self.assertEqual(
            TournamentFormat.WEEKEND_DAY.label,
            "Группы + плей-офф",
        )

    def test_format_display_uses_new_label(self) -> None:
        """get_format_display возвращает новую подпись."""
        tournament = Tournament(format=TournamentFormat.WEEKEND_DAY)

        self.assertEqual(tournament.get_format_display(), "Группы + плей-офф")

    def test_rules_section_title_is_renamed(self) -> None:
        """Раздел правил называется так же, как формат."""
        self.assertEqual(RULES_SECTION_TITLES["rules_tvd"], "Группы + плей-офф")

    def test_manual_pairing_note_names_supported_formats(self) -> None:
        """Пометка перечисляет форматы с ручным управлением и предупреждает об остальных."""
        self.assertIn(
            f"«{TournamentFormat.SINGLE_ELIMINATION.label}»",
            MANUAL_PAIRING_FORMATS_NOTE,
        )
        self.assertIn(
            f"«{TournamentFormat.ROUND_ROBIN.label}»",
            MANUAL_PAIRING_FORMATS_NOTE,
        )
        self.assertIn(
            "В остальных форматах пары строятся автоматически",
            MANUAL_PAIRING_FORMATS_NOTE,
        )

    def test_admin_format_help_shows_manual_pairing_note(self) -> None:
        """Пометка видна staff-админу в форме создания турнира."""
        from apps.tournaments.admin import TournamentAdminForm

        form = TournamentAdminForm()

        self.assertIn(
            MANUAL_PAIRING_FORMATS_NOTE,
            str(form.fields["format"].help_text),
        )
