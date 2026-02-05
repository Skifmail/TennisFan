import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

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
    2. Auto-calculate points if match is completing (только для не-FAN).
    """
    if instance.pk:
        try:
            old_instance = Match.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except Match.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

    if instance.status not in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]:
        return
    if instance.points_player1 != 0 or instance.points_player2 != 0:
        return
    t = getattr(instance, "tournament", None)
    if t and _is_fan(t):
        return
    if t and _is_round_robin(t):
        win_points, lose_points = 1, 0
    else:
        win_points = getattr(t, "points_winner", 100)
        lose_points = getattr(t, "points_loser", -50)
    if instance.winner_team_id:
        instance.points_player1 = win_points if instance.winner_team_id == instance.team1_id else lose_points
        instance.points_player2 = lose_points if instance.winner_team_id == instance.team1_id else win_points
    elif instance.winner_id == instance.player1_id:
        instance.points_player1 = win_points
        instance.points_player2 = lose_points
    elif instance.winner_id == instance.player2_id:
        instance.points_player1 = lose_points
        instance.points_player2 = win_points


@receiver(post_save, sender=Match)
def update_player_stats(sender, instance, created, **kwargs):
    """
    Update matches_played / matches_won when match is completed.
    total_points для FAN не трогаем — начисление при вылете/завершении турнира.
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

    if t and _is_olympic(t):
        if is_doubles:
            for p in (instance.team1.player1, instance.team1.player2) if winner_team == instance.team1 else (instance.team2.player1, instance.team2.player2):
                if p and not getattr(p, "is_bye", False):
                    p.matches_played += 1
                    p.matches_won += 1
                    p.save(update_fields=["matches_played", "matches_won"])
            loser_team = instance.team2 if winner_team == instance.team1 else instance.team1
            for p in (loser_team.player1, loser_team.player2):
                if p and not getattr(p, "is_bye", False):
                    p.matches_played += 1
                    p.save(update_fields=["matches_played"])
        else:
            winner.matches_played += 1
            winner.matches_won += 1
            winner.save(update_fields=["matches_played", "matches_won"])
            loser = instance.player2 if winner == instance.player1 else instance.player1
            if not getattr(loser, "is_bye", False):
                loser.matches_played += 1
                loser.save(update_fields=["matches_played"])
        advance_winner_olympic(instance)
        if instance.round_index >= 1 and not instance.is_consolation:
            ensure_consolation_created_for_round(t, instance.round_index)
        return

    if t and _is_fan(t):
        if is_doubles:
            for p in (instance.team1.player1, instance.team1.player2) if winner_team == instance.team1 else (instance.team2.player1, instance.team2.player2):
                if p and not getattr(p, "is_bye", False):
                    p.matches_played += 1
                    p.matches_won += 1
                    p.save(update_fields=["matches_played", "matches_won"])
            loser_team = instance.team2 if winner_team == instance.team1 else instance.team1
            for p in (loser_team.player1, loser_team.player2):
                if p and not getattr(p, "is_bye", False):
                    p.matches_played += 1
                    p.save(update_fields=["matches_played"])
        else:
            winner.matches_played += 1
            winner.matches_won += 1
            winner.save(update_fields=["matches_played", "matches_won"])
            loser = instance.player2 if winner == instance.player1 else instance.player1
            if not getattr(loser, "is_bye", False):
                loser.matches_played += 1
                loser.save(update_fields=["matches_played"])
        advance_winner_and_award_loser(instance)
        if instance.round_index == 1 and not instance.is_consolation:
            ensure_consolation_created(t)
        finalize_tournament(t)
        return

    if t and _is_round_robin(t):
        side1_won = winner_team == instance.team1 if winner_team else winner == instance.player1
        win_pts = instance.points_player1 if side1_won else instance.points_player2
        lose_pts = instance.points_player2 if side1_won else instance.points_player1
        if is_doubles:
            for p in (instance.team1.player1, instance.team1.player2) if winner_team == instance.team1 else (instance.team2.player1, instance.team2.player2):
                if p and not getattr(p, "is_bye", False):
                    p.matches_played += 1
                    p.matches_won += 1
                    p.total_points += win_pts
                    p.save(update_fields=["matches_played", "matches_won", "total_points"])
            loser_team = instance.team2 if winner_team == instance.team1 else instance.team1
            for p in (loser_team.player1, loser_team.player2):
                if p and not getattr(p, "is_bye", False):
                    p.matches_played += 1
                    p.total_points += lose_pts
                    p.save(update_fields=["matches_played", "total_points"])
        else:
            winner.matches_played += 1
            winner.matches_won += 1
            winner.save(update_fields=["matches_played", "matches_won"])
            loser = instance.player2 if winner == instance.player1 else instance.player1
            if not getattr(loser, "is_bye", False):
                loser.matches_played += 1
                loser.save(update_fields=["matches_played"])
            winner.total_points += win_pts
            winner.save(update_fields=["total_points"])
        check_and_finalize_if_complete(t)
        return

    player1 = instance.player1
    player2 = instance.player2
    if winner == player1:
        loser = player2
        winner_points = instance.points_player1
        loser_points = instance.points_player2
    else:
        loser = player1
        winner_points = instance.points_player2
        loser_points = instance.points_player1

    winner.matches_played += 1
    winner.matches_won += 1
    winner.total_points += winner_points
    winner.save()
    loser.matches_played += 1
    loser.total_points += loser_points
    loser.save()


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
