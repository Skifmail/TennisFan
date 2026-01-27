from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Match

@receiver(pre_save, sender=Match)
def prepare_match_completion(sender, instance, **kwargs):
    """
    Handle pre-save logic:
    1. Store old status to detect transitions.
    2. Auto-calculate points if match is completing.
    """
    if instance.pk:
        try:
            old_instance = Match.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except Match.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

    # If transitioning to COMPLETED (or Walkover)
    if instance.status in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]:
        # If points are not set (both 0), calculate them
        if instance.points_player1 == 0 and instance.points_player2 == 0:
            win_points = getattr(instance.tournament, "points_winner", 100)
            lose_points = getattr(instance.tournament, "points_loser", -50)
            
            if instance.winner == instance.player1:
                instance.points_player1 = win_points
                instance.points_player2 = lose_points
            elif instance.winner == instance.player2:
                instance.points_player1 = lose_points
                instance.points_player2 = win_points

@receiver(post_save, sender=Match)
def update_player_stats(sender, instance, created, **kwargs):
    """
    Update player statistics when match is completed.
    """
    old_status = getattr(instance, '_old_status', None)
    
    # Check if status CHANGED to COMPLETED/WALKOVER
    is_completed = instance.status in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
    was_completed = old_status in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
    
    if is_completed and not was_completed:
        winner = instance.winner
        if not winner:
            return

        player1 = instance.player1
        player2 = instance.player2
        
        # Identify loser
        if winner == player1:
            loser = player2
            winner_points = instance.points_player1
            loser_points = instance.points_player2
        else:
            loser = player1
            winner_points = instance.points_player2
            loser_points = instance.points_player1
        
        # Update stats
        winner.matches_played += 1
        winner.matches_won += 1
        winner.total_points += winner_points
        winner.save()
        
        loser.matches_played += 1
        loser.total_points += loser_points
        loser.save()
