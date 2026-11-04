from django.db import migrations, models


class Migration(migrations.Migration):
    """Добавляет ручной режим формирования пар в турнирах."""

    dependencies = [
        ("tournaments", "0067_alter_sparringdeadlineextension_player"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="pairing_mode",
            field=models.CharField(
                choices=[("auto", "Автоматический"), ("manual", "Ручной")],
                default="auto",
                editable=False,
                help_text=(
                    "Автоматический — система сама строит сетку и распределяет пары. "
                    "Ручной — организатор запускает турнир и сам составляет пары "
                    "из зарегистрированных участников."
                ),
                max_length=10,
                verbose_name="Режим формирования пар",
            ),
        ),
        migrations.AddField(
            model_name="tournament",
            name="manual_pairing_auto_next_rounds",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Только для ручного режима: если включено, после первого круга "
                    "система сама составит следующие пары по результатам матчей."
                ),
                verbose_name="Следующие круги распределять автоматически",
            ),
        ),
        migrations.AddField(
            model_name="tournament",
            name="manual_started_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Заполняется, когда организатор запускает турнир в ручном режиме. "
                    "Матчи появляются после сохранения первого круга пар."
                ),
                null=True,
                verbose_name="Ручной запуск турнира",
            ),
        ),
    ]
