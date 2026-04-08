from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0027_ntrp_test_result"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="is_hidden_on_home",
            field=models.BooleanField(
                default=False,
                help_text="Если включено, игрок не отображается в блоке рейтинга на главной странице.",
                verbose_name="Не показывать на главной",
            ),
        ),
    ]
