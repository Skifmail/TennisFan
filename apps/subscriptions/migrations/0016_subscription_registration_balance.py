from django.db import migrations, models


def convert_used_registrations_to_balance(apps, schema_editor):
    """Преобразовать старый счётчик использованных регистраций в остаток слотов."""
    UserSubscription = apps.get_model("subscriptions", "UserSubscription")

    for subscription in (
        UserSubscription.objects.select_related("tier").all().iterator()
    ):
        tier = getattr(subscription, "tier", None)
        if tier is None:
            subscription.tournament_registration_balance = 0
        elif (
            getattr(tier, "is_unlimited", False)
            or getattr(tier, "max_tournaments", 0) == 0
        ):
            subscription.tournament_registration_balance = 0
        else:
            used_registrations = int(subscription.tournament_registration_balance)
            tier_slots = int(getattr(tier, "max_tournaments", 0))
            subscription.tournament_registration_balance = max(
                tier_slots - used_registrations,
                0,
            )
        subscription.save(update_fields=["tournament_registration_balance"])


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0015_add_platinum_card_theme"),
    ]

    operations = [
        migrations.RenameField(
            model_name="usersubscription",
            old_name="tournaments_registered_count",
            new_name="tournament_registration_balance",
        ),
        migrations.RunPython(
            convert_used_registrations_to_balance,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="subscriptiontier",
            name="max_tournaments",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Сколько регистраций на турниры начисляется при покупке или продлении этого тарифа. 0 = регистрации запрещены.",
                verbose_name="Количество регистраций за покупку",
            ),
        ),
        migrations.AlterField(
            model_name="usersubscription",
            name="tournament_registration_balance",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Баланс регистраций, который пополняется при покупке подписки и расходуется при записи на многодневные турниры.",
                verbose_name="Остаток регистраций на турниры",
            ),
        ),
    ]
