import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .fan import (
    _is_fan,
    advance_winner_and_award_loser,
    ensure_consolation_created,
    finalize_tournament,
)
from .olympic_consolation import _is_olympic, advance_winner_olympic, ensure_consolation_created_for_round
from .round_robin import _is_round_robin, check_and_finalize_if_complete
from .models import Match, MatchResultProposal
from .proposal_service import apply_proposal

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Match)
def notify_telegram_match_created(sender, instance, created, **kwargs):
    """После создания матча с участниками — уведомление в Telegram."""
    if not created:
        return
    if not instance.player1_id and not instance.team1_id:
        return
    try:
        from apps.telegram_bot.notifications import notify_match_created
        notify_match_created(instance)
    except Exception as e:
        logger.exception("Telegram notify_match_created failed: %s", e)


@receiver(pre_save, sender=Match)
def prepare_match_completion(sender, instance, **kwargs):
    """
    Handle pre-save logic:
    1. Store old status to detect transitions.
    2. Mark completed matches as pending_calc for Elo rating.
    """
    if instance.pk:
        try:
            old_instance = Match.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except Match.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

    # If match is transitioning to completed, mark for Elo calculation
    if instance.status in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]:
        old_status = getattr(instance, "_old_status", None)
        was_completed = old_status in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
        if not was_completed and instance.rating_status == Match.RatingCalcStatus.NOT_APPLICABLE:
            instance.rating_status = Match.RatingCalcStatus.PENDING


@receiver(post_save, sender=Match)
def update_player_stats(sender, instance, created, **kwargs):
    """
    Update matches_played / matches_won when match is completed.
    Apply shadow Elo rating calculation to hidden_rating.
    """
    old_status = getattr(instance, "_old_status", None)
    is_completed = instance.status in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
    was_completed = old_status in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
    if not is_completed or was_completed:
        return

    winner = instance.winner
    winner_team = getattr(instance, "winner_team", None)
    t = getattr(instance, "tournament", None)
    is_doubles = t and t.is_doubles() and instance.team1_id and instance.team2_id

    if not winner and not winner_team:
        return

    _walkover_loss = instance.is_walkover_loss()

    # ------------------------------------------------------------------
    # 1) Update matches_played / matches_won for all formats
    # ------------------------------------------------------------------
    def _update_stats_singles(match):
        w = match.winner
        los = match.player2 if w == match.player1 else match.player1
        if w and not getattr(w, "is_bye", False):
            w.matches_played += 1
            w.matches_won += 1
            w.save(update_fields=["matches_played", "matches_won"])
        if los and not getattr(los, "is_bye", False):
            los.matches_played += 1
            los.save(update_fields=["matches_played"])

    def _update_stats_doubles(match):
        wt = match.winner_team
        lt = match.team2 if wt == match.team1 else match.team1
        for p in (wt.player1, wt.player2):
            if p and not getattr(p, "is_bye", False):
                p.matches_played += 1
                p.matches_won += 1
                p.save(update_fields=["matches_played", "matches_won"])
        for p in (lt.player1, lt.player2):
            if p and not getattr(p, "is_bye", False):
                p.matches_played += 1
                p.save(update_fields=["matches_played"])

    if is_doubles:
        _update_stats_doubles(instance)
    else:
        _update_stats_singles(instance)

    # ------------------------------------------------------------------
    # 2) Shadow Elo calculation (updates hidden_rating immediately)
    # ------------------------------------------------------------------
    _apply_elo_shadow(instance)

    # ------------------------------------------------------------------
    # 3) Format-specific bracket advancement (FAN / Olympic / Round Robin)
    # ------------------------------------------------------------------
    if t and _is_olympic(t):
        advance_winner_olympic(instance, skip_points=_walkover_loss)
        if instance.round_index >= 1 and not instance.is_consolation:
            ensure_consolation_created_for_round(t, instance.round_index)
    elif t and _is_fan(t):
        advance_winner_and_award_loser(instance, skip_points=_walkover_loss)
        if instance.round_index == 1 and not instance.is_consolation:
            ensure_consolation_created(t)
        finalize_tournament(t)
    elif t and _is_round_robin(t):
        check_and_finalize_if_complete(t)


