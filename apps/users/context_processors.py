"""
Context processors for users app.
"""

from .models import Notification


def unread_notifications(request):
    """Add unread notifications count to context."""
    if request.user.is_authenticated:
        unread_count = Notification.objects.filter(
            user=request.user,
            is_read=False,
        ).count()
        return {"unread_notifications_count": unread_count}
    return {"unread_notifications_count": 0}


def user_is_coach(request):
    """Add user_is_coach flag for templates (e.g. hide «Стать тренером» if already coach)."""
    if not request.user.is_authenticated:
        return {"user_is_coach": False}
    from apps.training.models import Coach

    return {"user_is_coach": Coach.objects.filter(user=request.user).exists()}
