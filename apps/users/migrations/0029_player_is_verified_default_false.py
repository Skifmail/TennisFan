from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0028_player_is_hidden_on_home"),
    ]

    operations = [
        migrations.AlterField(
            model_name="player",
            name="is_verified",
            field=models.BooleanField(default=False, verbose_name="Подтверждён"),
        ),
    ]
