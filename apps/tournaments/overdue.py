"""Просрочка дедлайна матча: уведомление администратора и Walkover за неявку."""

from __future__ import annotations

import logging
from typing import cast

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification, Player, User

from .models import Match, SeasonPoints, Tournament, TournamentStatus, TournamentTeam
from .rating import WALKOVER_NO_SHOW_PENALTY

logger = logging.getLogger(__name__)


def get_overdue_notification_recipients(match: Match) -> list[User]:
    """Собрать получателей уведомления о просрочке дедлайна.

    Платформенные сотрудники всегда получают уведомление. Для клубного турнира
    дополнительно уведомляются администраторы и менеджеры клуба — только они
    могут открыть управление таким турниром.

    Args:
        match: Просроченный матч.

    Returns:
        Уникальный список активных пользователей.
    """
    seen: set[int] = set()
    recipients: list[User] = []

    def _add(user: User | None) -> None:
        if user is None or not user.is_active or not user.pk or user.pk in seen:
            return
        seen.add(user.pk)
        recipients.append(user)

    for staff_user in User.objects.filter(is_staff=True, is_active=True):
        _add(staff_user)

    tournament = match.tournament
    if tournament and tournament.club_id:
        from apps.clubs.models import ClubMember, ClubMemberRole, ClubMemberStatus

        members = ClubMember.objects.filter(
            club_id=tournament.club_id,
            role__in=(ClubMemberRole.ADMIN, ClubMemberRole.MANAGER),
            status=ClubMemberStatus.ACTIVE,
        ).select_related("user")
        for member in members:
            _add(member.user)
    return recipients


def _manage_url(match: Match) -> str:
    """Ссылка на управление турниром с якорем на матч."""
    if match.tournament_id:
        return (
            cast(str, reverse("tournament_manage", args=[match.tournament.slug]))
            + f"#match-{match.pk}"
        )
    return cast(str, reverse("match_detail", args=[match.pk]))


def _build_overdue_lk_message(match: Match) -> str:
    """Короткий текст уведомления в личный кабинет (лимит 255 символов)."""
    tournament_name = match.tournament.name if match.tournament_id else "турнир"
    winner_name = match.get_player1_display()
    if match.winner_id == match.player2_id or (
        match.winner_team_id and match.winner_team_id == match.team2_id
    ):
        winner_name = match.get_player2_display()
    msg = (
        f"Просрочен дедлайн матча {match.get_player1_display()} — "
        f"{match.get_player2_display()} в «{tournament_name}». "
        f"Проставлен RT 0:0, дальше проходит {winner_name} (рейтинг не менялся)."
    )
    if len(msg) > 255:
        return msg[:252] + "..."
    return msg


def pick_higher_rated_winner(
    match: Match,
) -> tuple[Player | None, TournamentTeam | None]:
    """Выбрать победителя просроченного матча по рейтингу силы.

    При равенстве очков берётся сторона с меньшим id игрока. Для пары
    сравнивается сумма рейтингов обеих сторон.

    Args:
        match: Матч с двумя сторонами.

    Returns:
        Пара (победивший игрок, победившая команда или None).
    """
    if match.team1_id and match.team2_id and match.team1 and match.team2:
        t1_pts = match.team1.player1.total_points + (
            match.team1.player2.total_points if match.team1.player2_id else 0
        )
        t2_pts = match.team2.player1.total_points + (
            match.team2.player2.total_points if match.team2.player2_id else 0
        )
        if t1_pts != t2_pts:
            winner_team = match.team1 if t1_pts > t2_pts else match.team2
        elif match.team1.player1_id < match.team2.player1_id:
            winner_team = match.team1
        else:
            winner_team = match.team2
        return winner_team.player1 if winner_team else None, winner_team

    player_a, player_b = match.player1, match.player2
    if player_a is None or player_b is None:
        return None, None
    if getattr(player_a, "is_bye", False):
        return player_b, None
    if getattr(player_b, "is_bye", False):
        return player_a, None
    if player_a.total_points != player_b.total_points:
        winner = player_a if player_a.total_points > player_b.total_points else player_b
        return winner, None
    winner = player_a if player_a.pk < player_b.pk else player_b
    return winner, None


