"""Миграция Phase 8: тарифы игроков клуба и учёт лимитов."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tournaments", "0042_initial"),
        ("clubs", "0004_platform_audit_and_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClubPlayerPlan",
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
                    "name",
                    models.CharField(max_length=120, verbose_name="Название тарифа"),
                ),
                ("description", models.TextField(blank=True, verbose_name="Описание")),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Активен"),
                ),
                (
                    "monthly_fee",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=10,
                        verbose_name="Ежемесячный взнос",
                    ),
                ),
                (
                    "max_tournaments_per_month",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        verbose_name="Лимит турниров в месяц",
                    ),
                ),
                (
                    "monthly_slots",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Слотов в месяц"
                    ),
                ),
                (
                    "allow_rollover_slots",
                    models.BooleanField(
                        default=False,
                        verbose_name="Разрешить перенос слотов на следующий месяц",
                    ),
                ),
                (
                    "rollover_cap",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Лимит переносимых слотов"
                    ),
                ),
                (
                    "allow_self_change",
                    models.BooleanField(
                        default=True,
                        verbose_name="Разрешить самостоятельную смену тарифа игроком",
                    ),
                ),
                (
                    "sort_order",
                    models.PositiveIntegerField(default=0, verbose_name="Порядок"),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Дата создания"
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Дата обновления"),
                ),
                (
                    "club",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="player_plans",
                        to="clubs.club",
                        verbose_name="Клуб",
                    ),
                ),
            ],
            options={
                "verbose_name": "Клубный тариф игрока",
                "verbose_name_plural": "Клубные тарифы игроков",
                "db_table": "club_plans",
                "ordering": ["club", "sort_order", "name", "id"],
            },
        ),
        migrations.CreateModel(
            name="ClubMemberPlan",
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
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Активен"),
                            ("pending", "Ожидает активации"),
                            ("ended", "Завершён"),
                        ],
                        default="active",
                        max_length=20,
                        verbose_name="Статус",
                    ),
                ),
                (
                    "started_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Дата начала"),
                ),
                (
                    "ended_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Дата окончания"
                    ),
                ),
                (
                    "auto_renew",
                    models.BooleanField(default=True, verbose_name="Автопродление"),
                ),
                (
                    "change_reason",
                    models.CharField(
                        blank=True, max_length=255, verbose_name="Причина изменения"
                    ),
                ),
                (
                    "assigned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="assigned_club_member_plans",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Назначил",
                    ),
                ),
                (
                    "club_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plan_assignments",
                        to="clubs.clubmember",
                        verbose_name="Участник клуба",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="member_assignments",
                        to="clubs.clubplayerplan",
                        verbose_name="Тариф",
                    ),
                ),
            ],
            options={
                "verbose_name": "Тариф участника клуба",
                "verbose_name_plural": "Тарифы участников клуба",
                "db_table": "club_member_plans",
                "ordering": ["-started_at", "id"],
            },
        ),
        migrations.CreateModel(
            name="ClubPlanTournamentAccess",
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
                    "is_allowed",
                    models.BooleanField(default=True, verbose_name="Доступ разрешён"),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Дата создания"
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Дата обновления"),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tournament_access_rules",
                        to="clubs.clubplayerplan",
                        verbose_name="Тариф",
                    ),
                ),
                (
                    "tournament",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="club_plan_access_rules",
                        to="tournaments.tournament",
                        verbose_name="Турнир",
                    ),
                ),
            ],
            options={
                "verbose_name": "Доступ тарифа к турниру",
                "verbose_name_plural": "Доступы тарифов к турнирам",
                "db_table": "club_plan_tournament_access",
                "ordering": ["plan", "tournament"],
                "unique_together": {("plan", "tournament")},
            },
        ),
        migrations.CreateModel(
            name="ClubPlanSlotUsage",
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
                    "period_year",
                    models.PositiveIntegerField(verbose_name="Год периода"),
                ),
                (
                    "period_month",
                    models.PositiveSmallIntegerField(verbose_name="Месяц периода"),
                ),
                (
                    "tournaments_used",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Турниров использовано"
                    ),
                ),
                (
                    "slots_used",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Слотов использовано"
                    ),
                ),
                (
                    "rollover_in",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Перенесено в период"
                    ),
                ),
                (
                    "rollover_out",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Перенесено из периода"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Дата создания"
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Дата обновления"),
                ),
                (
                    "club_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plan_slot_usages",
                        to="clubs.clubmember",
                        verbose_name="Участник клуба",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="slot_usages",
                        to="clubs.clubplayerplan",
                        verbose_name="Тариф",
                    ),
                ),
            ],
            options={
                "verbose_name": "Учёт слотов тарифа",
                "verbose_name_plural": "Учёт слотов тарифов",
                "db_table": "club_plan_slot_usage",
                "ordering": ["-period_year", "-period_month", "club_member_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="clubplayerplan",
            constraint=models.UniqueConstraint(
                fields=("club", "name"), name="uniq_club_player_plan_name"
            ),
        ),
        migrations.AddConstraint(
            model_name="clubplayerplan",
            constraint=models.CheckConstraint(
                condition=models.Q(monthly_fee__gte=0),
                name="club_player_plan_monthly_fee_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubplayerplan",
            constraint=models.CheckConstraint(
                condition=models.Q(max_tournaments_per_month__gte=0)
                | models.Q(max_tournaments_per_month__isnull=True),
                name="club_player_plan_tournaments_gte_0_or_null",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubplayerplan",
            constraint=models.CheckConstraint(
                condition=models.Q(monthly_slots__gte=0),
                name="club_player_plan_monthly_slots_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubplayerplan",
            constraint=models.CheckConstraint(
                condition=models.Q(rollover_cap__gte=0),
                name="club_player_plan_rollover_cap_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubplayerplan",
            constraint=models.CheckConstraint(
                condition=models.Q(allow_rollover_slots=True)
                | models.Q(rollover_cap=0),
                name="club_player_plan_rollover_cap_when_disabled",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubmemberplan",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="active"),
                fields=("club_member",),
                name="uniq_active_plan_per_member",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubplanslotusage",
            constraint=models.UniqueConstraint(
                fields=("club_member", "period_year", "period_month"),
                name="uniq_slot_usage_period",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubplanslotusage",
            constraint=models.CheckConstraint(
                condition=models.Q(period_month__gte=1, period_month__lte=12),
                name="club_plan_slot_usage_period_month_1_12",
            ),
        ),
    ]