def _apply_elo_shadow(match: Match) -> None:
    """Apply shadow Elo rating calculation to both players' hidden_rating.

    For doubles: delta is applied to both members of each team.
    Bye players are skipped.
    """
    from .rating import (
        MatchScore,
        PlayerRatingSnapshot,
        calculate_new_ratings,
    )

    p1 = match.player1
    p2 = match.player2
    if not p1 or not p2:
        return
    if getattr(p1, "is_bye", False) or getattr(p2, "is_bye", False):
        return

    # K-factor определяется по количеству матчей ДО этого матча
    # (matches_played уже обновлён выше, поэтому вычитаем 1)
    matches_before_a = max(0, p1.matches_played - 1)
    matches_before_b = max(0, p2.matches_played - 1)

    # Для первого матча используем total_points как начальный рейтинг,
    # если hidden_rating не был правильно инициализирован
    rating_a = p1.hidden_rating
    rating_b = p2.hidden_rating
    
    if matches_before_a == 0:
        # Первый матч: если hidden_rating сильно отличается от total_points,
        # используем total_points как начальный рейтинг
        if abs(rating_a - float(p1.total_points)) > 100:
            rating_a = float(p1.total_points)
            logger.warning(
                "Player %s: hidden_rating (%.1f) не соответствует total_points (%d) для первого матча. "
                "Используем total_points как начальный рейтинг.",
                p1.pk, p1.hidden_rating, p1.total_points
            )
    
    if matches_before_b == 0:
        # Первый матч: если hidden_rating сильно отличается от total_points,
        # используем total_points как начальный рейтинг
        if abs(rating_b - float(p2.total_points)) > 100:
            rating_b = float(p2.total_points)
            logger.warning(
                "Player %s: hidden_rating (%.1f) не соответствует total_points (%d) для первого матча. "
                "Используем total_points как начальный рейтинг.",
                p2.pk, p2.hidden_rating, p2.total_points
            )

    snap_a = PlayerRatingSnapshot(rating=rating_a, total_matches=matches_before_a)
    snap_b = PlayerRatingSnapshot(rating=rating_b, total_matches=matches_before_b)

    score_a = MatchScore(
        set1=match.player1_set1 or 0,
        set2=match.player1_set2 or 0,
        set3=match.player1_set3 or 0,
    )
    score_b = MatchScore(
        set1=match.player2_set1 or 0,
        set2=match.player2_set2 or 0,
        set3=match.player2_set3 or 0,
    )

    a_won = (
        match.winner_team_id == match.team1_id
        if match.team1_id and match.team2_id and match.winner_team_id
        else match.winner_id == p1.pk
    )

    result = calculate_new_ratings(snap_a, snap_b, score_a, score_b, a_won)

    is_doubles = match.team1_id and match.team2_id
    
    # Проверяем, является ли это техническим поражением (Retired)
    is_walkover_loss = match.is_walkover_loss()
    
    # Определяем проигравшего для применения штрафа
    if is_doubles:
        # Для парных: определяем проигравшую команду
        winner_team = match.winner_team
        if winner_team:
            loser_team = match.team2 if winner_team == match.team1 else match.team1
        else:
            # Если winner_team не установлен, определяем по a_won
            loser_team = match.team2 if a_won else match.team1
        loser = None  # Не используется для парных
    else:
        # Для одиночных: определяем проигравшего игрока
        loser = p2 if a_won else p1
        loser_team = None

    # Update hidden_rating and total_points for player1 side
    # total_points обновляется сразу после каждого матча для видимости прогресса
    if is_doubles:
        for p in (match.team1.player1, match.team1.player2):
            if p and not getattr(p, "is_bye", False):
                new_rating = result.new_rating_a
                # Применяем штраф -40 очков для проигравшего при тех. поражении
                if is_walkover_loss and loser_team and p in (loser_team.player1, loser_team.player2):
                    new_rating = max(0, new_rating - 40.0)
                    logger.info("Player %s: штраф -40 очков за тех. поражение (Retired)", p.pk)
                p.hidden_rating = new_rating
                p.total_points = float(new_rating)
                p.save(update_fields=["hidden_rating", "total_points"])
    else:
        new_rating_a = result.new_rating_a
        # Применяем штраф -40 очков для проигравшего при тех. поражении
        if is_walkover_loss and loser == p1:
            new_rating_a = max(0, new_rating_a - 40.0)
            logger.info("Player %s: штраф -40 очков за тех. поражение (Retired)", p1.pk)
        p1.hidden_rating = new_rating_a
        p1.total_points = float(new_rating_a)
        p1.save(update_fields=["hidden_rating", "total_points"])

    # Update hidden_rating and total_points for player2 side
    if is_doubles:
        for p in (match.team2.player1, match.team2.player2):
            if p and not getattr(p, "is_bye", False):
                new_rating = result.new_rating_b
                # Применяем штраф -40 очков для проигравшего при тех. поражении
                if is_walkover_loss and loser_team and p in (loser_team.player1, loser_team.player2):
                    new_rating = max(0, new_rating - 40.0)
                    logger.info("Player %s: штраф -40 очков за тех. поражение (Retired)", p.pk)
                p.hidden_rating = new_rating
                p.total_points = float(new_rating)
                p.save(update_fields=["hidden_rating", "total_points"])
    else:
        new_rating_b = result.new_rating_b
        # Применяем штраф -40 очков для проигравшего при тех. поражении
        if is_walkover_loss and loser == p2:
            new_rating_b = max(0, new_rating_b - 40.0)
            logger.info("Player %s: штраф -40 очков за тех. поражение (Retired)", p2.pk)
        p2.hidden_rating = new_rating_b
        p2.total_points = float(new_rating_b)
        p2.save(update_fields=["hidden_rating", "total_points"])

    # Store deltas and mark as calculated
    Match.objects.filter(pk=match.pk).update(
        rating_delta_player1=result.delta_a,
        rating_delta_player2=result.delta_b,
        rating_status=Match.RatingCalcStatus.CALCULATED,
    )


@receiver(post_save, sender=MatchResultProposal)
def apply_proposal_on_admin_accept(sender, instance, created, **kwargs):
    """
    Когда админ вручную меняет статус заявки на «Подтверждено» — применить результат к матчу.
    При подтверждении через ЛК (confirm_proposal) apply_proposal вызывается из view,
    матч уже обновлён, поэтому пропускаем (match.status in COMPLETED/WALKOVER).
    """
    if instance.status != Match.ProposalStatus.ACCEPTED:
        return
    match = instance.match
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        return  # уже применено (например, из confirm_proposal)
    apply_proposal(instance)