def apply_deadline_auto_rt(match: Match, *, notify: bool = True) -> None:
    """Закрыть просроченный матч как RT 0:0: побеждает более высокий рейтинг.

    Рейтинг силы не меняется. Счёт записывается нулями, бейдж Rt.
    """
    if match.status in (
        Match.MatchStatus.COMPLETED,
        Match.MatchStatus.WALKOVER,
        Match.MatchStatus.CANCELLED,
    ):
        raise ValueError("Матч уже завершён.")
    winner, winner_team = pick_higher_rated_winner(match)
    if winner is None:
        raise ValueError("Нельзя определить победителя по рейтингу.")

    match.winner = winner
    match.winner_team = winner_team
    match.player1_set1 = 0
    match.player2_set1 = 0
    match.player1_set2 = 0
    match.player2_set2 = 0
    match.player1_set3 = None
    match.player2_set3 = None
    match.walkover_no_show_side1 = False
    match.walkover_no_show_side2 = False
    match.status = Match.MatchStatus.WALKOVER
    if not match.completed_datetime:
        match.completed_datetime = timezone.now()
    match.rating_status = Match.RatingCalcStatus.NOT_APPLICABLE
    match.rating_delta_player1 = 0.0
    match.rating_delta_player2 = 0.0
    match.save()

    match.result_proposals.filter(status=Match.ProposalStatus.PENDING).update(
        status=Match.ProposalStatus.REJECTED
    )
    if notify:
        _notify_players_deadline_auto_rt(match, winner=winner, winner_team=winner_team)
    logger.info(
        "Deadline auto RT: match %s → winner %s",
        match.pk,
        winner.pk,
    )


def _notify_players_deadline_auto_rt(
    match: Match,
    *,
    winner: Player,
    winner_team: TournamentTeam | None,
) -> None:
    """Сообщить участникам, что по дедлайну проставлен RT."""
    from django.urls import reverse

    url = reverse("match_detail", args=[match.pk])
    win_msg = "Дедлайн матча истёк. Вам присуждена тех. победа (RT 0:0)."
    lose_msg = "Дедлайн матча истёк. Вам засчитано тех. поражение (RT 0:0)."
    recipients: list[tuple[Player | None, str]] = []
    if winner_team is not None and match.team1 and match.team2:
        loser_team = match.team2 if winner_team.pk == match.team1_id else match.team1
        for player in (winner_team.player1, winner_team.player2):
            recipients.append((player, win_msg))
        for player in (loser_team.player1, loser_team.player2):
            recipients.append((player, lose_msg))
    else:
        loser = match.player2 if winner.pk == match.player1_id else match.player1
        recipients.append((winner, win_msg))
        recipients.append((loser, lose_msg))
    for player, message in recipients:
        if player is None or getattr(player, "is_bye", False):
            continue
        try:
            Notification.objects.create(user=player.user, message=message, url=url)
        except Exception:
            logger.exception(
                "deadline auto RT notify player %s match %s failed",
                getattr(player, "pk", None),
                match.pk,
            )


def revert_deadline_auto_rt(match: Match) -> None:
    """Откатить автоматический RT: матч снова запланирован, слот сетки очищен."""
    if not match.is_deadline_auto_rt():
        raise ValueError("Это не автоматический RT по дедлайну.")
    old_winner_id = match.winner_id
    old_winner_team_id = match.winner_team_id
    match.status = Match.MatchStatus.SCHEDULED
    match.winner = None
    match.winner_team = None
    match.completed_datetime = None
    match.rating_status = Match.RatingCalcStatus.NOT_APPLICABLE
    match.rating_delta_player1 = 0.0
    match.rating_delta_player2 = 0.0
    match.deadline_overdue_notified_at = None
    match.walkover_no_show_side1 = False
    match.walkover_no_show_side2 = False
    for set_idx in (1, 2, 3):
        setattr(match, f"player1_set{set_idx}", None)
        setattr(match, f"player2_set{set_idx}", None)
    match.save()
    _sync_bracket_after_walkover_edit(
        match,
        old_winner_id=old_winner_id,
        old_winner_team_id=old_winner_team_id,
    )
    if match.tournament_id:
        tournament = Tournament.objects.get(pk=match.tournament_id)
        _reopen_tournament_after_auto_rt_revert(tournament)
    logger.info("Reverted deadline auto RT: match %s", match.pk)


