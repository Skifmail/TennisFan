"""
Context processors for users app.
"""

from django.core.cache import cache

from .models import Notification

UNREAD_NOTIFICATIONS_CACHE_KEY_PREFIX = "unread_notifications_count"


def unread_notifications(request):
    """Add unread notifications count to context."""
    if request.user.is_authenticated:
        cache_key = f"{UNREAD_NOTIFICATIONS_CACHE_KEY_PREFIX}:{request.user.id}"
        unread_count = cache.get_or_set(
            cache_key,
            lambda: Notification.objects.filter(
                user=request.user,
                is_read=False,
            ).count(),
            timeout=30,
        )
        return {"unread_notifications_count": unread_count}
    return {"unread_notifications_count": 0}


def user_is_coach(request):
    """Add user_is_coach flag for templates (e.g. hide «Стать тренером» if already coach)."""
    if not request.user.is_authenticated:
        return {"user_is_coach": False}
    from apps.training.models import Coach

    return {"user_is_coach": Coach.objects.filter(user=request.user).exists()}
