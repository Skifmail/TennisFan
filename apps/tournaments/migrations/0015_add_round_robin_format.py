# Generated manually: add round-robin format and match_format

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0014_remove_tournament_format_other"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="match_format",
            field=models.CharField(
                blank=True,
                choices=[
                    ("1_set_6", "1 сет до 6 геймов"),
                    ("1_set_tiebreak", "1 сет с тай-брейком"),
                    ("2_sets", "2 сета до победы"),
                    ("fast4", "2 коротких сета + супертай-брейк"),
                ],
                help_text="Для круговых турниров: 1 сет до 6, с тай-брейком, 2 сета или Fast4.",
                max_length=20,
                verbose_name="Формат матча",
            ),
        ),
        migrations.AlterField(
            model_name="tournament",
            name="format",
            field=models.CharField(
                choices=[
                    ("single_elimination", "FAN (одноэтапная сетка)"),
                    ("round_robin", "Круговой"),
                ],
                default="single_elimination",
                help_text="FAN: одноэтапная сетка, посев по рейтингу, очки при вылете.",
                max_length=20,
                verbose_name="Формат",
            ),
        ),
    ]
