"""Добавить оплату на корте отдельно от онлайн-оплат и постоплатных счетов."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tournaments", "0076_tournament_start_time"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="allow_onsite_payment",
            field=models.BooleanField(
                default=False,
                verbose_name="Оплата на корте",
                help_text=(
                    "Разрешить участнику выбрать оплату взноса на площадке. "
                    "Регистрация не означает получение денег: организатор подтверждает его отдельно. "
                    "Не создаёт долг постоплаты и не списывает FT."
                ),
            ),
        ),
        migrations.CreateModel(
            name="TournamentOnsitePayment",
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
                    "amount",
                    models.DecimalField(
                        decimal_places=2, max_digits=10, verbose_name="Сумма (руб)"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("expected", "Ожидается на корте"),
                            ("received", "Получено на корте"),
                            ("cancelled", "Ожидание отменено"),
                            ("replaced", "Оплачено онлайн"),
                        ],
                        default="expected",
                        max_length=16,
                        verbose_name="Статус",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Выбрано"),
                ),
                (
                    "received_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Получено"
                    ),
                ),
                (
                    "closed_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Ожидание закрыто"
                    ),
                ),
                (
                    "closed_reason",
                    models.CharField(
                        blank=True, max_length=32, verbose_name="Причина закрытия"
                    ),
                ),
                (
                    "received_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="confirmed_tournament_onsite_payments",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кто подтвердил получение",
                    ),
                ),
                (
                    "tournament",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="onsite_payments",
                        to="tournaments.tournament",
                        verbose_name="Турнир",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tournament_onsite_payments",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Участник",
                    ),
                ),
            ],
            options={
                "verbose_name": "Оплата турнира на корте",
                "verbose_name_plural": "Оплаты турниров на корте",
                "ordering": ["-created_at", "-pk"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tournament", "user"),
                        condition=models.Q(status__in=["expected", "received"]),
                        name="uniq_active_tournament_onsite_payment",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(amount__gte=0),
                        name="tournament_onsite_amount_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(status="received", received_at__isnull=False)
                            | (
                                ~models.Q(status="received")
                                & models.Q(received_at__isnull=True)
                            )
                        ),
                        name="tournament_onsite_receipt_timestamp",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="tournamententryrefundrequest",
            name="onsite_payment",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="refund_request",
                to="tournaments.tournamentonsitepayment",
                verbose_name="Полученная оплата на корте",
                help_text="Заявка на ручной возврат не меняет историю получения денег.",
            ),
        ),
    ]
