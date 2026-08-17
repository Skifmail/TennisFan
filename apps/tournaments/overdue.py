"""Просрочка дедлайна матча: уведомление администратора и Walkover за неявку."""

from __future__ import annotations

import logging
from typing import cast

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification, Player, User

from .models import Match, TournamentTeam
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
    msg = (
        f"Просрочен дедлайн матча {match.get_player1_display()} — "
        f"{match.get_player2_display()} в «{tournament_name}». "
        "Продлите дедлайн или проставьте Walkover (неявку)."
    )
    if len(msg) > 255:
        return msg[:252] + "..."
    return msg


def notify_admins_match_deadline_overdue(match: Match) -> tuple[bool, str]:
    """Уведомить администраторов о просрочке, не меняя результат матча.

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

    recipients = get_overdue_notification_recipients(match)
    if not recipients:
        logger.warning(
            "Overdue match %s: нет получателей уведомления (staff/club admins).",
            match.pk,
        )
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
    """Найти просроченные матчи указанных форматов и уведомить администраторов.

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
    notify: bool = True,
    penalty: float | None = None,
) -> None:
    """Проставить Walkover (неявку): без счёта, штраф только неявившемуся.

    Победителю ставится 0 к рейтингу, проигравшему — ``penalty``
    (по умолчанию ``WALKOVER_NO_SHOW_PENALTY``). Ноль означает, что штраф
    не начисляется. Матч получает статус «Без игры».

    Args:
        match: Незавершённый матч.
        loser: Игрок, которому засчитывается неявка (одиночный матч).
        loser_team: Команда, которой засчитывается неявка (парный матч).
        notify: Отправлять уведомления участникам о результате.
        penalty: Сумма штрафа в очках; ``None`` — значение по умолчанию.

    Raises:
        ValueError: Если не указан проигравший или он не участвует в матче.
    """
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        raise ValueError("Матч уже завершён.")

    is_doubles = bool(match.team1_id and match.team2_id)
    if is_doubles:
        if loser_team is None:
            raise ValueError("Для парного матча укажите команду с Walkover (неявкой).")
        if loser_team.pk not in (match.team1_id, match.team2_id):
            raise ValueError("Команда не участвует в этом матче.")
        winner_team = match.team2 if loser_team.pk == match.team1_id else match.team1
        match.winner_team = winner_team
        match.winner = winner_team.player1 if winner_team else None
    else:
        if loser is None:
            raise ValueError("Укажите игрока с Walkover (неявкой).")
        if loser.pk not in (match.player1_id, match.player2_id):
            raise ValueError("Игрок не участвует в этом матче.")
        winner = match.player2 if loser.pk == match.player1_id else match.player1
        match.winner = winner
        match.winner_team = None

    resolved_penalty = resolve_no_show_penalty(penalty)
    match._no_show_penalty = resolved_penalty
    match.status = Match.MatchStatus.WALKOVER
    match.completed_datetime = timezone.now()
    match.rating_status = Match.RatingCalcStatus.PENDING
    for set_idx in (1, 2, 3):
        setattr(match, f"player1_set{set_idx}", None)
        setattr(match, f"player2_set{set_idx}", None)
    match.save()

    match.result_proposals.filter(status=Match.ProposalStatus.PENDING).update(
        status=Match.ProposalStatus.REJECTED
    )

    if notify:
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
        "No-show walkover: match %s, winner %s, penalty %.0f",
        match.pk,
        match.winner_id,
        resolved_penalty,
    )


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
