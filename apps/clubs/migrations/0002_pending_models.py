"""
Миграция: добавление моделей Pending для отслеживания платежей взносов и подписок.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClubFeePaymentPending",
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
                    "payment_id",
                    models.CharField(
                        max_length=255, unique=True, verbose_name="ID платежа ЮKassa"
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2, max_digits=10, verbose_name="Сумма"
                    ),
                ),
                (
                    "period_label",
                    models.CharField(
                        max_length=50, verbose_name="Период (напр. 2026-03)"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Дата создания"
                    ),
                ),
                (
                    "club",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_payments_pending",
                        to="clubs.club",
                        verbose_name="Клуб",
                    ),
                ),
                (
                    "fee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payments_pending",
                        to="clubs.clubmembershipfee",
                        verbose_name="Настройка взноса",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_payments_pending",
                        to="clubs.clubmember",
                        verbose_name="Участник",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ожидающий платёж взноса",
                "verbose_name_plural": "Ожидающие платежи взносов",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ClubSubscriptionPaymentPending",
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
                    "payment_id",
                    models.CharField(
                        max_length=255, unique=True, verbose_name="ID платежа ЮKassa"
                    ),
                ),
                (
                    "plan",
                    models.CharField(max_length=20, verbose_name="Тариф"),
                ),
                (
                    "period",
                    models.CharField(max_length=20, verbose_name="Период оплаты"),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2, max_digits=10, verbose_name="Сумма"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Дата создания"
                    ),
                ),
                (
                    "club",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription_payments_pending",
                        to="clubs.club",
                        verbose_name="Клуб",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ожидающий платёж подписки",
                "verbose_name_plural": "Ожидающие платежи подписок",
                "ordering": ["-created_at"],
            },
        ),
    ]
