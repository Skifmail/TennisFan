# Minimum participants/teams and insufficient-participants notification

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0017_olympic_consolation_format"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="min_participants",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Если к дедлайну регистрации меньше — админу уйдёт уведомление в Telegram; через 3 часа без продления турнир отменяется, лимиты регистраций возвращаются.",
                null=True,
                verbose_name="Минимальное количество участников",
            ),
        ),
        migrations.AddField(
            model_name="tournament",
            name="min_teams",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Для парных: если к дедлайну меньше — уведомление админу, через 3 ч без продления — отмена турнира.",
                null=True,
                verbose_name="Минимальное количество команд",
            ),
        ),
        migrations.AddField(
            model_name="tournament",
            name="insufficient_participants_notified_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Заполняется автоматически при первом срабатывании; сбрасывается при продлении дедлайна.",
                null=True,
                verbose_name="Когда отправлено уведомление о недостатке участников",
            ),
        ),
    ]
