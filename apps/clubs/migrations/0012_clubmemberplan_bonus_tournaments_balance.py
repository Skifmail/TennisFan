from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0011_clubjoinrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="clubmemberplan",
            name="bonus_tournaments_balance",
            field=models.PositiveIntegerField(
                default=0,
                verbose_name="Перенесённый остаток регистраций",
            ),
        ),
    ]
