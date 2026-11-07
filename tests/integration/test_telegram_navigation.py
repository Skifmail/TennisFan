"""Навигация бота: история матчей, список турниров и пагинация."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.telegram_bot import views
from apps.tournaments.models import Match, Tournament, TournamentStatus
from tests.support.factories import make_player, make_tournament


@override_settings(
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
    NOTIFICATION_CENTER_ENABLED=False,
)
class TelegramNavigationTests(TestCase):
    """Новые пункты меню и история матчей показывают правильные данные."""

    def setUp(self) -> None:
        self.viewer = make_player(email_suffix="tg-nav-viewer")
        self.rival = make_player(email_suffix="tg-nav-rival")
        self.tournament = make_tournament(
            name="Тестовый турнир",
            slug="tg-nav-tournament",
            start_date=date.today(),
        )
        self.tournament.status = TournamentStatus.COMPLETED
        self.tournament.save(update_fields=["status"])

    @staticmethod
    def _flat_buttons(markup: object) -> list[dict[str, str]]:
        """Собрать все inline-кнопки в один список."""
        buttons: list[dict[str, str]] = []
        if not isinstance(markup, dict):
            return buttons
        for row in markup.get("inline_keyboard", []):
            for button in row:
                if isinstance(button, dict):
                    buttons.append(button)
        return buttons

    def _send(self, callback_data: str, player=None) -> tuple[object, object]:
        """Вызвать обработчик callback и вернуть mock отправки и его вызов."""
        player = player or self.viewer
        with patch(
            "apps.telegram_bot.views.bot.send_to_user", return_value=True
        ) as send:
            views._handle_menu_callback_action(
                1001,
                None,
                callback_data,
                player.user,
                player,
                "https://tennisfan.ru",
            )
        return send, send.call_args

    def _completed_match(
        self,
        *,
        winner_is_viewer: bool = True,
        sets: tuple[tuple[int, int], ...] = ((6, 4), (3, 6), (7, 5)),
        tournament: Tournament | None = None,
    ) -> Match:
        """Создать завершённый одиночный матч."""
        match = Match.objects.create(
            tournament=tournament or self.tournament,
            player1=self.viewer,
            player2=self.rival,
            status=Match.MatchStatus.COMPLETED,
            winner=self.viewer if winner_is_viewer else self.rival,
            player1_set1=sets[0][0],
            player2_set1=sets[0][1],
            player1_set2=sets[1][0],
            player2_set2=sets[1][1],
            player1_set3=sets[2][0],
            player2_set3=sets[2][1],
        )
        return match

    def test_history_score_is_shown_from_viewer_side(self) -> None:
        """Счёт в истории отражает сторону игрока, а не всегда сторону 1."""
        self._completed_match(winner_is_viewer=False)
        _, call = self._send("my_matches_done_0")
        text = call.args[1]
        self.assertIn("Счёт: <b>6:4 3:6 7:5</b>", text)
        self.assertIn("Вы проиграли.", text)

        _, call = self._send("my_matches_done_0", player=self.rival)
        text = call.args[1]
        self.assertIn("Счёт: <b>4:6 6:3 5:7</b>", text)
        self.assertIn("Вы выиграли.", text)

    def test_history_pagination_uses_offset_in_callback_data(self) -> None:
        """Длинная история режется на страницы, смещение передаётся в callback_data."""
        for _ in range(7):
            self._completed_match()

        _, call = self._send("my_matches_done_0")
        text = call.args[1]
        markup = call.kwargs["reply_markup"]
        self.assertIn("<b>1.</b>", text)
        self.assertIn("<b>5.</b>", text)
        self.assertNotIn("<b>6.</b>", text)
        self.assertIn("my_matches_done_5", str(markup))

        _, call = self._send("my_matches_done_5")
        text = call.args[1]
        markup = call.kwargs["reply_markup"]
        self.assertIn("<b>6.</b>", text)
        self.assertIn("<b>7.</b>", text)
        self.assertIn("my_matches_done_0", str(markup))

    def test_history_button_appears_in_my_matches(self) -> None:
        """В «Мои матчи» появляется кнопка перехода в историю с количеством."""
        self._completed_match()
        _, call = self._send("menu_my_matches")
        markup = call.kwargs["reply_markup"]
        buttons = self._flat_buttons(markup)
        labels = [button["text"] for button in buttons]
        self.assertIn("🕘 История матчей (1)", labels)
        history_buttons = [
            button for button in buttons if "История матчей" in button["text"]
        ]
        self.assertEqual(history_buttons[0]["callback_data"], "my_matches_done_0")

    def test_tournaments_menu_lists_active_and_hides_registration_after_bracket(
        self,
    ) -> None:
        """Список показывает открытый турнир, а после формирования сетки — без записи."""
        open_tournament = make_tournament(
            name="Турнир <b>зимой</b> & летом",
            slug="open-tournament",
            start_date=date.today(),
            max_participants=8,
            city="Москва",
        )
        locked_tournament = make_tournament(
            name="Закрытый турнир",
            slug="locked-tournament",
            start_date=date.today(),
            city="Москва",
        )
        locked_tournament.bracket_generated = True
        locked_tournament.save(update_fields=["bracket_generated"])

        _, call = self._send("menu_tournaments")
        text = call.args[1]
        markup = call.kwargs["reply_markup"]
        buttons = self._flat_buttons(markup)

        self.assertIn("Турнир &lt;b&gt;зимой&lt;/b&gt; &amp; летом", text)
        self.assertIn("Закрытый турнир", text)
        detail_buttons = [
            button for button in buttons if button.get("text", "").startswith("🏆")
        ]
        self.assertTrue(detail_buttons)
        self.assertTrue(all(button.get("url") for button in detail_buttons))
        register_buttons = [
            button for button in buttons if button.get("text") == "✍️ Запись"
        ]
        # Только у открытого турнира есть кнопка записи.
        self.assertEqual(len(register_buttons), 1)
        self.assertIn(
            reverse("tournament_register", args=[open_tournament.slug]),
            register_buttons[0]["url"],
        )

    def test_tournaments_menu_hides_registration_when_full(self) -> None:
        """Полный турнир не предлагает кнопку записи."""
        full_tournament = make_tournament(
            name="Полный турнир",
            slug="full-tournament",
            start_date=date.today(),
            max_participants=1,
            city="Москва",
        )
        full_tournament.participants.add(self.viewer)

        _, call = self._send("menu_tournaments")
        buttons = self._flat_buttons(call.kwargs["reply_markup"])
        register_buttons = [
            button for button in buttons if button.get("text") == "✍️ Запись"
        ]
        self.assertEqual(register_buttons, [])

    def test_tournaments_pagination_uses_offset_in_callback_data(self) -> None:
        """Список турниров режется на страницы с кнопками навигации."""
        for index in range(6):
            make_tournament(
                name=f"Турнир {index}",
                slug=f"page-tournament-{index}",
                start_date=date.today(),
                city="Москва",
            )

        _, call = self._send("menu_tournaments")
        markup = call.kwargs["reply_markup"]
        self.assertIn("tournaments_page_5", str(markup))

        _, call = self._send("tournaments_page_5")
        markup = call.kwargs["reply_markup"]
        self.assertIn("tournaments_page_0", str(markup))
