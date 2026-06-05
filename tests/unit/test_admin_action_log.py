"""Тесты страницы полного журнала действий админки."""

from __future__ import annotations

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AdminActionLog
from apps.users.models import Player, User


class AdminActionLogAdminTestCase(TestCase):
    """Доступ и поиск в журнале действий."""

    def setUp(self) -> None:
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email="admin-log@test.local",
            password="testpass123",
            first_name="Леонид",
            last_name="Админ",
        )
        self.player = Player.objects.create(
            user=User.objects.create_user(
                email="parshin@test.local",
                password="testpass123",
                first_name="Алексей",
                last_name="Паршин",
            )
        )
        content_type = ContentType.objects.get_for_model(Player)
        LogEntry.objects.create(
            user_id=self.admin_user.pk,
            content_type_id=content_type.pk,
            object_id=str(self.player.pk),
            object_repr="Алексей Паршин",
            action_flag=ADDITION,
            change_message="",
        )
        self.client.force_login(self.admin_user)

    def test_changelist_is_accessible(self) -> None:
        response = self.client.get(
            reverse("admin:core_adminactionlog_changelist"),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Журнал действий")

    def test_search_by_fio_filters_entries(self) -> None:
        response = self.client.get(
            reverse("admin:core_adminactionlog_changelist"),
            {"log_search_mode": "fio", "log_query": "Паршин"},
            secure=True,
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Алексей Паршин")

    def test_search_by_date_filters_entries(self) -> None:
        today = timezone.localdate().isoformat()
        response = self.client.get(
            reverse("admin:core_adminactionlog_changelist"),
            {"log_search_mode": "date", "log_search_date": today},
            secure=True,
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Алексей Паршин")

    def test_search_by_action_filters_entries(self) -> None:
        response = self.client.get(
            reverse("admin:core_adminactionlog_changelist"),
            {"log_search_mode": "action", "log_action_flag": str(ADDITION)},
            secure=True,
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Добавлено")

    def test_proxy_model_uses_logentry_table(self) -> None:
        self.assertEqual(AdminActionLog.objects.count(), LogEntry.objects.count())