def revert_no_show_walkover(match: Match) -> None:
    """Откатить клубный Walkover (неявку): матч снова запланирован.

    Штраф рейтинга и matches_played / matches_won возвращаются.
    Продление дедлайна после неявки снова открывает матч.
    """
    if not match.is_no_show_walkover():
        raise ValueError("Это не Walkover (неявка).")
    old_winner_id = match.winner_id
    old_winner_team_id = match.winner_team_id
    _revert_old_walkover_effects(match)
    match.status = Match.MatchStatus.SCHEDULED
    match.winner = None
    match.winner_team = None
    match.completed_datetime = None
    match.rating_status = Match.RatingCalcStatus.NOT_APPLICABLE
    match.rating_delta_player1 = 0.0
    match.rating_delta_player2 = 0.0
    match.deadline_overdue_notified_at = None
    match.walkover_no_show_side1 = False
    match.walkover_no_show_side2 = False
    for set_idx in (1, 2, 3):
        setattr(match, f"player1_set{set_idx}", None)
        setattr(match, f"player2_set{set_idx}", None)
    match.save()
    _sync_bracket_after_walkover_edit(
        match,
        old_winner_id=old_winner_id,
        old_winner_team_id=old_winner_team_id,
    )
    if match.tournament_id:
        tournament = Tournament.objects.get(pk=match.tournament_id)
        _reopen_tournament_after_auto_rt_revert(tournament)
    logger.info("Reverted no-show walkover: match %s", match.pk)


def _reopen_tournament_after_auto_rt_revert(tournament: Tournament) -> None:
    """Вернуть турнир в ACTIVE и откатить очки за места после авто-RT.

    Авто-RT на последнем матче финализирует круговик. Продление дедлайна
    снова открывает матч — турнир не может оставаться завершённым.
    """
    if tournament.status != TournamentStatus.COMPLETED:
        return
    from .season_utils import get_current_season

    current_season = get_current_season()
    place_results = list(
        tournament.fan_results.filter(place__isnull=False).select_related("player")
    )
    for result in place_results:
        awarded = int(result.fan_points or 0)
        if awarded <= 0 or not result.player_id:
            continue
        season_points = SeasonPoints.objects.filter(player_id=result.player_id).first()
        if (
            season_points is None
            or season_points.season_name != current_season.name
            or season_points.season_year != current_season.year
        ):
            continue
        season_points.current_season_points = max(
            0, int(season_points.current_season_points) - awarded
        )
        season_points.save(update_fields=["current_season_points", "updated_at"])
    tournament.fan_results.filter(place__isnull=False).delete()
    tournament.status = TournamentStatus.ACTIVE
    tournament.save(update_fields=["status"])
    logger.info(
        "Reopened tournament %s after deadline auto RT revert",
        tournament.pk,
    )


def notify_admins_match_deadline_overdue(match: Match) -> tuple[bool, str]:
    """При просрочке: RT 0:0, побеждает более высокий рейтинг, уведомление админам.

    Args:
        match: Матч с истёкшим дедлайном.

    Returns:
        Пара (успех, сообщение для cron/логов).
    """
    if match.status in (
        Match.MatchStatus.COMPLETED,
        Match.MatchStatus.WALKOVER,
        Match.MatchStatus.CANCELLED,
    ):
        return False, "Матч уже завершён."
    if not match.deadline or match.deadline > timezone.now():
        return False, "Дедлайн не истёк."
    if match.deadline_overdue_notified_at:
        return False, "Администраторы уже уведомлены."

    try:
        apply_deadline_auto_rt(match, notify=True)
    except ValueError as exc:
        return False, str(exc)
    match.refresh_from_db()

    recipients = get_overdue_notification_recipients(match)
    if not recipients:
        logger.warning(
            "Overdue match %s: нет получателей уведомления (staff/club admins).",
            match.pk,
        )
        match.deadline_overdue_notified_at = timezone.now()
        match.save(update_fields=["deadline_overdue_notified_at"])
        return False, "Нет администраторов для уведомления."

    url = _manage_url(match)
    message = _build_overdue_lk_message(match)
    emailed = 0
    from apps.core.email_service import send_match_deadline_overdue_admin_email

    for user in recipients:
        Notification.objects.create(user=user, message=message, url=url)
        if send_match_deadline_overdue_admin_email(user, match, manage_url=url):
            emailed += 1

    match.deadline_overdue_notified_at = timezone.now()
    match.save(update_fields=["deadline_overdue_notified_at"])
    logger.info(
        "Overdue match %s: уведомлены %s админов, писем %s",
        match.pk,
        len(recipients),
        emailed,
    )
    return (
        True,
        (
            f"Матч {match.pk}: администраторы уведомлены о просрочке "
            f"(ЛК: {len(recipients)}, email: {emailed})."
        ),
    )


