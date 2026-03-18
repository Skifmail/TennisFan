# Data migration: начальные тарифы платформы (Старт, Базовый, Про)

from decimal import Decimal

from django.db import migrations


def create_platform_plans(apps, schema_editor):
    """Создаёт три тарифа платформы с дефолтными значениями."""
    PlatformPlan = apps.get_model("clubs", "PlatformPlan")
    plans = [
        {
            "slug": "start",
            "name": "Старт",
            "description": "Пробный период 14 дней бесплатно",
            "price_monthly": Decimal("990"),
            "price_yearly": Decimal("9900"),
            "max_tournaments_per_month": 1,
            "trial_days": 14,
            "is_public_page": False,
            "is_open_interclub": False,
            "sort_order": 0,
        },
        {
            "slug": "basic",
            "name": "Базовый",
            "description": "Публичная страница, больше игроков",
            "price_monthly": Decimal("1990"),
            "price_yearly": Decimal("19900"),
            "max_tournaments_per_month": 5,
            "trial_days": 0,
            "is_public_page": True,
            "is_open_interclub": False,
            "sort_order": 1,
        },
        {
            "slug": "pro",
            "name": "Про",
            "description": "Всё + межклубные турниры",
            "price_monthly": Decimal("4990"),
            "price_yearly": Decimal("49900"),
            "max_tournaments_per_month": None,
            "trial_days": 0,
            "is_public_page": True,
            "is_open_interclub": True,
            "sort_order": 2,
        },
    ]
    for p in plans:
        PlatformPlan.objects.get_or_create(slug=p["slug"], defaults=p)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("clubs", "0006_add_platform_plan"),
    ]

    operations = [
        migrations.RunPython(create_platform_plans, noop),
    ]
