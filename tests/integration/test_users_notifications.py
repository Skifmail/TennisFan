"""Интеграционные тесты: кэш непрочитанных уведомлений."""

from django.core.cache import cache
from django.test import TestCase

from apps.users.context_processors import UNREAD_NOTIFICATIONS_CACHE_KEY_PREFIX
from apps.users.models import Notification
from tests.support.factories import make_user


class NotificationCacheInvalidationTestCase(TestCase):
    """Сигнал сбрасывает кэш счётчика при изменении уведомлений."""

    def setUp(self) -> None:
        self.user = make_user(email="notif-cache@test.local")
        self.cache_key = f"{UNREAD_NOTIFICATIONS_CACHE_KEY_PREFIX}:{self.user.pk}"

    def test_create_notification_clears_cache(self) -> None:
        cache.set(self.cache_key, 5, timeout=30)

        Notification.objects.create(user=self.user, message="Новое уведомление")

        self.assertIsNone(cache.get(self.cache_key))

    def test_delete_notification_clears_cache(self) -> None:
        notif = Notification.objects.create(
            user=self.user,
            message="Удаляемое",
        )
        cache.set(self.cache_key, 3, timeout=30)

        notif.delete()

        self.assertIsNone(cache.get(self.cache_key))
