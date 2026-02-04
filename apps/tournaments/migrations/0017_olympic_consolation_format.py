# Generated manually: Olympic consolation format

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0016_add_doubles_variant"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="placement_max",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Олимпийская система: верхняя граница места (напр. 8).",
                null=True,
                verbose_name="Максимальное место (диапазон)",
            ),
        ),
        migrations.AddField(
            model_name="match",
            name="placement_min",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Олимпийская система: за какое место идёт борьба (напр. 5 для сетки 5–8).",
                null=True,
                verbose_name="Минимальное место (диапазон)",
            ),
        ),
        migrations.AddField(
            model_name="match",
            name="loser_next_match",
            field=models.ForeignKey(
                blank=True,
                help_text="Для олимпийской системы: матч за следующее место (утешительная сетка).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="prev_matches_loser",
                to="tournaments.match",
                verbose_name="Следующий матч (проигравший)",
            ),
        ),
        migrations.AddField(
            model_name="tournamentplayerresult",
            name="place",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Олимпийская система: занятое место (1, 2, 3, …).",
                null=True,
                verbose_name="Итоговое место",
            ),
        ),
        migrations.AlterField(
            model_name="tournamentplayerresult",
            name="round_eliminated",
            field=models.CharField(
                blank=True,
                choices=[
                    ("r1", "1 круг"),
                    ("r2", "2 круг"),
                    ("sf", "Полуфинал"),
                    ("final", "Финал"),
                    ("winner", "Победитель"),
                ],
                max_length=10,
                verbose_name="Раунд вылета",
            ),
        ),
        migrations.AlterField(
            model_name="tournament",
            name="format",
            field=models.CharField(
                choices=[
                    ("single_elimination", "FAN (одноэтапная сетка)"),
                    ("olympic_consolation", "Олимпийская система (утешительная сетка)"),
                    ("round_robin", "Круговой"),
                ],
                default="single_elimination",
                help_text="FAN: одноэтапная сетка, посев по рейтингу, очки при вылете. \nКруговой: все играют со всеми, итоговая таблица по очкам.",
                max_length=20,
                verbose_name="Формат",
            ),
        ),
    ]