def notify_overdue_matches_for_formats(formats: list[str]) -> tuple[int, int]:
    """Найти просроченные матчи, проставить RT 0:0 и уведомить админов.

    Args:
        formats: Значения ``Tournament.format``.

    Returns:
        Пара (число уведомлённых матчей, число пропущенных).
    """
    now = timezone.now()
    matches = list(
        Match.objects.filter(
            tournament__format__in=formats,
            deadline__lte=now,
            deadline__isnull=False,
            deadline_overdue_notified_at__isnull=True,
            status__in=(Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS),
        ).select_related(
            "tournament",
            "tournament__club",
            "player1",
            "player2",
            "team1",
            "team2",
        )
    )
    notified = 0
    skipped = 0
    for match in matches:
        ok, _msg = notify_admins_match_deadline_overdue(match)
        if ok:
            notified += 1
        else:
            skipped += 1
    return notified, skipped


def resolve_no_show_penalty(penalty: float | None) -> float:
    """Вернуть величину штрафа за неявку в очках рейтинга.

    ``None`` означает значение по умолчанию ``WALKOVER_NO_SHOW_PENALTY``.
    Отрицательные числа принимаются по модулю: ``-25`` и ``25`` дают штраф 25.
    """
    if penalty is None:
        return float(WALKOVER_NO_SHOW_PENALTY)
    return abs(float(penalty))


def apply_no_show_walkover(
    match: Match,
    *,
    loser: Player | None = None,
    loser_team: TournamentTeam | None = None,
    side1_no_show: bool | None = None,
    side2_no_show: bool | None = None,
    notify: bool = True,
    penalty: float | None = None,
    penalty_side1: float | None = None,
    penalty_side2: float | None = None,
    replace: bool = False,
) -> None:
    """Проставить или изменить Walkover (неявку) без счёта.

    Можно отметить одну или обе стороны. Если отмечены обе — победителя нет,
    обе стороны получают свой штраф. Ноль штрафа означает, что рейтинг
    не меняется. ``replace=True`` позволяет править уже проставленный WO.
    """
    is_edit = replace and (match.is_no_show_walkover() or match.is_deadline_auto_rt())
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        if not is_edit:
            raise ValueError("Матч уже завершён.")
        if match.is_walkover_loss():
            raise ValueError("Retired нельзя заменить этим Walkover (неявкой).")

    is_doubles = bool(match.team1_id and match.team2_id)
    if side1_no_show is None or side2_no_show is None:
        side1_no_show, side2_no_show = _sides_from_loser(
            match, loser=loser, loser_team=loser_team, is_doubles=is_doubles
        )
    if not side1_no_show and not side2_no_show:
        raise ValueError("Отметьте хотя бы одного участника с Walkover (неявкой).")

    resolved_side1 = (
        resolve_no_show_penalty(penalty_side1 if penalty_side1 is not None else penalty)
        if side1_no_show
        else 0.0
    )
    resolved_side2 = (
        resolve_no_show_penalty(penalty_side2 if penalty_side2 is not None else penalty)
        if side2_no_show
        else 0.0
    )

    old_winner_id = match.winner_id
    old_winner_team_id = match.winner_team_id
    _assign_walkover_winner(
        match,
        is_doubles=is_doubles,
        side1_no_show=side1_no_show,
        side2_no_show=side2_no_show,
    )
    match.walkover_no_show_side1 = bool(side1_no_show)
    match.walkover_no_show_side2 = bool(side2_no_show)
    match._no_show_penalty_side1 = resolved_side1
    match._no_show_penalty_side2 = resolved_side2
    match._no_show_penalty = resolved_side1 or resolved_side2
    if is_edit:
        match._force_wo_recalc = True
    match.status = Match.MatchStatus.WALKOVER
    if not match.completed_datetime:
        match.completed_datetime = timezone.now()
    match.rating_status = Match.RatingCalcStatus.PENDING
    for set_idx in (1, 2, 3):
        setattr(match, f"player1_set{set_idx}", None)
        setattr(match, f"player2_set{set_idx}", None)
    match.save()

    match.result_proposals.filter(status=Match.ProposalStatus.PENDING).update(
        status=Match.ProposalStatus.REJECTED
    )
    if is_edit:
        _sync_bracket_after_walkover_edit(
            match,
            old_winner_id=old_winner_id,
            old_winner_team_id=old_winner_team_id,
        )

    if notify and not is_edit:
        from .proposal_service import notify_participants_match_result_confirmed

        try:
            notify_participants_match_result_confirmed(match, walkover=True)
        except Exception:
            logger.exception(
                "notify_participants_match_result_confirmed after no-show "
                "walkover match %s failed",
                match.pk,
            )
    logger.info(
        "No-show walkover: match %s, winner %s, penalties %.0f/%.0f",
        match.pk,
        match.winner_id,
        resolved_side1,
        resolved_side2,
    )


