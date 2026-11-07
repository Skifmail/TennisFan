"""Свежие составы, однократная генерация и атомарные уведомления парного спарринга."""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import UserTelegramLink
from apps.notifications.models import Delivery
from apps.sparring.doubles_services import (
    accept_join_request,
    add_partner_to_author_team,
    confirm_match,
    confirm_team_sparring_series,
    create_doubles_request,
    create_join_request,
    delete_match_request,
    remove_team_member,
)
from apps.sparring.models import (
    DoublesJoinRequest,
    DoublesJoinRequestStatus,
    DoublesMatchKind,
    DoublesMatchRequest,
    DoublesMatchRequestStatus,
    DoublesTeamMember,
    TeamSide,
)
from apps.tournaments.models import Match
from apps.users.models import Notification, Player
from tests.support.factories import make_player


@override_settings(
    NOTIFICATION_CENTER_ENABLED=False,
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver"],
)
class SparringTeamAtomicityTests(TestCase):
    """Одни и те же сервисы защищают действия сайта и бота."""

    author: ClassVar[Player]
    partner: ClassVar[Player]
    opponent1: ClassVar[Player]
    opponent2: ClassVar[Player]

    @classmethod
    def setUpTestData(cls) -> None:
        """Создать четыре разных аккаунта с явной привязкой к Telegram."""
        cls.author = make_player(email_suffix="team-atomic-author")
        cls.partner = make_player(email_suffix="team-atomic-partner")
        cls.opponent1 = make_player(email_suffix="team-atomic-opponent1")
        cls.opponent2 = make_player(email_suffix="team-atomic-opponent2")
        for chat_id, player in enumerate(
            (cls.author, cls.partner, cls.opponent1, cls.opponent2), start=1001
        ):
            UserTelegramLink.objects.create(user=player.user, user_bot_chat_id=chat_id)

    def setUp(self) -> None:
        """Использовать настоящую очередь, исключив внешнюю сеть."""
        patcher = patch("apps.telegram_bot.services.is_configured", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        mail.outbox.clear()

    def _request(
        self, *, kind: str = "classic", full_author: bool = True
    ) -> DoublesMatchRequest:
        """Создать заявку нужного типа с одним или двумя игроками автора."""
        return create_doubles_request(
            created_by=self.author,
            partner=self.partner if full_author else None,
            kind=kind,
        )

    def _join(self, request: DoublesMatchRequest) -> DoublesJoinRequest:
        """Сохранить отклик двух настоящих соперников."""
        return create_join_request(
            match_request_id=request.pk,
            created_by=self.opponent1,
            target_side=TeamSide.OPPONENT,
            players=[self.opponent1, self.opponent2],
        )

    def _accept(self, request: DoublesMatchRequest, join: DoublesJoinRequest) -> None:
        """Принять отклик через общий сервис."""
        accept_join_request(
            match_request_id=request.pk,
            join_request_id=join.pk,
            accepted_by=self.author,
        )

    def _ready(self, *, kind: str = "classic") -> DoublesMatchRequest:
        """Подготовить состав, отделив его уведомления от следующего действия."""
        request = self._request(kind=kind)
        self._accept(request, self._join(request))
        request.refresh_from_db()
        Delivery.objects.all().delete()
        Notification.objects.all().delete()
        mail.outbox.clear()
        return request

    def _assert_accept_rolled_back(
        self, request: DoublesMatchRequest, join: DoublesJoinRequest
    ) -> None:
        """Отказ не сохраняет команду, статус и уведомления первого получателя."""
        request.refresh_from_db()
        join.refresh_from_db()
        self.assertEqual(request.status, DoublesMatchRequestStatus.FORMING)
        self.assertEqual(join.status, DoublesJoinRequestStatus.PENDING)
        self.assertFalse(request.teams.filter(side=TeamSide.OPPONENT).exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(mail.outbox)

    def test_join_requires_two_different_players(self) -> None:
        """Повтор одного игрока отклоняется как бизнес-ошибка, а не ошибка БД."""
        request = self._request()
        with self.assertRaises(ValueError):
            create_join_request(
                match_request_id=request.pk,
                created_by=self.opponent1,
                target_side=TeamSide.OPPONENT,
                players=[self.opponent1, self.opponent1],
            )
        self.assertFalse(request.join_requests.exists())

    def test_join_cannot_be_submitted_for_two_other_people(self) -> None:
        """Заявитель обязан входить в состав своего отклика."""
        request = self._request()
        with self.assertRaises(ValueError):
            create_join_request(
                match_request_id=request.pk,
                created_by=self.opponent1,
                target_side=TeamSide.OPPONENT,
                players=[self.opponent2],
            )
        self.assertFalse(request.join_requests.exists())

    def test_accept_rechecks_player_membership_in_both_teams(self) -> None:
        """Игрок, добавленный в команду автора после отклика, не попадает к соперникам."""
        request = self._request(full_author=False)
        join = self._join(request)
        add_partner_to_author_team(
            match_request_id=request.pk,
            player_id=self.opponent1.pk,
            added_by=self.author,
        )
        with self.assertRaises(ValueError):
            self._accept(request, join)
        self._assert_accept_rolled_back(request, join)

    def test_accept_rechecks_that_submitter_is_still_in_roster(self) -> None:
        """Прежний отклик с изменённым составом не принимается от чужого капитана."""
        request = self._request()
        join = self._join(request)
        join.members.filter(player=self.opponent1).delete()
        with self.assertRaises(ValueError):
            self._accept(request, join)
        self._assert_accept_rolled_back(request, join)

    def test_accept_inbox_failure_rolls_back_roster(self) -> None:
        """Создание уведомления входит в ту же транзакцию, что и принятие."""
        request = self._request()
        join = self._join(request)
        with patch(
            "apps.sparring.doubles_services.create_notification",
            side_effect=RuntimeError("join inbox failed"),
        ):
            with self.assertRaises(RuntimeError):
                self._accept(request, join)
        self._assert_accept_rolled_back(request, join)

    def test_accept_second_delivery_failure_rolls_back_all_notifications(self) -> None:
        """После сбоя второго получателя не остаётся даже уведомлений готовой команды."""
        request = self._request(kind=DoublesMatchKind.TEAM)
        join = self._join(request)
        original = Delivery.objects.create

        def create(**kwargs: Any) -> Delivery:
            """Отказать только второму игроку отклика после всех предыдущих записей."""
            if (
                kwargs.get("event_key") == "sparring.doubles_joined"
                and kwargs.get("user_id") == self.opponent2.user_id
            ):
                raise RuntimeError("second join delivery failed")
            return original(**kwargs)

        with patch(
            "apps.telegram_bot.delivery.Delivery.objects.create", side_effect=create
        ):
            with self.assertRaises(RuntimeError):
                self._accept(request, join)
        self._assert_accept_rolled_back(request, join)

    def test_site_acceptance_notifies_each_join_member_once(self) -> None:
        """Веб-обработчик не дублирует уведомления общего сервиса."""
        request = self._request()
        join = self._join(request)
        self.client.force_login(self.author.user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("doubles_accept_join", args=[request.pk, join.pk])
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(
            Delivery.objects.filter(event_key="sparring.doubles_joined").count(), 2
        )
        self.assertEqual(len(mail.outbox), 2)

    def test_repeated_acceptance_is_business_refusal_without_duplicate_delivery(
        self,
    ) -> None:
        """Прежняя кнопка не создаёт повторное уведомление или техническую ошибку."""
        request = self._request(full_author=False)
        join = self._join(request)
        self._accept(request, join)
        before = Delivery.objects.count()
        with self.assertRaises(ValueError):
            self._accept(request, join)
        self.assertEqual(Delivery.objects.count(), before)

    def test_classic_generation_rejects_same_player_on_both_sides(self) -> None:
        """Старый повреждённый состав не превращается в некорректный матч 2×2."""
        request = self._ready()
        team = request.teams.get(side=TeamSide.OPPONENT)
        team.members.filter(player=self.opponent2).delete()
        DoublesTeamMember.objects.create(team=team, player=self.partner)
        with self.assertRaises(ValueError):
            confirm_match(request.pk, self.author)
        self.assertFalse(request.series_matches.exists())

    def test_reopened_status_cannot_generate_classic_match_twice(self) -> None:
        """Смена статуса администратором не снимает маркер уже созданного матча."""
        request = self._ready()
        confirm_match(request.pk, self.author)
        DoublesMatchRequest.objects.filter(pk=request.pk).update(
            status=DoublesMatchRequestStatus.READY
        )
        with self.assertRaises(ValueError):
            confirm_match(request.pk, self.author)
        self.assertEqual(request.series_matches.count(), 1)

    def test_reopened_status_cannot_generate_team_series_twice(self) -> None:
        """Однократность серии не зависит только от редактируемого статуса заявки."""
        request = self._ready(kind=DoublesMatchKind.TEAM)
        confirm_team_sparring_series(request.pk, self.author)
        DoublesMatchRequest.objects.filter(pk=request.pk).update(
            status=DoublesMatchRequestStatus.READY
        )
        with self.assertRaises(ValueError):
            confirm_team_sparring_series(request.pk, self.author)
        self.assertEqual(request.series_matches.count(), 5)

    def test_generated_roster_cannot_change_after_status_reopen(self) -> None:
        """Уже созданные матчи продолжают ссылаться на исходный состав серии."""
        request = self._ready(kind=DoublesMatchKind.TEAM)
        confirm_team_sparring_series(request.pk, self.author)
        DoublesMatchRequest.objects.filter(pk=request.pk).update(
            status=DoublesMatchRequestStatus.READY
        )
        with self.assertRaises(ValueError):
            remove_team_member(
                match_request_id=request.pk,
                team_side=TeamSide.OPPONENT,
                player_id=self.opponent1.pk,
                removed_by=self.author,
            )
        self.assertEqual(request.teams.get(side=TeamSide.OPPONENT).members.count(), 2)

    def test_deleting_generated_request_preserves_match_and_billing_context(
        self,
    ) -> None:
        """Удаление запрещено, если оно лишит созданные матчи дружеского режима и серии."""
        request = self._ready(kind=DoublesMatchKind.TEAM)
        DoublesMatchRequest.objects.filter(pk=request.pk).update(is_friendly=True)
        matches = confirm_team_sparring_series(request.pk, self.author)
        with self.assertRaises(ValueError):
            delete_match_request(request.pk, self.author)
        self.assertTrue(DoublesMatchRequest.objects.filter(pk=request.pk).exists())
        self.assertTrue(
            all(
                Match.objects.get(pk=match.pk).is_friendly_sparring()
                for match in matches
            )
        )
        self.assertEqual(request.series_matches.count(), 5)

    def test_friendly_team_series_keeps_friendly_context_for_all_five_matches(
        self,
    ) -> None:
        """Дружеский режим относится и к одиночным матчам, а не только к парному."""
        request = self._ready(kind=DoublesMatchKind.TEAM)
        DoublesMatchRequest.objects.filter(pk=request.pk).update(is_friendly=True)
        matches = confirm_team_sparring_series(request.pk, self.author)
        for match in matches:
            with self.subTest(match=match.pk):
                self.assertTrue(Match.objects.get(pk=match.pk).is_friendly_sparring())

    def test_standalone_match_creation_rolls_back_with_notification_failure(
        self,
    ) -> None:
        """Даже прямое сохранение матча не оставляет строку после отказа сигнала."""
        with patch(
            "apps.tournaments.signals.create_notification",
            side_effect=RuntimeError("match inbox failed"),
        ):
            with self.assertRaises(RuntimeError):
                Match.objects.create(
                    match_type=Match.MatchType.SPARRING,
                    player1=self.author,
                    player2=self.opponent1,
                )
        self.assertFalse(Match.objects.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_match_creation_inbox_failure_rolls_back_confirmation(self) -> None:
        """Обязательное уведомление о новом матче не подавляет ошибку сохранения."""
        request = self._ready()
        with patch(
            "apps.tournaments.signals.create_notification",
            side_effect=RuntimeError("match inbox failed"),
        ):
            with self.assertRaises(RuntimeError):
                confirm_match(request.pk, self.author)
        request.refresh_from_db()
        self.assertEqual(request.status, DoublesMatchRequestStatus.READY)
        self.assertIsNone(request.match_id)
        self.assertFalse(request.series_matches.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(mail.outbox)

    def test_last_match_delivery_failure_rolls_back_whole_team_series(self) -> None:
        """Пятый матч не оставляет четыре первых, их уведомления и частичный статус."""
        request = self._ready(kind=DoublesMatchKind.TEAM)
        from apps.telegram_bot.notifications import notify_match_created

        calls = 0

        def notify(match: Match) -> None:
            """Сохранить четыре первых комплекта уведомлений и отказать на парном матче."""
            nonlocal calls
            calls += 1
            if calls == 5:
                raise RuntimeError("last match delivery failed")
            notify_match_created(match)

        with patch(
            "apps.telegram_bot.notifications.notify_match_created", side_effect=notify
        ):
            with self.assertRaises(RuntimeError):
                confirm_team_sparring_series(request.pk, self.author)
        request.refresh_from_db()
        self.assertEqual(request.status, DoublesMatchRequestStatus.READY)
        self.assertIsNone(request.match_id)
        self.assertFalse(request.series_matches.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(mail.outbox)
