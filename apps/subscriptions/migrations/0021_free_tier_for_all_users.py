"""Бессрочный Free: пустая дата окончания и назначение тарифа всем пользователям."""

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def assign_free_tier(apps, schema_editor) -> None:
    """Выдать Free тем, у кого нет подписки, и вернуть его истёкшим платным.

    Args:
        apps: Реестр исторических моделей миграции.
        schema_editor: Редактор схемы. Не используется.
    """
    User = apps.get_model("users", "User")
    SubscriptionTier = apps.get_model("subscriptions", "SubscriptionTier")
    UserSubscription = apps.get_model("subscriptions", "UserSubscription")
    free = SubscriptionTier.objects.filter(name="free").first()
    if free is None:
        return

    now = timezone.now()
    UserSubscription.objects.filter(tier=free).update(
        end_date=None,
        is_active=True,
        cancelled_at=None,
    )

    UserSubscription.objects.exclude(tier=free).filter(Q(end_date__lte=now)).update(
        tier=free,
        is_active=True,
        cancelled_at=None,
        end_date=None,
        start_date=now,
    )

    missing_users = User.objects.exclude(
        pk__in=UserSubscription.objects.values("user_id")
    ).iterator()
    batch: list = []
    for user in missing_users:
        batch.append(
            UserSubscription(
                user=user,
                tier=free,
                is_active=True,
                start_date=now,
                end_date=None,
            )
        )
        if len(batch) >= 500:
            UserSubscription.objects.bulk_create(batch)
            batch = []
    if batch:
        UserSubscription.objects.bulk_create(batch)


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0020_rename_city_verbose_to_locality"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usersubscription",
            name="end_date",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Дата окончания",
            ),
        ),
        migrations.RunPython(assign_free_tier, migrations.RunPython.noop),
    ]
