"""
Sparring app utilities.
"""

from apps.subscriptions.models import SubscriptionTier


def user_has_sparring_access(user) -> bool:
    """
    Return True if user has access to sparring (create, respond) based on subscription tier.
    Requires tier.has_sparring; free tier and no subscription = no access.
    """
    if not user or not user.is_authenticated:
        return False
    try:
        sub = user.subscription
        if not sub.is_valid():
            return False
        return bool(sub.tier.has_sparring)
    except Exception:
        return False
