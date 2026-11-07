"""Настоящие гонки доставки, доступа и списания на разных соединениях PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from io import StringIO
from queue import Queue
from threading import Barrier, Event
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, transaction
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.notifications.models import Delivery
from apps.sparring.doubles_services import (
    accept_join_request,
    confirm_match,
    confirm_team_sparring_series,
    create_doubles_request,
    create_join_request,
    remove_team_member,
)
from apps.sparring.models import (
    DoublesMatchRequest,
    SparringRequest,
    SparringResponse,
    TeamSide,
)
from apps.sparring.services import cancel_sparring_response, confirm_sparring_response
from apps.subscriptions.fancoin import SPARRING_DOUBLES_COST, SPARRING_TEAM_COST
from apps.subscriptions.models import (
    FancoinTransaction,
    SubscriptionTier,
    UserSubscription,
)
from apps.telegram_bot import views
from apps.telegram_bot.binding import consume_binding_token
from apps.telegram_bot.delivery import enqueue_message, process_telegram_deliveries
from apps.telegram_bot.models import TelegramUpdateReceipt
from apps.tournaments import views as tournament_views
from apps.tournaments.deadline_extensions import (
    approve_deadline_extension,
    request_deadline_extension,
)
from apps.tournaments.models import (
    DeadlineExtensionRequest,
    Match,
    MatchResultProposal,
    SparringDeadlineExtension,
)
from apps.tournaments.overdue import (
    apply_deadline_auto_rt,
    apply_no_show_walkover,
    notify_admins_match_deadline_overdue,
    revert_deadline_auto_rt,
    revert_no_show_walkover,
)
from apps.tournaments.proposal_service import apply_proposal, submit_match_result
from apps.tournaments.sparring_deadlines import (
    extend_sparring_deadline,
    prepare_admin_sparring_save,
)
from apps.users.models import Player, User
from tests.support.factories import (
    make_player,
    make_subscription,
    make_tournament,
    make_user,
)
from tests.support.postgres_concurrency import is_blocked, on_connection

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Нужны блокировки PostgreSQL"
    ),
]


@pytest.fixture(autouse=True)
def _isolated_bot_state() -> Iterator[None]:
    """Не отправлять почту и не переносить состояние ввода между конкурентными тестами."""
    cache.clear()
    with (
        override_settings(
            NOTIFICATION_CENTER_ENABLED=False,
            USER_NOTIFICATIONS_EMAIL_ENABLED=False,
            COMPLETION_NOTIFY_SYNC=True,
            TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
            TELEGRAM_USER_BOT_WEBHOOK_SECRET="concurrency-secret",
        ),
        patch("apps.telegram_bot.services.is_configured", return_value=True),
    ):
        yield
    cache.clear()


def _business_outcome(action: Callable[[], object]) -> bool:
    """Различать ожидаемый отказ старого действия и технический сбой соединения."""
    try:
        action()
    except (ValueError, ValidationError):
        return False
    return True


def _result_match(*, suffix: str, player1: Player | None = None) -> Match:
    """Создать рейтинговый спарринг с привязками и балансами обоих участников."""
    first = player1 or make_player(email_suffix=f"{suffix}-1", points=3000)
    second = make_player(email_suffix=f"{suffix}-2", points=2500)
    for player in (first, second):
        UserTelegramLink.objects.get_or_create(
            user=player.user, defaults={"user_bot_chat_id": 100000 + player.pk}
        )
        if not UserSubscription.objects.filter(user_id=player.user_id).exists():
            make_subscription(player.user, fancoin_balance=20)
    return Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=first,
        player2=second,
        deadline=timezone.now() + timedelta(days=7),
    )


def _match_players(match: Match) -> tuple[Player, Player]:
    """Проверить, что тестовый матч содержит двух обязательных участников."""
    first, second = match.player1, match.player2
    assert first is not None and second is not None
    return first, second


def _submit_result(match: Match) -> MatchResultProposal:
    """Внести настоящий результат, включая рейтинг, списание и обязательные уведомления."""
    player, _ = _match_players(match)
    return submit_match_result(
        match.pk,
        player,
        result=Match.ResultChoice.WIN,
        player1_set1=6,
        player2_set1=4,
    )


def _manual_deadline(match: Match, admin: User, new_deadline: datetime) -> int:
    """Вызвать настоящий endpoint переноса с правами сотрудника."""
    tournament = match.tournament
    assert tournament is not None
    request = RequestFactory().post(
        reverse("tournament_manage_match_deadline", args=[tournament.slug, match.pk]),
        {"deadline": timezone.localtime(new_deadline).date().isoformat()},
    )
    request.user = admin
    with patch("apps.tournaments.views.messages"):
        return tournament_views.tournament_manage_match_deadline(
            request, tournament.slug, match.pk
        ).status_code


@pytest.mark.parametrize("same_player", [False, True])
def test_parallel_self_extensions_add_week_once_per_participant(
    same_player: bool,
) -> None:
    """Два участника добавляют по неделе; повтор одного игрока добавляет только одну."""
    match = _result_match(suffix="self-extension-race")
    original = match.deadline
    assert original is not None
    first, second = _match_players(match)
    players = (first, first if same_player else second)
    start = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(
                on_connection,
                lambda player=player: _business_outcome(
                    lambda: extend_sparring_deadline(match.pk, player)
                ),
                start=start,
            )
            for player in players
        ]
        assert sorted(task.result(timeout=15) for task in tasks) == (
            [False, True] if same_player else [True, True]
        )
    extension_count = 1 if same_player else 2
    match.refresh_from_db()
    assert match.deadline == original + timedelta(days=7 * extension_count)
    assert (
        SparringDeadlineExtension.objects.filter(match=match).count() == extension_count
    )
    assert (
        Delivery.objects.filter(event_key="match.deadline_extended").count()
        == 2 * extension_count
    )


@pytest.mark.parametrize("first_action", ["extension", "result"])
def test_self_extension_and_result_recheck_committed_state(first_action: str) -> None:
    """Результат сохраняет предшествующее продление; продление после результата недоступно."""
    match = _result_match(suffix=f"self-extension-result-{first_action}")
    original_deadline = match.deadline
    assert original_deadline is not None
    player, _ = _match_players(match)
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    original_create = SparringDeadlineExtension.objects.create
    from apps.subscriptions.sparring_billing import charge_fancoin_for_completed_match

    def create(**kwargs: Any) -> SparringDeadlineExtension:
        """Удержать настоящую запись однократности до коммита."""
        extension = original_create(**kwargs)
        entered.set()
        assert release.wait(timeout=10)
        return extension

    def charge(current: Match) -> None:
        """Удержать настоящий результат до коммита."""
        charge_fancoin_for_completed_match(current)
        if first_action == "result":
            entered.set()
            assert release.wait(timeout=10)

    def extend() -> bool:
        """Не скрывать технические ошибки за ожидаемым отказом старого действия."""
        return _business_outcome(lambda: extend_sparring_deadline(match.pk, player))

    def submit() -> MatchResultProposal:
        """Сохранить результат как второе из двух конкурирующих действий."""
        return _submit_result(match)

    first_callback: Callable[[], bool | MatchResultProposal] = (
        extend if first_action == "extension" else submit
    )
    second_callback: Callable[[], bool | MatchResultProposal] = (
        submit if first_action == "extension" else extend
    )
    with (
        patch(
            "apps.tournaments.sparring_deadlines.SparringDeadlineExtension.objects.create",
            side_effect=create,
        ),
        patch(
            "apps.tournaments.proposal_service.charge_fancoin_for_completed_match",
            side_effect=charge,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(on_connection, first_callback)
        try:
            assert entered.wait(timeout=5)
            second = pool.submit(
                on_connection,
                second_callback,
                pid=pid,
            )
            assert is_blocked(second, pid.get(timeout=5))
        finally:
            release.set()
        first.result(timeout=15)
        outcome = second.result(timeout=15)
        if first_action == "result":
            assert outcome is False
    match.refresh_from_db()
    assert match.status == Match.MatchStatus.COMPLETED
    assert match.deadline == original_deadline + timedelta(
        days=7 if first_action == "extension" else 0
    )
    assert SparringDeadlineExtension.objects.filter(match=match).count() == int(
        first_action == "extension"
    )
    assert Player.objects.get(pk=player.pk).matches_played == 1


@pytest.mark.parametrize("first_action", ["deadline", "result"])
def test_manual_deadline_and_result_recheck_committed_state(first_action: str) -> None:
    """Ручной перенос и результат сериализуются; старая форма не открывает сыгранный матч."""
    match = _result_match(suffix=f"manual-deadline-result-{first_action}")
    tournament = make_tournament(
        slug="manual-deadline-race", format="round_robin", status="active"
    )
    Match.objects.filter(pk=match.pk).update(
        tournament=tournament, match_type=Match.MatchType.TOURNAMENT
    )
    match.refresh_from_db()
    player, _ = _match_players(match)
    Match.objects.create(
        tournament=tournament,
        player1=match.player1,
        player2=make_player(email_suffix="manual-deadline-next"),
    )
    admin = make_user(email="manual-deadline-race@test.local", is_staff=True)
    original_deadline = match.deadline
    assert original_deadline is not None
    new_date = timezone.localtime(original_deadline).date() + timedelta(days=7)
    new_deadline = timezone.make_aware(datetime.combine(new_date, time(23, 59, 59)))
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    from apps.subscriptions.sparring_billing import charge_fancoin_for_completed_match
    from apps.tournaments.utils import notify_participants_match_deadline_changed

    def notify(current: Match, **kwargs: Any) -> tuple[int, int]:
        """Удержать реальное сохранение обязательных уведомлений до коммита переноса."""
        counts = notify_participants_match_deadline_changed(current, **kwargs)
        entered.set()
        assert release.wait(timeout=10)
        return counts

    def charge(current: Match) -> None:
        """Удержать результат до коммита, пока ручной endpoint читает старую строку."""
        charge_fancoin_for_completed_match(current)
        if first_action == "result":
            entered.set()
            assert release.wait(timeout=10)

    def deadline() -> int:
        """Запустить ручной перенос с полной проверкой статуса."""
        return _manual_deadline(match, admin, new_deadline)

    def submit() -> MatchResultProposal:
        """Сохранить результат как второе из двух конкурирующих действий."""
        return _submit_result(match)

    first_callback: Callable[[], int | MatchResultProposal] = (
        deadline if first_action == "deadline" else submit
    )
    second_callback: Callable[[], int | MatchResultProposal] = (
        submit if first_action == "deadline" else deadline
    )
    with (
        patch(
            "apps.tournaments.utils.notify_participants_match_deadline_changed",
            side_effect=notify,
        ),
        patch(
            "apps.tournaments.proposal_service.charge_fancoin_for_completed_match",
            side_effect=charge,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(on_connection, first_callback)
        try:
            assert entered.wait(timeout=5)
            second = pool.submit(
                on_connection,
                second_callback,
                pid=pid,
            )
            assert is_blocked(second, pid.get(timeout=5))
        finally:
            release.set()
        first.result(timeout=15)
        second.result(timeout=15)
    match.refresh_from_db()
    assert match.status == Match.MatchStatus.COMPLETED
    assert match.deadline == (
        new_deadline if first_action == "deadline" else original_deadline
    )
    assert Delivery.objects.filter(event_key="match.deadline_changed").count() == (
        2 if first_action == "deadline" else 0
    )
    assert Player.objects.get(pk=player.pk).matches_played == 1


def test_old_admin_deadline_form_waits_for_result_and_preserves_new_fields() -> None:
    """Подготовка старой административной формы сохраняет результат после ожидания его коммита."""
    match = _result_match(suffix="old-admin-deadline-race")
    first_player, _ = _match_players(match)
    stale = Match.objects.get(pk=match.pk)
    original_deadline = stale.deadline
    assert original_deadline is not None
    stale.deadline = original_deadline + timedelta(days=7)
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    from apps.subscriptions.sparring_billing import charge_fancoin_for_completed_match

    def charge(current: Match) -> None:
        """Задержать результат после реального расчёта рейтинга."""
        charge_fancoin_for_completed_match(current)
        entered.set()
        assert release.wait(timeout=10)

    def save() -> None:
        """Сохранить форму с единственным изменённым полем под общей транзакцией."""
        with transaction.atomic():
            prepare_admin_sparring_save(stale, {"deadline"})
            stale.save()

    with (
        patch(
            "apps.tournaments.proposal_service.charge_fancoin_for_completed_match",
            side_effect=charge,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        result = pool.submit(on_connection, lambda: _submit_result(match))
        try:
            assert entered.wait(timeout=5)
            admin_save = pool.submit(on_connection, save, pid=pid)
            assert is_blocked(admin_save, pid.get(timeout=5))
        finally:
            release.set()
        result.result(timeout=15)
        admin_save.result(timeout=15)
    match.refresh_from_db()
    player = Player.objects.get(pk=first_player.pk)
    assert match.status == Match.MatchStatus.COMPLETED
    assert (match.player1_set1, match.player2_set1) == (6, 4)
    assert match.deadline == stale.deadline
    assert match.rating_status == Match.RatingCalcStatus.CALCULATED
    assert player.total_points == pytest.approx(3000 + match.rating_delta_player1)
    assert (player.matches_played, player.matches_won) == (1, 1)


@pytest.mark.parametrize("direct_save", [False, True])
def test_result_locks_next_round_before_shared_players(direct_save: bool) -> None:
    """Ожидание следующего матча не удерживает общих игроков, нужных его собственному результату."""
    match = _result_match(suffix="next-round-lock-order")
    player, _ = _match_players(match)
    tournament = make_tournament(
        slug="next-round-lock-order", status="active", format="single_elimination"
    )
    parent = Match.objects.create(
        tournament=tournament,
        player1=match.player1,
        player2=make_player(email_suffix="next-round-other"),
        round_index=3,
    )
    Match.objects.filter(pk=match.pk).update(
        tournament=tournament,
        match_type=Match.MatchType.TOURNAMENT,
        next_match=parent,
        round_index=2,
        round_order=1,
    )
    match.refresh_from_db()
    pid: Queue[int] = Queue()

    def save() -> None:
        """Использовать общий сервис или настоящее сохранение административного результата."""
        if direct_save:
            current = Match.objects.get(pk=match.pk)
            current.winner = current.player1
            current.player1_set1, current.player2_set1 = 6, 4
            current.status = Match.MatchStatus.COMPLETED
            current.save()
        else:
            _submit_result(match)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Match.objects.select_for_update(no_key=True).get(pk=parent.pk)
            result = pool.submit(on_connection, save, pid=pid)
            assert is_blocked(result, pid.get(timeout=5))
            Player.objects.select_for_update(nowait=True).get(pk=player.pk)
        result.result(timeout=15)


def _disconnect(user: User) -> int:
    """Вызвать настоящее отключение бота без middleware сообщений."""
    request = RequestFactory().post("/telegram/disconnect/")
    request.user = user
    with patch("apps.telegram_bot.views.messages"):
        return views.disconnect_user_bot(request).status_code


def _overdue_action(match: Match, action: str) -> bool:
    """Вызвать настоящий игровой вход и отличить бизнес-отказ от сбоя БД."""
    first, second = _match_players(match)
    if action == "cron":
        return notify_admins_match_deadline_overdue(match)[0]
    if action == "auto_rt":
        return _business_outcome(lambda: apply_deadline_auto_rt(match, notify=False))
    if action == "legacy_fan":
        from apps.tournaments.fan import apply_overdue_walkover

        return _business_outcome(lambda: apply_overdue_walkover(match, first))
    if action == "legacy_round_robin":
        from apps.tournaments.round_robin import apply_overdue_walkover_round_robin

        return _business_outcome(
            lambda: apply_overdue_walkover_round_robin(match, first)
        )
    return _business_outcome(
        lambda: apply_no_show_walkover(match, loser=second, notify=False)
    )


@pytest.mark.parametrize(
    "action", ["no_show", "auto_rt", "cron", "legacy_fan", "legacy_round_robin"]
)
def test_overdue_action_waits_for_played_result_and_refuses_stale_match(
    action: str,
) -> None:
    """Результат удерживает матч; неявка/cron не могут заменить его после ожидания."""
    match = _result_match(suffix=f"overdue-played-race-{action}")
    if action.startswith("legacy_"):
        tournament = make_tournament(
            slug=f"overdue-legacy-{action}",
            format=(
                "round_robin"
                if action == "legacy_round_robin"
                else "single_elimination"
            ),
            status="active",
            bracket_generated=True,
        )
        Match.objects.filter(pk=match.pk).update(
            tournament=tournament, match_type=Match.MatchType.TOURNAMENT
        )
    Match.objects.filter(pk=match.pk).update(
        deadline=timezone.now() - timedelta(hours=1)
    )
    match.refresh_from_db()
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    from apps.subscriptions.sparring_billing import charge_fancoin_for_completed_match

    def charge(current: Match) -> None:
        """Задержать результат после реального расчёта рейтинга до коммита."""
        entered.set()
        assert release.wait(timeout=10)
        charge_fancoin_for_completed_match(current)

    with (
        patch(
            "apps.tournaments.proposal_service.charge_fancoin_for_completed_match",
            side_effect=charge,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        result = pool.submit(on_connection, lambda: _submit_result(match))
        try:
            assert entered.wait(timeout=5)
            overdue = pool.submit(
                on_connection, lambda: _overdue_action(match, action), pid=pid
            )
            assert is_blocked(overdue, pid.get(timeout=5))
        finally:
            release.set()
        result.result(timeout=15)
        assert not overdue.result(timeout=15)
    match.refresh_from_db()
    assert match.status == Match.MatchStatus.COMPLETED
    assert (match.player1_set1, match.player2_set1) == (6, 4)
    assert list(
        Player.objects.order_by("pk").values_list("matches_played", flat=True)
    ) == [1, 1]


@pytest.mark.parametrize("action", ["auto_rt", "cron"])
def test_overdue_action_waits_for_extension_and_rechecks_new_deadline(
    action: str,
) -> None:
    """Дедлайн, продлённый до коммита, исключает последующее действие старого cron."""
    match = _result_match(suffix=f"overdue-extension-race-{action}")
    Match.objects.filter(pk=match.pk).update(
        deadline=timezone.now() - timedelta(hours=1)
    )
    match.refresh_from_db()
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    original = SparringDeadlineExtension.objects.create

    def create(**kwargs: Any) -> SparringDeadlineExtension:
        """Удержать продление до коммита его отметки однократности."""
        extension = original(**kwargs)
        entered.set()
        assert release.wait(timeout=10)
        return extension

    with (
        patch(
            "apps.tournaments.sparring_deadlines.SparringDeadlineExtension.objects.create",
            side_effect=create,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        extension = pool.submit(
            on_connection, lambda: extend_sparring_deadline(match.pk, match.player1)
        )
        try:
            assert entered.wait(timeout=5)
            overdue = pool.submit(
                on_connection, lambda: _overdue_action(match, action), pid=pid
            )
            assert is_blocked(overdue, pid.get(timeout=5))
        finally:
            release.set()
        extension.result(timeout=15)
        assert not overdue.result(timeout=15)
    match.refresh_from_db()
    assert match.status == Match.MatchStatus.SCHEDULED
    assert match.deadline is not None
    assert match.deadline > timezone.now()
    assert not Delivery.objects.filter(event_key="match.result_confirmed").exists()


@pytest.mark.parametrize("auto_rt", [False, True])
def test_parallel_overdue_reverts_restore_effects_once(auto_rt: bool) -> None:
    """Две копии неявки или RT не возвращают рейтинг и статистику дважды."""
    match = _result_match(suffix=f"overdue-revert-race-{auto_rt}")
    Match.objects.filter(pk=match.pk).update(
        deadline=timezone.now() - timedelta(hours=1)
    )
    match.refresh_from_db()
    if auto_rt:
        apply_deadline_auto_rt(match, notify=False)
    else:
        apply_no_show_walkover(match, loser=match.player2, notify=False)
    stale = Match.objects.get(pk=match.pk)
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    revert = revert_deadline_auto_rt if auto_rt else revert_no_show_walkover

    def sync(*args: Any, **kwargs: Any) -> None:
        """Остановить первый откат после реального возврата рейтинга и статуса."""
        entered.set()
        assert release.wait(timeout=10)

    with (
        patch(
            "apps.tournaments.overdue._sync_bracket_after_walkover_edit",
            side_effect=sync,
        ) as bracket,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(on_connection, lambda: revert(match))
        try:
            assert entered.wait(timeout=5)
            second = pool.submit(
                on_connection, lambda: _business_outcome(lambda: revert(stale)), pid=pid
            )
            assert is_blocked(second, pid.get(timeout=5))
        finally:
            release.set()
        first.result(timeout=15)
        assert not second.result(timeout=15)
    bracket.assert_called_once()
    assert list(
        Player.objects.order_by("pk").values_list("total_points", flat=True)
    ) == [3000, 2500]
    assert list(
        Player.objects.order_by("pk").values_list("matches_played", flat=True)
    ) == [0, 0]
    assert Match.objects.get(pk=match.pk).status == Match.MatchStatus.SCHEDULED


@pytest.mark.parametrize("change", ["disconnect", "deactivate"])
def test_delivery_keeps_authorization_locked_until_network_finishes(
    change: str,
) -> None:
    """Отключение или деактивация не фиксируются посередине разрешённой отправки."""
    player = make_player(email_suffix=f"delivery-race-{change}")
    UserTelegramLink.objects.create(user=player.user, user_bot_chat_id=1001)
    item = enqueue_message(player.user, "Персональный контекст матча")
    assert item is not None
    sending, release = Event(), Event()
    pid: Queue[int] = Queue()

    def send(*args: Any, **kwargs: Any) -> int:
        """Удержать внешний запрос открытым до проверки блокировки второго соединения."""
        sending.set()
        assert release.wait(timeout=10)
        return 71

    def mutate() -> int:
        """Использовать реальный endpoint либо обновление активности пользователя."""
        if change == "disconnect":
            return _disconnect(player.user)
        return User.objects.filter(pk=player.user_id).update(is_active=False)

    with (
        patch("apps.telegram_bot.services.is_configured", return_value=True),
        patch(
            "apps.telegram_bot.services.send_message_or_raise", side_effect=send
        ) as network,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        sender = pool.submit(
            on_connection, lambda: process_telegram_deliveries(limit=1)
        )
        try:
            assert sending.wait(timeout=5)
            mutation = pool.submit(on_connection, mutate, pid=pid)
            assert is_blocked(mutation, pid.get(timeout=5))
        finally:
            release.set()
        assert sender.result(timeout=15) == 1
        mutation.result(timeout=15)
    network.assert_called_once()
    item.refresh_from_db()
    assert item.status == "accepted"
    assert item.provider_id == "71"


def _join_webhook() -> int:
    """Отправить настоящую заявку закрытого чата с проверенным секретом."""
    request = RequestFactory().post(
        "/telegram/user-bot-webhook/",
        data=json.dumps(
            {
                "update_id": 1,
                "chat_join_request": {
                    "chat": {"id": -100200, "type": "supergroup"},
                    "from": {"id": 1001},
                    "user_chat_id": 1001,
                    "date": 1,
                },
            }
        ),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="concurrency-secret",
    )
    return views.user_bot_webhook(request).status_code


@pytest.mark.parametrize("change", ["subscription", "tier"])
@override_settings(
    TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True,
    TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID="-100100",
    TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID="-100200",
    TELEGRAM_USER_BOT_WEBHOOK_SECRET="concurrency-secret",
)
def test_private_approval_locks_subscription_and_tier_during_network(
    change: str,
) -> None:
    """Подписка или тариф не отзываются между проверкой доступа и одобрением заявки."""
    player = make_player(email_suffix=f"private-approval-{change}")
    UserTelegramLink.objects.create(user=player.user, user_bot_chat_id=1001)
    subscription = make_subscription(
        player.user, tier_kwargs={"has_private_chat": True}
    )
    resolving, release = Event(), Event()
    pid: Queue[int] = Queue()

    def resolve(chat_id: int, *, approve: bool) -> bool:
        """Приостановить только одобрение проверенного подписчика."""
        assert approve and chat_id == 1001
        resolving.set()
        assert release.wait(timeout=10)
        return True

    def revoke() -> int:
        """Отозвать право тем же обновлением, которое используется админкой."""
        if change == "subscription":
            return UserSubscription.objects.filter(pk=subscription.pk).update(
                is_active=False
            )
        return SubscriptionTier.objects.filter(pk=subscription.tier_id).update(
            has_private_chat=False
        )

    with (
        patch(
            "apps.telegram_bot.services.resolve_private_chat_join_request",
            side_effect=resolve,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        approval = pool.submit(on_connection, _join_webhook)
        try:
            assert resolving.wait(timeout=5)
            revocation = pool.submit(on_connection, revoke, pid=pid)
            assert is_blocked(revocation, pid.get(timeout=5))
        finally:
            release.set()
        assert approval.result(timeout=15) == 200
        assert revocation.result(timeout=15) == 1


@override_settings(
    TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True,
    TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID="-100100",
    TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID="-100200",
)
def test_private_removal_locks_renewal_during_network() -> None:
    """Продление, начатое после решения об удалении, ждёт завершения этого решения."""
    player = make_player(email_suffix="private-removal-race")
    UserTelegramLink.objects.create(user=player.user, user_bot_chat_id=1001)
    subscription = make_subscription(
        player.user, tier_kwargs={"has_private_chat": True}
    )
    UserSubscription.objects.filter(pk=subscription.pk).update(is_active=False)
    removing, release = Event(), Event()
    pid: Queue[int] = Queue()

    def kick(chat_id: int) -> bool:
        """Удержать удаление после финальной проверки доступа."""
        assert chat_id == 1001
        removing.set()
        assert release.wait(timeout=10)
        return True

    with (
        patch("apps.telegram_bot.services.is_configured", return_value=True),
        patch(
            "apps.telegram_bot.services.get_private_chat_member_status",
            return_value="member",
        ),
        patch("apps.telegram_bot.services.kick_from_private_chat", side_effect=kick),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        removal = pool.submit(
            on_connection,
            lambda: call_command("sync_private_chat_access", stdout=StringIO()),
        )
        try:
            assert removing.wait(timeout=5)
            renewal = pool.submit(
                on_connection,
                lambda: UserSubscription.objects.filter(pk=subscription.pk).update(
                    is_active=True
                ),
                pid=pid,
            )
            assert is_blocked(renewal, pid.get(timeout=5))
        finally:
            release.set()
        removal.result(timeout=15)
        assert renewal.result(timeout=15) == 1


def _ready_team(*, kind: str = "team") -> DoublesMatchRequest:
    """Создать четыре разных игрока с балансами и готовым составом."""
    players = [make_player(email_suffix=f"series-race-{index}") for index in range(4)]
    for player in players:
        make_subscription(player.user, fancoin_balance=20)
    request = create_doubles_request(
        created_by=players[0], partner=players[1], kind=kind
    )
    join = create_join_request(
        match_request_id=request.pk,
        created_by=players[2],
        target_side=TeamSide.OPPONENT,
        players=players[2:],
    )
    accept_join_request(
        match_request_id=request.pk, join_request_id=join.pk, accepted_by=players[0]
    )
    return request


@override_settings(
    NOTIFICATION_CENTER_ENABLED=False, USER_NOTIFICATIONS_EMAIL_ENABLED=False
)
def test_different_results_of_one_series_lock_series_before_match_and_players() -> None:
    """Два результата с непересекающимися игроками делят общую блокировку списания серии."""
    request = _ready_team()
    matches = confirm_team_sparring_series(request.pk, request.created_by)
    # Первый и четвёртый одиночные матчи имеют непересекающиеся пары игроков.
    first, other = matches[0], matches[3]
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    from apps.subscriptions.sparring_billing import _charge_team_series

    def charge(match: Match) -> None:
        """Остановить первый результат до чтения журнала, сохранив его игровые блокировки."""
        if match.pk == first.pk:
            entered.set()
            assert release.wait(timeout=10)
        _charge_team_series(match)

    def submit(match: Match) -> int:
        """Применить настоящий результат и рейтинг, не подменяя бизнес-сервисы."""
        return _submit_result(match).pk

    with (
        patch(
            "apps.subscriptions.sparring_billing._charge_team_series",
            side_effect=charge,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        result1 = pool.submit(on_connection, lambda: submit(first))
        try:
            assert entered.wait(timeout=5)
            result2 = pool.submit(on_connection, lambda: submit(other), pid=pid)
            assert is_blocked(result2, pid.get(timeout=5))
            assert Match.objects.get(pk=other.pk).status == Match.MatchStatus.SCHEDULED
        finally:
            release.set()
        assert result1.result(timeout=15) != result2.result(timeout=15)
    charges = FancoinTransaction.objects.filter(
        doubles_request=request, reason=FancoinTransaction.Reason.SPARRING_TEAM
    )
    assert charges.count() == 4
    assert (
        list(
            UserSubscription.objects.order_by("user_id").values_list(
                "fancoin_balance", flat=True
            )
        )
        == [20 - SPARRING_TEAM_COST] * 4
    )


@override_settings(
    NOTIFICATION_CENTER_ENABLED=False, USER_NOTIFICATIONS_EMAIL_ENABLED=False
)
def test_classic_request_context_does_not_charge_team_series_price() -> None:
    """Прямая связь парного матча с заявкой сохраняет контекст, но не меняет цену на командную."""
    request = _ready_team(kind="classic")
    match = confirm_match(request.pk, request.created_by)
    submit_match_result(
        match.pk,
        request.created_by,
        result=Match.ResultChoice.WIN,
        player1_set1=6,
        player2_set1=4,
    )
    charges = FancoinTransaction.objects.all()
    assert charges.count() == 4
    assert set(charges.values_list("reason", flat=True)) == {
        FancoinTransaction.Reason.SPARRING_DOUBLES
    }
    assert (
        list(
            UserSubscription.objects.order_by("user_id").values_list(
                "fancoin_balance", flat=True
            )
        )
        == [20 - SPARRING_DOUBLES_COST] * 4
    )


def test_parallel_replay_of_one_proposal_applies_effects_once() -> None:
    """Две копии одной заявки на разных соединениях не повторяют рейтинг и списание."""
    match = _result_match(suffix="one-proposal-race")
    player, _ = _match_players(match)
    proposal = MatchResultProposal.objects.create(
        match=match,
        proposer=player,
        result=Match.ResultChoice.WIN,
        player1_set1=6,
        player2_set1=4,
    )
    start = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(
                on_connection,
                lambda: apply_proposal(MatchResultProposal.objects.get(pk=proposal.pk)),
                start=start,
            )
            for _ in range(2)
        ]
        assert sorted(task.result(timeout=15) for task in results) == [False, True]
    assert MatchResultProposal.objects.filter(applied_at__isnull=False).count() == 1
    assert list(
        Player.objects.order_by("pk").values_list("matches_played", flat=True)
    ) == [1, 1]
    assert FancoinTransaction.objects.filter(match=match).count() == 2
    assert Delivery.objects.filter(event_key="match.result_confirmed").count() == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_competing_results_of_one_match_leave_one_applied_result(legacy: bool) -> None:
    """Два новых ввода или две старые заявки не оставляют второй результат и побочные эффекты."""
    match = _result_match(suffix="competing-results")
    player, _ = _match_players(match)
    proposals = (
        [
            MatchResultProposal.objects.create(
                match=match,
                proposer=player,
                result=Match.ResultChoice.WIN,
                player1_set1=6,
                player2_set1=games,
            )
            for games in (3, 4)
        ]
        if legacy
        else []
    )
    start = Barrier(2)

    def act(index: int) -> bool:
        """Ожидаемый отказ проигравшего гонку не маскирует ошибку PostgreSQL."""
        return _business_outcome(
            lambda: (
                apply_proposal(proposals[index]) if legacy else _submit_result(match)
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(on_connection, lambda index=index: act(index), start=start)
            for index in range(2)
        ]
        assert sorted(task.result(timeout=15) for task in tasks) == [False, True]
    assert match.result_proposals.filter(applied_at__isnull=False).count() == 1
    assert match.result_proposals.count() == (2 if legacy else 1)
    if legacy:
        assert (
            match.result_proposals.filter(status=Match.ProposalStatus.REJECTED).count()
            == 1
        )
    assert list(
        Player.objects.order_by("pk").values_list("matches_played", flat=True)
    ) == [1, 1]
    assert FancoinTransaction.objects.filter(match=match).count() == 2
    assert Delivery.objects.filter(event_key="match.result_confirmed").count() == 2


@pytest.mark.parametrize("direct_save", [False, True])
def test_different_matches_keep_shared_player_rating_and_stats(
    direct_save: bool,
) -> None:
    """Сохранение из админки и общий сервис перечитывают рейтинг после ожидания общего игрока."""
    first = _result_match(suffix="shared-first")
    shared_player, _ = _match_players(first)
    other = _result_match(suffix="shared-second", player1=shared_player)
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    from apps.tournaments.signals import _apply_fan_shadow

    def rate(match: Match) -> None:
        """Удержать первый расчёт после обновления статистики и рейтинга игрока."""
        _apply_fan_shadow(match)
        if match.pk == first.pk:
            entered.set()
            assert release.wait(timeout=10)

    def act(match: Match) -> object:
        """Вызвать настоящий путь сохранения без подмены расчёта рейтинга."""
        if not direct_save:
            return _submit_result(match)
        current = Match.objects.get(pk=match.pk)
        current.player1_set1, current.player2_set1 = 6, 4
        current.winner = current.player1
        current.status = Match.MatchStatus.COMPLETED
        current.rating_status = Match.RatingCalcStatus.PENDING
        current.save()
        return current.pk

    with (
        patch("apps.tournaments.signals._apply_fan_shadow", side_effect=rate),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        result1 = pool.submit(on_connection, lambda: act(first))
        try:
            assert entered.wait(timeout=5)
            result2 = pool.submit(on_connection, lambda: act(other), pid=pid)
            assert is_blocked(result2, pid.get(timeout=5))
        finally:
            release.set()
        result1.result(timeout=15)
        result2.result(timeout=15)
    shared = Player.objects.get(pk=shared_player.pk)
    first.refresh_from_db()
    other.refresh_from_db()
    assert shared.matches_played == 2
    assert shared.matches_won == 2
    assert shared.total_points == pytest.approx(
        3000 + first.rating_delta_player1 + other.rating_delta_player1
    )
    if not direct_save:
        assert FancoinTransaction.objects.filter(user_id=shared.user_id).count() == 2
        assert (
            UserSubscription.objects.get(user_id=shared.user_id).fancoin_balance == 18
        )


def test_parallel_direct_saves_of_same_score_do_not_repeat_stats() -> None:
    """Старая копия pending не обходит уже рассчитанный результат при прямом сохранении."""
    match = _result_match(suffix="direct-same-score")
    copies = [Match.objects.get(pk=match.pk) for _ in range(2)]
    for copy in copies:
        copy.player1_set1, copy.player2_set1 = 6, 4
        copy.winner = copy.player1
        copy.status = Match.MatchStatus.COMPLETED
        copy.rating_status = Match.RatingCalcStatus.PENDING
    entered, release = Event(), Event()
    pid: Queue[int] = Queue()
    from apps.tournaments.signals import _apply_fan_shadow

    def rate(current: Match) -> None:
        """Приостановить первый расчёт, оставив второе сохранение ждать строку матча."""
        _apply_fan_shadow(current)
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=10)

    with (
        patch("apps.tournaments.signals._apply_fan_shadow", side_effect=rate),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(on_connection, copies[0].save)
        try:
            assert entered.wait(timeout=5)
            second = pool.submit(on_connection, copies[1].save, pid=pid)
            assert is_blocked(second, pid.get(timeout=5))
        finally:
            release.set()
        first.result(timeout=15)
        second.result(timeout=15)
    assert list(
        Player.objects.order_by("pk").values_list("matches_played", flat=True)
    ) == [1, 1]
    assert (
        Match.objects.get(pk=match.pk).rating_status
        == Match.RatingCalcStatus.CALCULATED
    )


@pytest.mark.parametrize(
    "action",
    [
        "self",
        "admin",
        "no_show",
        "auto_rt",
        "cron",
        "revert_no_show",
        "revert_auto_rt",
        "approval",
    ],
)
def test_team_deadline_actions_lock_request_before_match(action: str) -> None:
    """Продление, неявка, RT и их откаты не захватывают матч раньше исходной заявки серии."""
    request = _ready_team()
    match = confirm_team_sparring_series(request.pk, request.created_by)[0]
    player, _ = _match_players(match)
    if action == "cron":
        make_user(email="team-deadline-cron-admin@test.local", is_staff=True)
    if action not in ("self", "admin", "approval"):
        Match.objects.filter(pk=match.pk).update(
            deadline=timezone.now() - timedelta(hours=1)
        )
        match.refresh_from_db()
    if action == "revert_no_show":
        apply_no_show_walkover(match, loser=match.player2, notify=False)
    elif action == "revert_auto_rt":
        apply_deadline_auto_rt(match, notify=False)
    extension_id = (
        request_deadline_extension(match.pk, player)[0].pk
        if action == "approval"
        else None
    )
    pid: Queue[int] = Queue()

    def extend() -> None:
        """Использовать оба настоящих пути изменения дедлайна."""
        if action == "self":
            extend_sparring_deadline(match.pk, match.player1)
        elif action == "admin":
            with transaction.atomic():
                prepare_admin_sparring_save(match, {"deadline"})
        elif action == "revert_no_show":
            revert_no_show_walkover(match)
        elif action == "revert_auto_rt":
            revert_deadline_auto_rt(match)
        elif action == "approval":
            assert extension_id is not None
            approve_deadline_extension(extension_id)
        else:
            assert _overdue_action(match, action)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            DoublesMatchRequest.objects.select_for_update().get(pk=request.pk)
            extension = pool.submit(on_connection, extend, pid=pid)
            assert is_blocked(extension, pid.get(timeout=5))
            # Поток должен ждать заявку, ещё не удерживая сам матч.
            Match.objects.select_for_update(nowait=True).get(pk=match.pk)
        extension.result(timeout=15)


def test_parallel_extension_approval_adds_day_once() -> None:
    """Две админские команды не продлевают один запрос на два дня."""
    match = _result_match(suffix="approval-race")
    original = match.deadline
    assert original is not None
    player, _ = _match_players(match)
    extension, created = request_deadline_extension(match.pk, player)
    assert created
    start = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(
                on_connection,
                lambda: approve_deadline_extension(extension.pk),
                start=start,
            )
            for _ in range(2)
        ]
        assert sorted(task.result(timeout=15) for task in tasks) == [False, True]
    match.refresh_from_db()
    assert match.deadline == original + timedelta(days=1)
    assert (
        DeadlineExtensionRequest.objects.get(pk=extension.pk).status
        == DeadlineExtensionRequest.Status.APPROVED
    )
    assert Delivery.objects.filter(event_key="match.deadline_extended").count() == 1


def test_parallel_extension_requests_keep_one_pending_record() -> None:
    """Повторные запросы одного участника сохраняют единственную историю ожидания."""
    match = _result_match(suffix="extension-request-race")
    player, _ = _match_players(match)
    start = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(
                on_connection,
                lambda: request_deadline_extension(match.pk, player),
                start=start,
            )
            for _ in range(2)
        ]
        results = [task.result(timeout=15) for task in tasks]
    assert results[0][0].pk == results[1][0].pk
    assert sorted(created for _, created in results) == [False, True]
    assert DeadlineExtensionRequest.objects.filter(match=match).count() == 1


@pytest.mark.parametrize("kind", ["same_token", "same_chat"])
def test_parallel_binding_tokens_cannot_be_consumed_twice(kind: str) -> None:
    """Один токен или один Telegram ID не привязывают сразу два разных аккаунта."""
    first = make_player(email_suffix="binding-race-first")
    link = UserTelegramLink.objects.create(user=first.user)
    token = link.get_or_create_binding_token()
    if kind == "same_chat":
        second = make_player(email_suffix="binding-race-second")
        other = UserTelegramLink.objects.create(user=second.user)
        attempts = [(token, 5001), (other.get_or_create_binding_token(), 5001)]
    else:
        attempts = [(token, 5001), (token, 5002)]
    start = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(
                on_connection,
                lambda token=token, chat=chat: consume_binding_token(token, chat),
                start=start,
            )
            for token, chat in attempts
        ]
        assert sorted(task.result(timeout=15)[0] for task in tasks) == [False, True]
    assert UserTelegramLink.objects.filter(user_bot_chat_id__isnull=False).count() == 1
    assert UserTelegramLink.objects.filter(binding_token__isnull=True).count() == 1


def test_binding_token_expiration_is_rechecked_after_waiting_for_user() -> None:
    """Токен, истёкший в ожидании блокировки, не может привязать аккаунт."""
    player = make_player(email_suffix="binding-expiry-wait")
    now = timezone.now()
    link = UserTelegramLink.objects.create(
        user=player.user,
        binding_token="waiting-token",
        token_created_at=now - timedelta(hours=23, minutes=59),
    )
    clock = [now]
    pid: Queue[int] = Queue()
    with (
        patch("apps.telegram_bot.binding.timezone.now", side_effect=lambda: clock[0]),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        with transaction.atomic():
            User.objects.select_for_update(no_key=True).get(pk=player.user_id)
            consumption = pool.submit(
                on_connection,
                lambda: consume_binding_token("waiting-token", 5001),
                pid=pid,
            )
            assert is_blocked(consumption, pid.get(timeout=5))
            clock[0] += timedelta(minutes=2)
        assert consumption.result(timeout=15)[0] is False
    link.refresh_from_db()
    assert link.user_bot_chat_id is None
    assert link.binding_token == "waiting-token"


@pytest.mark.parametrize("first_reply_fails", [False, True])
def test_parallel_webhook_duplicate_commits_once_or_retries_rollback(
    first_reply_fails: bool,
) -> None:
    """Повтор update ждёт первый коммит; после 503 получает право повторить откатившееся действие."""
    match = _result_match(suffix="webhook-duplicate-race")
    player, _ = _match_players(match)
    chat_id = UserTelegramLink.objects.get(user_id=player.user_id).user_bot_chat_id
    assert chat_id is not None
    cache.set(views.CACHE_KEY_RESULT_ENTRY % chat_id, match.pk)
    data = {
        "update_id": 73,
        "message": {
            "message_id": 1,
            "date": 1,
            "from": {"id": chat_id},
            "chat": {"id": chat_id, "type": "private"},
            "text": "6:4",
        },
    }
    replying, release = Event(), Event()
    pid: Queue[int] = Queue()
    replies = 0

    def reply(*args: Any, **kwargs: Any) -> tuple[int, bool]:
        """Остановить обязательный ответ после сохранения результата, но до коммита webhook."""
        nonlocal replies
        replies += 1
        if replies == 1:
            replying.set()
            assert release.wait(timeout=10)
            if first_reply_fails:
                return 0, False
        return 1, True

    def webhook() -> int:
        """Вызвать endpoint с полной проверкой секрета и настоящим диспетчером."""
        request = RequestFactory().post(
            "/telegram/user-bot-webhook/",
            data=json.dumps(data),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="concurrency-secret",
        )
        return views.user_bot_webhook(request).status_code

    with (
        patch("apps.telegram_bot.services.is_configured", return_value=True),
        patch("apps.telegram_bot.services.send_message", side_effect=reply),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(on_connection, webhook)
        try:
            assert replying.wait(timeout=5)
            second = pool.submit(on_connection, webhook, pid=pid)
            assert is_blocked(second, pid.get(timeout=5))
        finally:
            release.set()
        assert first.result(timeout=15) == (503 if first_reply_fails else 200)
        assert second.result(timeout=15) == 200
    assert replies == (2 if first_reply_fails else 1)
    assert TelegramUpdateReceipt.objects.count() == 1
    assert match.result_proposals.count() == 1
    assert FancoinTransaction.objects.filter(match=match).count() == 2
    assert list(
        Player.objects.order_by("pk").values_list("matches_played", flat=True)
    ) == [1, 1]
    assert Delivery.objects.filter(event_key="match.result_confirmed").count() == 2


def test_parallel_sparring_accept_and_cancel_preserve_created_match() -> None:
    """Отмена отклика ждёт принятие и не отменяет уже созданный матч."""
    author = make_player(email_suffix="single-accept-author")
    opponent = make_player(email_suffix="single-accept-opponent")
    make_subscription(author.user, tier_kwargs={"has_sparring": True})
    request = SparringRequest.objects.create(player=author, city="Тула")
    response = SparringResponse.objects.create(
        sparring_request=request, respondent=opponent
    )
    creating, release = Event(), Event()
    pid: Queue[int] = Queue()
    from apps.sparring.services import create_match_from_response

    def create(current: SparringResponse) -> Match:
        """Удержать принятие после фиксации матча внутри ещё не завершённой транзакции."""
        match = create_match_from_response(current)
        creating.set()
        assert release.wait(timeout=10)
        return match

    with (
        patch("apps.sparring.services.create_match_from_response", side_effect=create),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        acceptance = pool.submit(
            on_connection,
            lambda: confirm_sparring_response(response.pk, author.user_id),
        )
        try:
            assert creating.wait(timeout=5)
            cancellation = pool.submit(
                on_connection,
                lambda: _business_outcome(
                    lambda: cancel_sparring_response(response.pk, opponent.user_id)
                ),
                pid=pid,
            )
            assert is_blocked(cancellation, pid.get(timeout=5))
        finally:
            release.set()
        assert acceptance.result(timeout=15).pk
        assert cancellation.result(timeout=15) is False
    response.refresh_from_db()
    assert response.status == SparringResponse.ResponseStatus.ACCEPTED
    assert response.matches.count() == 1


def test_parallel_team_generation_and_roster_change_keep_full_series() -> None:
    """Удаление участника ждёт генерацию и не разрушает контекст пяти созданных матчей."""
    request = _ready_team()
    creating, release = Event(), Event()
    pid: Queue[int] = Queue()
    from apps.sparring.doubles_services import _create_team_sparring_singles

    def create(**kwargs: Any) -> list[Match]:
        """Остановить создание между четырьмя одиночными и последним парным матчем."""
        matches = _create_team_sparring_singles(**kwargs)
        creating.set()
        assert release.wait(timeout=10)
        return matches

    opponent = request.teams.get(side=TeamSide.OPPONENT).members.order_by("pk").first()
    assert opponent is not None
    opponent_id = opponent.player_id
    with (
        patch(
            "apps.sparring.doubles_services._create_team_sparring_singles",
            side_effect=create,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        generation = pool.submit(
            on_connection,
            lambda: confirm_team_sparring_series(request.pk, request.created_by),
        )
        try:
            assert creating.wait(timeout=5)
            removal = pool.submit(
                on_connection,
                lambda: _business_outcome(
                    lambda: remove_team_member(
                        match_request_id=request.pk,
                        team_side=TeamSide.OPPONENT,
                        player_id=opponent_id,
                        removed_by=request.created_by,
                    )
                ),
                pid=pid,
            )
            assert is_blocked(removal, pid.get(timeout=5))
        finally:
            release.set()
        assert len(generation.result(timeout=15)) == 5
        assert removal.result(timeout=15) is False
    assert request.series_matches.count() == 5
    assert request.teams.get(side=TeamSide.OPPONENT).members.count() == 2


@pytest.mark.parametrize("lease_expires", [False, True])
def test_parallel_workers_share_sender_lease_without_duplicate_network_calls(
    lease_expires: bool,
) -> None:
    """Активная отправка защищена и после истечения аренды; восстановление её не повторяет."""
    match = _result_match(suffix="parallel-workers")
    player1, player2 = _match_players(match)
    Delivery.objects.all().delete()
    first = enqueue_message(player1.user, "Первый адресат")
    second = enqueue_message(player2.user, "Второй адресат")
    assert first is not None and second is not None
    clock = [timezone.now()]
    sending, release = Event(), Event()

    def send(*args: Any, **kwargs: Any) -> int:
        """Оставить первую отправку незавершённой для запуска второго worker."""
        sending.set()
        assert release.wait(timeout=10)
        return 71

    with (
        patch("apps.telegram_bot.services.is_configured", return_value=True),
        patch(
            "apps.telegram_bot.services.send_message_or_raise", side_effect=send
        ) as network,
        patch("apps.telegram_bot.delivery.timezone.now", side_effect=lambda: clock[0]),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        sender = pool.submit(
            on_connection, lambda: process_telegram_deliveries(limit=1)
        )
        try:
            assert sending.wait(timeout=5)
            if lease_expires:
                clock[0] += timedelta(minutes=6)
            other = pool.submit(
                on_connection, lambda: process_telegram_deliveries(limit=1)
            )
            assert other.result(timeout=5) == 0
            network.assert_called_once()
            assert Delivery.objects.get(pk=first.pk).status == "processing"
            assert Delivery.objects.get(pk=second.pk).attempts == 0
        finally:
            release.set()
        assert sender.result(timeout=15) == 1
        assert process_telegram_deliveries(limit=1) == 1
    assert network.call_count == 2
    assert Delivery.objects.filter(status="accepted").count() == 2


@pytest.mark.parametrize("expired", ["delivery", "lease"])
def test_delivery_expiration_is_rechecked_after_waiting_for_binding(
    expired: str,
) -> None:
    """Срок задания и аренды проверяются снова после ожидания разрешения адресата."""
    player = make_player(email_suffix="delivery-expiry-wait")
    link = UserTelegramLink.objects.create(user=player.user, user_bot_chat_id=1001)
    now = timezone.now()
    item = enqueue_message(
        player.user,
        "Истекающий контекст",
        expires_at=now + timedelta(minutes=1 if expired == "delivery" else 30),
    )
    assert item is not None
    clock = [timezone.now()]
    pid: Queue[int] = Queue()
    with (
        patch("apps.telegram_bot.services.is_configured", return_value=True),
        patch(
            "apps.telegram_bot.services.send_message_or_raise", return_value=71
        ) as network,
        patch("apps.telegram_bot.delivery.timezone.now", side_effect=lambda: clock[0]),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        with transaction.atomic():
            UserTelegramLink.objects.select_for_update(no_key=True).get(pk=link.pk)
            sender = pool.submit(
                on_connection, lambda: process_telegram_deliveries(limit=1), pid=pid
            )
            assert is_blocked(sender, pid.get(timeout=5))
            clock[0] += timedelta(minutes=2 if expired == "delivery" else 6)
        assert sender.result(timeout=15) == 1
    network.assert_not_called()
    item.refresh_from_db()
    assert item.status == ("skipped" if expired == "delivery" else "uncertain")
    assert item.error_code == (
        "telegram_expired" if expired == "delivery" else "telegram_worker_interrupted"
    )
