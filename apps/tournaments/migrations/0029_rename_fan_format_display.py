# Rename format display: "FAN (одноэтапная сетка)" -> "Одноэтапная сетка"

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0028_add_match_type_and_sparring_support"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tournament",
            name="format",
            field=models.CharField(
                choices=[
                    ("single_elimination", "Одноэтапная сетка"),
                    ("olympic_consolation", "Олимпийская система (утешительная сетка)"),
                    ("round_robin", "Круговой"),
                ],
                default="single_elimination",
                help_text="Одноэтапная сетка: турнир на выбывание с подвалом, посев по рейтингу, очки при вылете. \nКруговой: все играют со всеми, итоговая таблица по очкам.",
                max_length=20,
                verbose_name="Формат",
            ),
        ),
    ]
