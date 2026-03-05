from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0026_has_ever_paid_subscription"),
    ]

    operations = [
        migrations.CreateModel(
            name="NtrpTestResult",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Дата прохождения"
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("registration", "Регистрация"),
                            ("manual_test", "Тест без изменения рейтинга"),
                        ],
                        default="registration",
                        max_length=32,
                        verbose_name="Источник",
                    ),
                ),
                (
                    "total_score",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        verbose_name="Суммарные баллы по тесту",
                    ),
                ),
                (
                    "level",
                    models.DecimalField(
                        blank=True,
                        decimal_places=1,
                        max_digits=3,
                        null=True,
                        verbose_name="Рассчитанный уровень силы (NTRP)",
                    ),
                ),
                (
                    "starting_points",
                    models.FloatField(
                        blank=True,
                        help_text="Значение рейтинга FAN, установленное на основании этого теста (если применимо).",
                        null=True,
                        verbose_name="Стартовый рейтинг FAN",
                    ),
                ),
                (
                    "applied_to_rating",
                    models.BooleanField(
                        default=False,
                        help_text="Показывает, использовался ли этот тест для установки стартового рейтинга игрока.",
                        verbose_name="Применён к рейтингу игрока",
                    ),
                ),
                (
                    "answers",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            "Структура: список словарей с вопросом, выбранным вариантом и баллами. "
                            "Например: [{'index': 0, 'question': 'Опыт игры', 'option_index': 2, "
                            "'option_label': '...', 'option_score': 30}, ...]."
                        ),
                        verbose_name="Ответы по вопросам",
                    ),
                ),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="ntrp_tests",
                        to="users.player",
                        verbose_name="Игрок",
                    ),
                ),
            ],
            options={
                "verbose_name": "Результат теста NTRP",
                "verbose_name_plural": "Результаты теста NTRP",
                "ordering": ["-created_at"],
            },
        ),
    ]