def _sides_from_loser(
    match: Match,
    *,
    loser: Player | None,
    loser_team: TournamentTeam | None,
    is_doubles: bool,
) -> tuple[bool, bool]:
    """Вывести неявку сторон из одного проигравшего (совместимость)."""
    if is_doubles:
        if loser_team is None:
            raise ValueError("Для парного матча укажите команду с Walkover (неявкой).")
        if loser_team.pk not in (match.team1_id, match.team2_id):
            raise ValueError("Команда не участвует в этом матче.")
        return loser_team.pk == match.team1_id, loser_team.pk == match.team2_id
    if loser is None:
        raise ValueError("Укажите игрока с Walkover (неявкой).")
    if loser.pk not in (match.player1_id, match.player2_id):
        raise ValueError("Игрок не участвует в этом матче.")
    return loser.pk == match.player1_id, loser.pk == match.player2_id


def _assign_walkover_winner(
    match: Match,
    *,
    is_doubles: bool,
    side1_no_show: bool,
    side2_no_show: bool,
) -> None:
    """Выставить победителя: если неявки обе стороны — победителя нет."""
    if side1_no_show and side2_no_show:
        match.winner = None
        match.winner_team = None
        return
    if is_doubles:
        winner_team = match.team2 if side1_no_show else match.team1
        match.winner_team = winner_team
        match.winner = winner_team.player1 if winner_team else None
        return
    winner = match.player2 if side1_no_show else match.player1
    match.winner = winner
    match.winner_team = None


def _sync_bracket_after_walkover_edit(
    match: Match,
    *,
    old_winner_id: int | None,
    old_winner_team_id: int | None,
) -> None:
    """Обновить слот следующего матча после правки Walkover (неявки)."""
    parent = match.next_match
    if parent is None:
        return
    if parent.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        logger.warning(
            "Walkover edit match %s: next match %s already finished, skip slot sync",
            match.pk,
            parent.pk,
        )
        return
    is_doubles = bool(match.team1_id and match.team2_id)
    fill_slot1 = (match.round_order or 0) % 2 == 1
    if is_doubles:
        if fill_slot1 and parent.team1_id == old_winner_team_id:
            parent.team1 = None
            parent.player1 = None
        elif not fill_slot1 and parent.team2_id == old_winner_team_id:
            parent.team2 = None
            parent.player2 = None
        if match.winner_team_id:
            if fill_slot1:
                parent.team1 = match.winner_team
                parent.player1 = (
                    match.winner_team.player1 if match.winner_team else None
                )
            else:
                parent.team2 = match.winner_team
                parent.player2 = (
                    match.winner_team.player1 if match.winner_team else None
                )
        both_filled = bool(parent.team1_id and parent.team2_id)
        update_fields = ["team1", "team2", "player1", "player2"]
    else:
        if fill_slot1 and parent.player1_id == old_winner_id:
            parent.player1 = None
        elif not fill_slot1 and parent.player2_id == old_winner_id:
            parent.player2 = None
        if match.winner_id:
            if fill_slot1:
                parent.player1 = match.winner
            else:
                parent.player2 = match.winner
        both_filled = bool(parent.player1_id and parent.player2_id)
        update_fields = ["player1", "player2"]
    if both_filled:
        parent.status = Match.MatchStatus.SCHEDULED
        update_fields.append("status")
    parent.save(update_fields=update_fields)


