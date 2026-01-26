import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.subscriptions.models import UserSubscription, SubscriptionTier

User = get_user_model()
try:
    admin = User.objects.filter(is_superuser=True).first()
    if admin:
        print(f"Admin: {admin}")
        try:
            sub = admin.subscription
            print(f"Subscription: {sub}")
            print(f"Tier: {sub.tier.name}")
            print(f"Is Valid: {sub.is_valid()}")
        except Exception as e:
            print(f"No subscription or error: {e}")
except Exception as e:
    print(e)
