"""Тесты очереди целей Яндекс.Метрики через сессию."""

from django.test import RequestFactory, TestCase

from apps.core.metrika import (
    SESSION_KEY,
    TOURNAMENT_REGISTRATION_SUCCESS,
    pop_metrika_goals,
    queue_metrika_goal,
)


class MetrikaQueueTestCase(TestCase):
    """Постановка цели в сессию и одноразовая выдача в шаблон."""

    def setUp(self) -> None:
        self.factory = RequestFactory()

    def _request(self):
        request = self.factory.get("/")
        from django.contrib.sessions.backends.db import SessionStore

        request.session = SessionStore()
        return request

    def test_queue_and_pop_once(self) -> None:
        request = self._request()
        queue_metrika_goal(
            request,
            TOURNAMENT_REGISTRATION_SUCCESS,
            {"tournament_id": 7, "variant": "singles"},
        )

        goals = pop_metrika_goals(request)
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["goal"], TOURNAMENT_REGISTRATION_SUCCESS)
        self.assertEqual(goals[0]["params"]["tournament_id"], 7)
        self.assertEqual(pop_metrika_goals(request), [])
        self.assertNotIn(SESSION_KEY, request.session)

    def test_skips_empty_goal(self) -> None:
        request = self._request()
        queue_metrika_goal(request, "")
        self.assertEqual(pop_metrika_goals(request), [])