def _players_matching_name(query: str) -> list[Player]:
    """Найти игроков по фрагменту имени или фамилии."""
    q = query.strip()
    if not q:
        return []
    return list(
        Player.objects.filter(
            Q(user__last_name__icontains=q) | Q(user__first_name__icontains=q)
        ).select_related("user")
    )


def _reload_match(match_id: int) -> Match:
    """Перечитать матч со связанными игроками."""
    return cast(
        Match,
        Match.objects.select_related(
            "player1__user",
            "player2__user",
            "winner",
            "tournament",
            "team1__player1",
            "team1__player2",
            "team2__player1",
            "team2__player2",
            "winner_team",
        ).get(pk=match_id),
    )


def find_match_for_walkover_replace(
    *,
    match_id: int | None = None,
    player_query: str = "",
    opponent_query: str = "",
    tournament_query: str = "",
) -> Match:
    """Найти матч для отката авто-Walkover по id или фамилиям игроков.

    Args:
        match_id: Точный id матча.
        player_query: Фрагмент имени первого игрока.
        opponent_query: Фрагмент имени второго игрока.
        tournament_query: Фрагмент названия или slug турнира.

    Returns:
        Найденный матч.

    Raises:
        ValueError: Если матч не найден или найдено несколько.
    """
    if match_id:
        match = (
            Match.objects.filter(pk=match_id)
            .select_related(
                "player1__user",
                "player2__user",
                "winner",
                "tournament",
            )
            .first()
        )
        if match is None:
            raise ValueError(f"Матч не найден: id={match_id}")
        return cast(Match, match)

    player_q = player_query.strip()
    opponent_q = opponent_query.strip()
    if not player_q or not opponent_q:
        raise ValueError("Укажите --match-id или пару --player и --opponent.")

    p1_ids = [p.pk for p in _players_matching_name(player_q)]
    p2_ids = [p.pk for p in _players_matching_name(opponent_q)]
    if not p1_ids:
        raise ValueError(f"Игрок не найден: {player_q}")
    if not p2_ids:
        raise ValueError(f"Игрок не найден: {opponent_q}")

    qs = Match.objects.filter(
        Q(player1_id__in=p1_ids, player2_id__in=p2_ids)
        | Q(player1_id__in=p2_ids, player2_id__in=p1_ids),
        status=Match.MatchStatus.WALKOVER,
    ).select_related(
        "player1__user",
        "player2__user",
        "winner",
        "tournament",
    )
    tournament_q = tournament_query.strip()
    if tournament_q:
        qs = qs.filter(
            Q(tournament__name__icontains=tournament_q)
            | Q(tournament__slug__icontains=tournament_q)
        )
    matches = list(qs.order_by("-pk"))
    if not matches:
        raise ValueError(f"Walkover-матч не найден: {player_q} vs {opponent_q}.")
    if len(matches) > 1:
        ids = ", ".join(str(m.pk) for m in matches)
        raise ValueError(f"Найдено несколько матчей ({ids}). Укажите --match-id.")
    return cast(Match, matches[0])


