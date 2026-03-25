from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0012_clubmemberplan_bonus_tournaments_balance"),
    ]

    operations = [
        migrations.AddField(
            model_name="clubmember",
            name="balance",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=10,
                verbose_name="Баланс игрока",
            ),
        ),
        migrations.CreateModel(
            name="ClubMemberBalanceTransaction",
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
                    "direction",
                    models.CharField(
                        choices=[("credit", "Пополнение"), ("debit", "Списание")],
                        max_length=12,
                        verbose_name="Направление",
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("tournament_payment", "Оплата турнира"),
                            ("tournament_refund", "Возврат за турнир"),
                            ("club_plan_payment", "Оплата тарифа клуба"),
                            ("club_fee_payment", "Оплата членского взноса"),
                            ("manual", "Ручная корректировка"),
                        ],
                        max_length=32,
                        verbose_name="Источник",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Ожидает"),
                            ("completed", "Завершена"),
                            ("cancelled", "Отменена"),
                        ],
                        default="completed",
                        max_length=16,
                        verbose_name="Статус",
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        verbose_name="Сумма",
                    ),
                ),
                (
                    "description",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="Описание",
                    ),
                ),
                (
                    "reference",
                    models.CharField(
                        blank=True,
                        max_length=128,
                        verbose_name="Внешний идентификатор",
                    ),
                ),
                (
                    "metadata",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        verbose_name="Дополнительные данные",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "completed_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Завершено",
                    ),
                ),
                (
                    "club",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="member_balance_transactions",
                        to="clubs.club",
                        verbose_name="Клуб",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="balance_transactions",
                        to="clubs.clubmember",
                        verbose_name="Участник",
                    ),
                ),
            ],
            options={
                "verbose_name": "Операция по балансу участника клуба",
                "verbose_name_plural": "Операции по балансу участников клуба",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
