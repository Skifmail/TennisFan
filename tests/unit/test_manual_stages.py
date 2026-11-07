"""Проверки кругового отбора и двух сеток в обычных ручных турнирах."""

from __future__ import annotations

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.tournaments.manual_pairing import (
    ManualPairingError,
    load_entities,
    save_manual_round,
    start_manual_tournament,
)
from apps.tournaments.manual_stages import (
    create_qualifiers,
    create_two_knockouts,
    handle_result,
)
from apps.tournaments.models import (
    ManualTournamentStage,
    Match,
    TournamentStatus,
    TournamentTeam,
)
from apps.tournaments.score_validation import ProposalValidationError
from tests.support.factories import make_player, make_tournament, make_user


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class ManualStagesTests(TestCase):
    """Проверяет полный сценарий, неизменность формата и защиту этапов."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.players = [
            make_player(email_suffix=f"stages-{i}", points=2000 - i * 50)
            for i in range(10)
        ]
        cls.manager = make_user(email="stages-manager@test.local", is_staff=True)

    def setUp(self) -> None:
        patch("apps.telegram_bot.notifications.notify_tournament_start").start()
        patch("apps.telegram_bot.notifications.notify_match_created").start()
        patch(
            "apps.tournaments.proposal_service.notify_participants_match_result_confirmed"
        ).start()
        self.addCleanup(patch.stopall)
        self.tournament = make_tournament(
            slug="stages",
            format="single_elimination",
            max_participants=16,
            allow_postpayment=False,
        )
        self.tournament.participants.set(self.players[:8])
        self.client.force_login(self.manager)
        self.assertTrue(start_manual_tournament(self.tournament)[0])
        self.tokens = [f"p:{p.pk}" for p in self.players[:8]]

    def _qualifiers(self) -> None:
        """Создать два отборочных круга."""
        create_qualifiers(self.tournament, {"A": self.tokens[:4], "B": self.tokens[4:]})

    def _complete(self, match: Match) -> None:
        """Сыграть матч с победой первой стороны."""
        match.refresh_from_db()
        match.winner = match.player1
        match.winner_team = match.team1
        match.player1_set1 = 6
        match.player2_set1 = 0
        match.status = Match.MatchStatus.COMPLETED
        match.save()

    def _complete_qualifiers(self) -> None:
        """Завершить все матчи отбора."""
        for match in self.tournament.matches.filter(manual_stage__kind="qualifier"):
            self._complete(match)

    def _play_branch(self, kind: str) -> None:
        """Сыграть все реальные матчи ветви по порядку кругов."""
        for match in self.tournament.matches.filter(manual_stage__kind=kind).order_by(
            "round_index", "round_order"
        ):
            if match.status not in ("completed", "walkover"):
                self._complete(match)

    def test_qualifiers_play_all_pairs_once_and_keep_original_format(self) -> None:
        """В каждом отборе есть все пары, соперники из разных отборов не встречаются."""
        self._qualifiers()
        self.assertEqual(self.tournament.matches.count(), 12)
        self.assertEqual(ManualTournamentStage.objects.count(), 2)
        for stage in self.tournament.manual_stages.all():
            self.assertEqual(stage.matches.count(), 6)
            self.assertEqual(
                len(
                    {
                        frozenset((m.player1_id, m.player2_id))
                        for m in stage.matches.all()
                    }
                ),
                6,
            )
        self._complete_qualifiers()
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.format, "single_elimination")
        self.assertEqual(self.tournament.status, TournamentStatus.ACTIVE)
        self.assertFalse(self.tournament.fan_results.exists())

    def test_round_robin_supports_same_scenario_without_early_completion(self) -> None:
        """Круговой отбор не финализируется штатным круговым алгоритмом."""
        self.tournament.format = "round_robin"
        self.tournament.save()
        create_qualifiers(self.tournament, {"A": self.tokens})
        self._complete_qualifiers()
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.ACTIVE)
        self.assertEqual(self.tournament.format, "round_robin")
        create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        self._play_branch("main")
        self._play_branch("consolation")
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.COMPLETED)

    def test_waits_for_both_finals_and_awards_points_once(self) -> None:
        """Финал основной сетки не закрывает утешительную; очки идемпотентны."""
        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        self._play_branch("main")
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.ACTIVE)
        self.assertFalse(self.tournament.fan_results.exists())
        self._play_branch("consolation")
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.COMPLETED)
        self.assertEqual(self.tournament.fan_results.count(), 8)
        self.assertEqual(
            sorted(self.tournament.fan_results.values_list("place", flat=True)),
            [1, 2, 3, 3, 5, 6, 7, 7],
        )
        match = self.tournament.matches.filter(
            manual_stage__kind="consolation", next_match__isnull=True
        ).get()
        before = list(
            self.tournament.fan_results.values_list(
                "player__season_points__current_season_points", flat=True
            )
        )
        handle_result(self.tournament, match)
        after = list(
            self.tournament.fan_results.values_list(
                "player__season_points__current_season_points", flat=True
            )
        )
        self.assertEqual(before, after)
        match.winner = match.player2
        with self.assertRaises(ValidationError):
            match.save()

    def test_odd_knockout_sizes_have_byes_and_real_final(self) -> None:
        """Свободные проходы работают при трёх и пяти участниках сеток."""
        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:3], self.tokens[3:])
        self.assertEqual(
            self.tournament.matches.filter(manual_stage__kind="main").count(), 3
        )
        self.assertEqual(
            self.tournament.matches.filter(
                manual_stage__kind="main", status="walkover"
            ).count(),
            1,
        )
        self._play_branch("main")
        self._play_branch("consolation")
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.COMPLETED)
        self.assertEqual(self.tournament.fan_results.count(), 8)

    def test_fourth_places_can_enter_consolation_and_arbitrary_manual_pairs(
        self,
    ) -> None:
        """Посев задаёт организатор, без ограничения третьими местами."""
        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[::2], self.tokens[1::2])
        matches = list(
            self.tournament.matches.filter(
                manual_stage__kind="main", round_index=1
            ).order_by("round_order")
        )
        self.assertEqual(
            (matches[0].player1_id, matches[0].player2_id),
            (self.players[0].pk, self.players[2].pk),
        )
        consolation = self.tournament.manual_stages.get(kind="consolation")
        self.assertIn(self.tokens[7], consolation.member_tokens)

    def test_early_knockouts_rejected_without_partial_writes(self) -> None:
        """Нельзя создавать сетки до окончания всех отборов."""
        self._qualifiers()
        with self.assertRaises(ManualPairingError):
            create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        self.assertEqual(self.tournament.manual_stages.count(), 2)

    def test_duplicate_or_missing_qualifier_members_rejected(self) -> None:
        """Дубли, пропуски и отборы из одного участника не сохраняются."""
        for groups in [
            {"A": self.tokens[:4], "B": self.tokens[3:]},
            {"A": self.tokens[:4]},
            {"A": self.tokens[:1], "B": self.tokens[1:]},
        ]:
            with self.assertRaises(ManualPairingError):
                create_qualifiers(self.tournament, groups)
        self.assertFalse(self.tournament.matches.exists())
        self.assertFalse(self.tournament.manual_stages.exists())

    def test_duplicate_or_missing_knockout_members_rejected(self) -> None:
        """Обе сетки сохраняются только вместе и без дублей."""
        self._qualifiers()
        self._complete_qualifiers()
        for main, cons in [
            (self.tokens[:4], self.tokens[3:]),
            (self.tokens[:4], self.tokens[5:]),
            (self.tokens[:1], self.tokens[1:]),
        ]:
            with self.assertRaises(ManualPairingError):
                create_two_knockouts(self.tournament, main, cons)
        self.assertEqual(self.tournament.manual_stages.count(), 2)

    def test_repeat_creation_is_rejected(self) -> None:
        """Повторная отправка формы не дублирует матчи."""
        self._qualifiers()
        with self.assertRaises(ManualPairingError):
            self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        with self.assertRaises(ManualPairingError):
            create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        self.assertEqual(self.tournament.matches.count(), 18)

    def test_qualifier_result_locked_after_knockout_creation(self) -> None:
        """После посева изменение отборочного результата откатывается."""
        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        match = self.tournament.matches.filter(manual_stage__kind="qualifier").first()
        match.winner = match.player2
        with self.assertRaises(ValidationError):
            match.save()
        match.refresh_from_db()
        self.assertEqual(match.winner_id, match.player1_id)

    def test_standard_pair_builder_cannot_add_matches_to_staged_tournament(
        self,
    ) -> None:
        """Прямой старый POST не смешивает обычный тур с этапами."""
        self._qualifiers()
        entities = load_entities(self.tournament)
        ok, _ = save_manual_round(
            self.tournament,
            pairs=list(zip(entities[::2], entities[1::2], strict=True)),
            leftover=None,
            auto_next=True,
        )
        self.assertFalse(ok)
        self.assertEqual(self.tournament.matches.count(), 12)

    def test_doubles_qualify_and_play_two_knockouts(self) -> None:
        """В парном варианте перемещаются полные команды, оба игрока получают итог."""
        self.tournament.variant = "doubles"
        self.tournament.save()
        teams = [
            TournamentTeam.objects.create(
                tournament=self.tournament,
                player1=self.players[i],
                player2=self.players[i + 1],
            )
            for i in range(0, 8, 2)
        ]
        tokens = [f"t:{t.pk}" for t in teams]
        create_qualifiers(self.tournament, {"A": tokens})
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, tokens[:2], tokens[2:])
        self._play_branch("main")
        self._play_branch("consolation")
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.COMPLETED)
        self.assertEqual(self.tournament.fan_results.count(), 8)

    def test_manager_can_submit_and_render_qualifiers(self) -> None:
        """Организатор сохраняет отбор и видит форму результатов."""
        url = reverse("tournament_manage_stages", args=[self.tournament.slug])
        page = self.client.get(url)
        self.assertContains(page, "Круговой отбор")
        response = self.client.post(
            url,
            {
                "action": "qualifiers",
                **{
                    f"qualifier_{token}": str(i // 4 + 1)
                    for i, token in enumerate(self.tokens)
                },
            },
        )
        self.assertRedirects(response, url)
        page = self.client.get(url)
        self.assertContains(page, "Отбор 1")
        self.assertContains(page, 'name="winner_id"')

    def test_unauthorized_user_cannot_create_stages(self) -> None:
        """Участник не получает права организатора через новый маршрут."""
        self.client.force_login(self.players[0].user)
        url = reverse("tournament_manage_stages", args=[self.tournament.slug])
        self.client.post(url, {"action": "qualifiers"})
        self.assertFalse(self.tournament.manual_stages.exists())

    def test_public_page_separates_stages_without_management_forms(self) -> None:
        """Публичная страница показывает таблицы отборов, сохраняя обычную карточку."""
        self._qualifiers()
        self.client.logout()
        response = self.client.get(
            reverse("tournament_detail", args=[self.tournament.slug])
        )
        self.assertContains(response, "Отбор A")
        self.assertNotContains(response, 'name="winner_id"')
        self.assertTemplateUsed(response, "tournaments/detail.html")

    def test_knockout_form_preserves_manual_assignment(self) -> None:
        """Ручной выбор сеток и посева сохраняется через форму организатора."""
        self._qualifiers()
        self._complete_qualifiers()
        url = reverse("tournament_manage_stages", args=[self.tournament.slug])
        data = {"action": "knockouts"}
        for index, token in enumerate(self.tokens):
            data[f"branch_{token}"] = "main" if index < 4 else "consolation"
            data[f"seed_{token}"] = str(8 - index)
        response = self.client.post(url, data)
        self.assertRedirects(response, url)
        stage = self.tournament.manual_stages.get(kind="main")
        self.assertEqual(stage.member_tokens, list(reversed(self.tokens[:4])))

    def test_cancelled_tournament_does_not_accept_qualifiers(self) -> None:
        """Отменённый турнир не возобновляется новым действием."""
        self.tournament.status = TournamentStatus.CANCELLED
        self.tournament.save()
        with self.assertRaises(ManualPairingError):
            self._qualifiers()
        self.assertFalse(self.tournament.manual_stages.exists())

    def test_exports_keep_qualifiers_and_both_brackets_separate(self) -> None:
        """PDF-модель и регламент описывают фактический ручной сценарий."""
        from apps.tournaments.exports.bracket import build_bracket_layout
        from apps.tournaments.exports.regulation import _system_section

        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        layout = build_bracket_layout(self.tournament)
        self.assertEqual(len(layout.tables), 2)
        self.assertEqual(
            [board.title for board in layout.boards],
            ["Основная сетка", "Утешительная сетка"],
        )
        self.assertIn(
            "обоих финалов", " ".join(_system_section(self.tournament).paragraphs)
        )

    def test_next_match_notification_waits_for_both_players(self) -> None:
        """Уведомление следующего матча появляется после определения обеих сторон."""
        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        semifinals = list(
            self.tournament.matches.filter(
                manual_stage__kind="main", round_index=1
            ).order_by("round_order")
        )
        with patch("apps.tournaments.signals.notify_telegram_match_created") as notify:
            self._complete(semifinals[0])
            notify.assert_not_called()
            self._complete(semifinals[1])
            notify.assert_called_once()
            final = notify.call_args.args[1]
            self.assertTrue(final.player1_id and final.player2_id)

    def test_one_participant_cannot_enter_two_stages(self) -> None:
        """Минимум четыре участника необходим для двух олимпийских сеток."""
        self.tournament.participants.set(self.players[:2])
        with self.assertRaises(ManualPairingError):
            create_qualifiers(self.tournament, {"A": self.tokens[:2]})
        self.assertFalse(self.tournament.matches.exists())

    def test_bye_resave_does_not_add_played_match(self) -> None:
        """Повторное сохранение прохода без игры не добавляет матч в статистику."""
        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:3], self.tokens[3:])
        bye = self.tournament.matches.filter(
            manual_stage__kind="main", status="walkover"
        ).get()
        player = bye.player1
        player.refresh_from_db()
        before = player.matches_played
        bye.save(update_fields=["deadline"])
        player.refresh_from_db()
        self.assertEqual(player.matches_played, before)

    def test_second_bracket_failure_rolls_back_both(self) -> None:
        """Сбой построения второй сетки откатывает и первую."""
        from apps.tournaments.manual_stages import _build_knockout

        self._qualifiers()
        self._complete_qualifiers()
        calls = 0

        def build(stage: ManualTournamentStage, entities: dict) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Проверка отката")
            _build_knockout(stage, entities)

        with patch("apps.tournaments.manual_stages._build_knockout", side_effect=build):
            with self.assertRaises(RuntimeError):
                create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        self.assertEqual(self.tournament.manual_stages.count(), 2)
        self.assertEqual(self.tournament.matches.count(), 12)

    def test_future_match_deadline_starts_when_both_players_are_known(self) -> None:
        """Ожидающий победителей финал не получает дедлайн раньше соперников."""
        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        final = self.tournament.matches.get(
            manual_stage__kind="main", next_match__isnull=True
        )
        self.assertIsNone(final.deadline)
        matches = self.tournament.matches.filter(
            manual_stage__kind="main", round_index=1
        )
        for match in matches:
            self._complete(match)
        final.refresh_from_db()
        self.assertIsNotNone(final.deadline)

    def test_common_management_keeps_cancel_and_stage_link(self) -> None:
        """Новая страница не прячет общие действия организатора."""
        self._qualifiers()
        response = self.client.get(
            reverse("tournament_manage", args=[self.tournament.slug])
        )
        self.assertContains(response, "Открыть ручные этапы")
        self.assertContains(
            response, reverse("tournament_manage_cancel", args=[self.tournament.slug])
        )

    def test_advancement_when_target_loses_tournament_should_rollback_result(
        self,
    ) -> None:
        """Повреждённая связь финала откатывает результат без частичного продвижения."""
        self._qualifiers()
        self._complete_qualifiers()
        create_two_knockouts(self.tournament, self.tokens[:4], self.tokens[4:])
        semifinals = list(
            self.tournament.matches.filter(
                manual_stage__kind="main", round_index=1
            ).order_by("round_order")
        )
        self._complete(semifinals[0])
        final = semifinals[1].next_match
        assert final is not None
        Match.objects.filter(pk=final.pk).update(tournament=None)

        with self.assertRaisesRegex(ProposalValidationError, "турнира"):
            self._complete(semifinals[1])

        semifinals[1].refresh_from_db()
        final.refresh_from_db()
        self.assertEqual(semifinals[1].status, Match.MatchStatus.SCHEDULED)
        self.assertIsNone(final.player2_id)
