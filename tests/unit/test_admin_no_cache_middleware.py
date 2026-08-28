"""Тесты запрета кэширования страниц админки."""

from __future__ import annotations

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from apps.core.middleware import AdminNoCacheMiddleware


class AdminNoCacheMiddlewareTestCase(TestCase):
    """AdminNoCacheMiddleware выставляет no-store только для /admin/."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.middleware = AdminNoCacheMiddleware(lambda request: HttpResponse("ok"))

    @override_settings(ADMIN_URL="admin")
    def test_admin_path_gets_no_cache_headers(self) -> None:
        request = self.factory.get("/admin/courts/court/add/")
        response = self.middleware(request)

        self.assertEqual(
            response["Cache-Control"], "no-store, no-cache, must-revalidate, max-age=0"
        )
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(response["Expires"], "0")

    @override_settings(ADMIN_URL="admin")
    def test_public_path_is_unchanged(self) -> None:
        request = self.factory.get("/courts/")
        response = self.middleware(request)

        self.assertNotIn("Cache-Control", response)