def resolve_walkover_loser(
    match: Match,
    *,
    loser_query: str = "",
    keep_winner: bool = False,
) -> Player:
    """Определить игрока, которому засчитывается неявка.

    Args:
        match: Одиночный матч.
        loser_query: Фрагмент имени неявившегося.
        keep_winner: Оставить текущего победителя, неявку — сопернику.

    Returns:
        Игрок с Walkover.

    Raises:
        ValueError: Если проигравшего нельзя однозначно определить.
    """
    if match.team1_id and match.team2_id:
        raise ValueError("Для парного матча укажите --match-id и правьте вручную.")
    if not match.player1_id or not match.player2_id:
        raise ValueError("У матча нет обоих игроков.")

    if keep_winner:
        if not match.winner_id:
            raise ValueError("У матча нет победителя, укажите --loser.")
        if match.winner_id == match.player1_id:
            if match.player2 is None:
                raise ValueError("У матча нет второго игрока.")
            return cast(Player, match.player2)
        if match.player1 is None:
            raise ValueError("У матча нет первого игрока.")
        return cast(Player, match.player1)

    query = loser_query.strip()
    if not query:
        raise ValueError("Укажите --loser или --keep-winner.")

    candidates = [
        player
        for player in (match.player1, match.player2)
        if player is not None and _player_matches_query(player, query)
    ]
    if len(candidates) == 1:
        return cast(Player, candidates[0])
    if not candidates:
        raise ValueError(f"Игрок «{query}» не участвует в этом матче.")
    raise ValueError(f"Фрагмент «{query}» подходит обоим игрокам, уточните.")


def _player_matches_query(player: Player, query: str) -> bool:
    """Проверить, подходит ли игрок под фрагмент имени."""
    q = query.strip().lower()
    if not q:
        return False
    user = getattr(player, "user", None)
    parts = [
        player.get_display_name(),
        getattr(user, "first_name", "") or "",
        getattr(user, "last_name", "") or "",
        getattr(user, "email", "") or "",
    ]
    haystack = " ".join(part for part in parts if part).lower()
    return q in haystack


def _new_deltas_for_loser(match: Match, loser: Player) -> tuple[float, float]:
    """Дельты рейтинга после Walkover за неявку."""
    penalty = WALKOVER_NO_SHOW_PENALTY
    if loser.pk == match.player1_id:
        return -penalty, 0.0
    return 0.0, -penalty


def _revert_old_walkover_effects(match: Match) -> None:
    """Откатить FAN-дельты и matches_played / matches_won текущего walkover."""
    from apps.tournaments.signals import _revert_player_rating
    from apps.tournaments.withdraw import _revert_player_stats_for_walkover

    d1 = float(match.rating_delta_player1 or 0.0)
    d2 = float(match.rating_delta_player2 or 0.0)
    if match.team1_id and match.team2_id:
        if match.team1:
            for player in (match.team1.player1, match.team1.player2):
                _revert_player_rating(player, d1)
                _revert_player_stats_for_walkover(
                    match,
                    player=player,
                    was_winner=match.winner_team_id == match.team1_id,
                )
        if match.team2:
            for player in (match.team2.player1, match.team2.player2):
                _revert_player_rating(player, d2)
                _revert_player_stats_for_walkover(
                    match,
                    player=player,
                    was_winner=match.winner_team_id == match.team2_id,
                )
        return
    _revert_player_rating(match.player1, d1)
    _revert_player_rating(match.player2, d2)
    if match.player1:
        _revert_player_stats_for_walkover(
            match,
            player=match.player1,
            was_winner=match.winner_id == match.player1_id,
        )
    if match.player2:
        _revert_player_stats_for_walkover(
            match,
            player=match.player2,
            was_winner=match.winner_id == match.player2_id,
        )


def _reset_walkover_to_scheduled(match: Match) -> None:
    """Сбросить walkover в scheduled без сигналов, чтобы заново проставить результат."""
    Match.objects.filter(pk=match.pk).update(
        status=Match.MatchStatus.SCHEDULED,
        winner=None,
        winner_team=None,
        rating_status=Match.RatingCalcStatus.NOT_APPLICABLE,
        rating_delta_player1=0.0,
        rating_delta_player2=0.0,
        completed_datetime=None,
        points_player1=0,
        points_player2=0,
    )


