"""Миграция сохраняет старые заявки у владельцев тренировок без повторных уведомлений."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.training.models import TrainingEnrollment
from apps.users.models import Notification


class TrainingEnrollmentDistributionMigrationTests(TransactionTestCase):
    """Проверка перехода старой схемы к ручному распределению заявок."""

    def test_existing_assignments_are_preserved_and_new_requests_are_pending(
        self,
    ) -> None:
        """Старые записи видны текущим тренерам, новые ожидают администратора."""
        executor = MigrationExecutor(connection)
        previous = [
            ("training", "0024_alter_training_options_remove_training_is_featured")
        ]
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(previous)
            apps = executor.loader.project_state(previous).apps
            coach_model = apps.get_model("training", "Coach")
            training_model = apps.get_model("training", "Training")
            enrollment_model = apps.get_model("training", "TrainingEnrollment")
            coach = coach_model.objects.create(
                name="Старый тренер", slug="migration-coach", city="Москва"
            )
            training = training_model.objects.create(
                title="Старая тренировка",
                slug="migration-training",
                description="Описание",
                city="Москва",
                coach=coach,
            )
            old = enrollment_model.objects.create(
                training=training, full_name="Старый игрок"
            )
            unassigned_training = training_model.objects.create(
                title="Без тренера",
                slug="migration-no-coach",
                description="Описание",
                city="Москва",
            )
            without_coach = enrollment_model.objects.create(
                training=unassigned_training, full_name="Без тренера"
            )
            notes_before = Notification.objects.count()
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            preserved = TrainingEnrollment.objects.get(pk=old.pk)
            self.assertEqual(preserved.requested_coach_id, coach.pk)
            self.assertEqual(preserved.requested_coach_name, "Старый тренер")
            self.assertEqual(preserved.assigned_coach_id, coach.pk)
            self.assertEqual(preserved.assigned_at, old.created_at)
            self.assertIsNone(preserved.assigned_by_id)
            self.assertIsNone(
                TrainingEnrollment.objects.get(pk=without_coach.pk).assigned_at
            )
            new = TrainingEnrollment.objects.create(
                training_id=training.pk, full_name="Новый игрок"
            )
            self.assertEqual(new.requested_coach_id, coach.pk)
            self.assertIsNone(new.assigned_at)
            self.assertIsNone(new.assigned_coach_id)
            self.assertEqual(Notification.objects.count(), notes_before)
        finally:
            MigrationExecutor(connection).migrate(latest)
