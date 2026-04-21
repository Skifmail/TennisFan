from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("courts", "0010_alter_courtphoto_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="courtapplication",
            name="is_outdoor",
            field=models.BooleanField(
                default=False,
                help_text="Можно выбрать одновременно с «Крытый», если есть оба формата.",
                verbose_name="Открытый",
            ),
        ),
    ]
