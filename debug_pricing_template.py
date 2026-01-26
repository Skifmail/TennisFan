import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.template.loader import get_template
from django.http import HttpRequest
from django.contrib.auth.models import AnonymousUser

class MockTier:
    def __init__(self, name, price):
        self.name = name
        self.price = price
        self.is_unlimited = False
        self.max_tournaments = 3
        self.one_day_tournament_discount = 0
        self.has_badge = False
        self.pk = 1
        self.id = 1
    
    def get_name_display(self):
        return self.name.capitalize()

tiers = [
    MockTier('silver', 1000),
    MockTier('diamond', 5000),
]

request = HttpRequest()
request.user = AnonymousUser()

try:
    template = get_template('subscriptions/pricing.html')
    context = {'tiers': tiers, 'current_tier_id': None}
    print("Rendering with Mock Tiers...")
    rendered = template.render(context, request)
    print("Success Mock Tiers (Diamond check: 'box-shadow' in rendered output?)")
    if 'box-shadow' in rendered:
        print("Found box-shadow!")
    else:
        print("box-shadow NOT found!")
except Exception as e:
    import traceback
    traceback.print_exc()