def replace_no_show_walkover(
    match: Match,
    *,
    loser: Player,
    dry_run: bool = False,
    notify: bool = False,
) -> list[str]:
    """Откатить ошибочный FAN-walkover и проставить Walkover за неявку.

    Старый авто-крон ставил WALKOVER без счёта и считал FAN от 0:0, из-за чего
    оба игрока теряли рейтинг. Функция возвращает рейтинг по сохранённым дельтам
    и заново ставит неявку: −40 неявившемуся, 0 сопернику.

    Args:
        match: Walkover без счёта или ещё не сыгранный матч.
        loser: Игрок, которому засчитывается неявка.
        dry_run: Только показать ожидаемый результат, не писать в БД.
        notify: Уведомить участников (по умолчанию нет — это правка данных).

    Returns:
        Строки отчёта для management-команды.

    Raises:
        ValueError: Если матч нельзя заменить таким Walkover.
    """
    match = _reload_match(match.pk)
    if loser.pk not in (match.player1_id, match.player2_id):
        raise ValueError("Игрок не участвует в этом матче.")

    is_walkover = match.status == Match.MatchStatus.WALKOVER
    if match.status == Match.MatchStatus.COMPLETED:
        raise ValueError("Матч завершён со счётом, откат Walkover не применяется.")
    if is_walkover:
        if match.has_set_scores():
            raise ValueError("У матча есть счёт (Retired), это не авто-Walkover.")
        if match.rating_status == Match.RatingCalcStatus.NOT_APPLICABLE:
            raise ValueError(
                "Это walkover снятия участника, используйте "
                "revert_withdrawal_walkover_ratings."
            )
    elif match.status not in (
        Match.MatchStatus.SCHEDULED,
        Match.MatchStatus.IN_PROGRESS,
    ):
        raise ValueError(f"Неподходящий статус матча: {match.status}.")

    old_delta1 = float(match.rating_delta_player1 or 0.0)
    old_delta2 = float(match.rating_delta_player2 or 0.0)
    new_delta1, new_delta2 = _new_deltas_for_loser(match, loser)
    p1_name = match.get_player1_display()
    p2_name = match.get_player2_display()
    tournament_name = match.tournament.name if match.tournament_id else "—"
    winner_name = match.winner.get_display_name() if match.winner else "—"
    new_winner = match.player2 if loser.pk == match.player1_id else match.player1
    new_winner_name = new_winner.get_display_name() if new_winner else "—"

    p1_now = float(match.player1.total_points) if match.player1 else 0.0
    p2_now = float(match.player2.total_points) if match.player2 else 0.0
    p1_after = p1_now - (old_delta1 if is_walkover else 0.0) + new_delta1
    p2_after = p2_now - (old_delta2 if is_walkover else 0.0) + new_delta2

    lines = [
        (
            f"match={match.pk} tournament={tournament_name} "
            f"«{p1_name}» vs «{p2_name}»"
        ),
        (
            f"сейчас: status={match.status} winner={winner_name} "
            f"Δ1={old_delta1:+.1f} Δ2={old_delta2:+.1f}"
        ),
        f"текущий рейтинг: {p1_name}={p1_now:.1f} {p2_name}={p2_now:.1f}",
        (
            f"после: winner={new_winner_name} неявка={loser.get_display_name()} "
            f"Δ1={new_delta1:+.1f} Δ2={new_delta2:+.1f}"
        ),
        (f"ожидаемый рейтинг: {p1_name}={p1_after:.1f} " f"{p2_name}={p2_after:.1f}"),
    ]
    if match.tournament_id and match.tournament.status == "completed":
        lines.append("внимание: турнир уже завершён; таблица мест не пересчитывается.")
        if match.winner_id and new_winner and match.winner_id != new_winner.pk:
            lines.append("внимание: победитель изменится — проверьте места вручную.")

    if dry_run:
        lines.append("[dry-run] изменения не записаны")
        return lines

    with transaction.atomic():
        if is_walkover:
            _revert_old_walkover_effects(match)
            _reset_walkover_to_scheduled(match)
            match = _reload_match(match.pk)
            lines.append(
                f"откат FAN: {p1_name} {old_delta1:+.1f}, "
                f"{p2_name} {old_delta2:+.1f}"
            )
        apply_no_show_walkover(match, loser=loser, notify=notify)
        lines.append(
            f"проставлен Walkover за неявку: −{WALKOVER_NO_SHOW_PENALTY:.0f} "
            f"{loser.get_display_name()}, 0 сопернику"
        )
    return lines
