# Generated manually for SparringInvitation

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sparring", "0013_doublesmatchrequest_kind"),
        ("tournaments", "0042_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SparringInvitation",
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
                    "is_friendly",
                    models.BooleanField(
                        default=False,
                        help_text="Без влияния на рейтинг и силу",
                        verbose_name="Дружеская игра",
                    ),
                ),
                (
                    "proposed_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        verbose_name="Предполагаемая дата игры",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Ожидает ответа"),
                            ("accepted", "Принято"),
                            ("rejected", "Отклонено"),
                            ("cancelled", "Отменено"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                        verbose_name="Статус",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
                ),
                (
                    "invitee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sparring_invitations_received",
                        to="users.player",
                        verbose_name="Кого пригласили",
                    ),
                ),
                (
                    "inviter",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sparring_invitations_sent",
                        to="users.player",
                        verbose_name="Кто пригласил",
                    ),
                ),
                (
                    "match",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sparring_invitation_records",
                        to="tournaments.match",
                        verbose_name="Матч",
                    ),
                ),
            ],
            options={
                "verbose_name": "Приглашение на спарринг",
                "verbose_name_plural": "Приглашения на спарринг",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="sparringinvitation",
            index=models.Index(
                fields=["inviter", "status"], name="sparring_sp_inviter_4f8a1d_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="sparringinvitation",
            index=models.Index(
                fields=["invitee", "status"], name="sparring_sp_invitee_8b2c3e_idx"
            ),
        ),
    ]
