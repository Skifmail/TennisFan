"""Тесты очереди целей и настройки счётчика Яндекс.Метрики."""

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings

from apps.core.context_processors import metrika_goals
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

    @override_settings(YANDEX_METRIKA_ID=987654)
    def test_context_processor_exposes_counter_id(self) -> None:
        request = self._request()
        queue_metrika_goal(request, TOURNAMENT_REGISTRATION_SUCCESS, {"id": 1})

        context = metrika_goals(request)

        self.assertEqual(context["yandex_metrika_id"], 987654)
        self.assertEqual(
            context["metrika_goals"][0]["goal"],
            TOURNAMENT_REGISTRATION_SUCCESS,
        )

    def test_base_template_skips_counter_when_disabled(self) -> None:
        html = render_to_string("base.html", {"yandex_metrika_id": 0})

        self.assertNotIn("mc.yandex.ru", html)

    @override_settings(YANDEX_METRIKA_ID=987654)
    def test_base_template_loads_counter_only_after_consent(self) -> None:
        html = render_to_string("base.html", {"yandex_metrika_id": 987654})

        self.assertIn("counterId = 987654", html)
        self.assertIn("cookie_consent=true", html)
        self.assertIn("cookie_consent_accepted", html)
        # <noscript>-пиксель удалён: он не может ждать согласия.
        self.assertNotIn("mc.yandex.ru/watch/", html)
