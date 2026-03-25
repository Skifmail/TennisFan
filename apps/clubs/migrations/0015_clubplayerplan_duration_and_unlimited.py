from django.db import migrations, models


def populate_unlimited_registrations(apps, schema_editor) -> None:
    """Заполняет флаг безлимитных регистраций из старых данных."""
    ClubPlayerPlan = apps.get_model("clubs", "ClubPlayerPlan")
    ClubPlayerPlan.objects.filter(max_tournaments_per_month__isnull=True).update(
        has_unlimited_registrations=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0014_club_use_player_plans"),
    ]

    operations = [
        migrations.AddField(
            model_name="clubplayerplan",
            name="duration_days",
            field=models.PositiveIntegerField(
                default=30,
                help_text="Сколько дней действует оплаченный тариф.",
                verbose_name="Срок действия в днях",
            ),
        ),
        migrations.AddField(
            model_name="clubplayerplan",
            name="has_unlimited_registrations",
            field=models.BooleanField(
                default=False,
                help_text="Если включено, лимит турниров в месяц не применяется.",
                verbose_name="Безлимитные регистрации",
            ),
        ),
        migrations.AlterField(
            model_name="clubplayerplan",
            name="max_tournaments_per_month",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Обязателен, если безлимитные регистрации выключены.",
                null=True,
                verbose_name="Лимит турниров в месяц",
            ),
        ),
        migrations.RunPython(
            populate_unlimited_registrations,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="clubplayerplan",
            constraint=models.CheckConstraint(
                condition=models.Q(duration_days__gte=1),
                name="club_player_plan_duration_days_gte_1",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubplayerplan",
            constraint=models.CheckConstraint(
                condition=models.Q(has_unlimited_registrations=True)
                | models.Q(max_tournaments_per_month__isnull=False),
                name="club_player_plan_limit_required_without_unlimited",
            ),
        ),
    ]
