from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0013_member_balance"),
    ]

    operations = [
        migrations.AddField(
            model_name="club",
            name="use_player_plans",
            field=models.BooleanField(
                default=True,
                help_text="Если выключено, тарифы не ограничивают регистрацию на турниры и не обязательны для участников клуба.",
                verbose_name="Использовать клубные тарифы",
            ),
        ),
    ]
