"""Добавить исходного тренера и назначение заявки администратором."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("training", "0024_alter_training_options_remove_training_is_featured"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="trainingenrollment",
            name="requested_coach",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="requested_enrollments",
                to="training.coach",
                verbose_name="Исходный тренер",
            ),
        ),
        migrations.AddField(
            model_name="trainingenrollment",
            name="requested_coach_name",
            field=models.CharField(
                blank=True,
                max_length=100,
                verbose_name="Имя исходного тренера при записи",
            ),
        ),
        migrations.AddField(
            model_name="trainingenrollment",
            name="assigned_coach",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_enrollments",
                to="training.coach",
                verbose_name="Назначенный тренер",
            ),
        ),
        migrations.AddField(
            model_name="trainingenrollment",
            name="assigned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="distributed_training_enrollments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Распределил",
            ),
        ),
        migrations.AddField(
            model_name="trainingenrollment",
            name="assigned_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Дата распределения"
            ),
        ),
    ]
