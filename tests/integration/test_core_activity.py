"""Интеграционные тесты: лента активности платформы."""

import uuid
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.clubs.models import (
    Club,
    ClubJoinRequest,
)
from apps.core.activity import log_activity
from apps.core.models import PlatformActivityEvent
from apps.payments.models import PaymentRecord
from apps.users.models import Player, User
from tests.support.factories import make_subscription


@override_settings(SECURE_SSL_REDIRECT=False)
class PlatformActivityFeedTestCase(TestCase):
    """Тесты ленты активности платформы и её сигналов."""

    def setUp(self) -> None:
        self.client = Client()
        self.staff = User.objects.create_user(
            email="activity-staff@test.local",
            password="testpass123",
            first_name="Админ",
            last_name="Платформы",
            is_staff=True,
        )

    def test_registration_event_created_on_player_create(self) -> None:
        """При создании профиля игрока появляется событие регистрации."""
        user = User.objects.create_user(
            email="activity-newbie@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Петров",
        )
        Player.objects.create(user=user)
        event = PlatformActivityEvent.objects.filter(
            event_type=PlatformActivityEvent.EventType.REGISTRATION,
            actor=user,
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor_name, "Иван Петров")

    def test_payment_event_created_on_succeeded_payment(self) -> None:
        """Успешный платёж порождает событие-оплату с суммой."""
        user = User.objects.create_user(
            email="activity-payer@test.local",
            password="testpass123",
            first_name="Пётр",
            last_name="Сидоров",
        )
        payment = PaymentRecord.objects.create(
            user=user,
            payment_type=PaymentRecord.PaymentType.DONATION,
            amount="500.00",
            currency="RUB",
            status="succeeded",
            item_label="Донат проекту",
        )
        event = PlatformActivityEvent.objects.filter(
            dedupe_key=f"payment:{payment.pk}"
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(
            event.event_type, PlatformActivityEvent.EventType.PAYMENT_DONATION
        )
        self.assertEqual(event.get_amount_display(), "500 ₽")

    def test_pending_payment_does_not_create_event(self) -> None:
        """Платёж в статусе pending не попадает в ленту."""
        user = User.objects.create_user(
            email="activity-pending@test.local",
            password="testpass123",
        )
        payment = PaymentRecord.objects.create(
            user=user,
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            amount="990.00",
            status="pending",
        )
        self.assertFalse(
            PlatformActivityEvent.objects.filter(
                dedupe_key=f"payment:{payment.pk}"
            ).exists()
        )

    def test_log_activity_is_idempotent_by_dedupe_key(self) -> None:
        """Повторный вызов с тем же dedupe_key не создаёт дубликат."""
        log_activity(
            event_type=PlatformActivityEvent.EventType.REGISTRATION,
            actor=self.staff,
            description="Тестовое событие",
            dedupe_key="test:unique-1",
        )
        log_activity(
            event_type=PlatformActivityEvent.EventType.REGISTRATION,
            actor=self.staff,
            description="Тестовое событие (повтор)",
            dedupe_key="test:unique-1",
        )
        self.assertEqual(
            PlatformActivityEvent.objects.filter(dedupe_key="test:unique-1").count(),
            1,
        )

    def test_dashboard_shows_activity_and_name_search(self) -> None:
        """Дашборд отображает ленту и фильтрует по имени за всё время."""
        target = User.objects.create_user(
            email="activity-target@test.local",
            password="testpass123",
            first_name="Светлана",
            last_name="Иванова",
        )
        Player.objects.create(user=target)
        self.client.force_login(self.staff)

        url = reverse("platform_dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Лента действий")

        response = self.client.get(url, {"act_q": "Светлана"})
        self.assertEqual(response.status_code, 200)
        page = response.context["activity_page"]
        names = {event.actor_name for event in page.object_list}
        self.assertIn("Светлана Иванова", names)

        for query in ("светлана", "ИВАНОВА", "иванова", "иванов"):
            response = self.client.get(url, {"act_q": query})
            self.assertEqual(response.status_code, 200)
            names = {
                event.actor_name
                for event in response.context["activity_page"].object_list
            }
            self.assertIn(
                "Светлана Иванова",
                names,
                msg=f"Поиск «{query}» должен находить пользователя без учёта регистра",
            )

        response = self.client.get(
            url, {"act_type": PlatformActivityEvent.EventType.PAYMENT_DONATION}
        )
        self.assertEqual(response.status_code, 200)
        for event in response.context["activity_page"].object_list:
            self.assertEqual(
                event.event_type, PlatformActivityEvent.EventType.PAYMENT_DONATION
            )

    def test_registration_event_stores_role_snapshot(self) -> None:
        """Событие хранит снимок роли пользователя (игрок/админ)."""
        admin_user = User.objects.create_user(
            email="activity-role-admin@test.local",
            password="testpass123",
            first_name="Роман",
            last_name="Админов",
            is_staff=True,
        )
        Player.objects.create(user=admin_user)
        event = PlatformActivityEvent.objects.filter(
            event_type=PlatformActivityEvent.EventType.REGISTRATION,
            actor=admin_user,
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor_role, "Админ")

        player_user = User.objects.create_user(
            email="activity-role-player@test.local",
            password="testpass123",
            first_name="Олег",
            last_name="Игроков",
        )
        Player.objects.create(user=player_user)
        event = PlatformActivityEvent.objects.filter(
            event_type=PlatformActivityEvent.EventType.REGISTRATION,
            actor=player_user,
        ).first()
        self.assertEqual(event.actor_role, "Игрок")

    def test_club_join_request_creates_event(self) -> None:
        """Заявка в клуб порождает событие в ленте."""
        club = Club.objects.create(
            name="Теннис-Клуб",
            slug="tennis-club",
            city="Москва",
            address="ул. Кортовая, 1",
            email="tennis-club@test.local",
            admin_name="Админ клуба",
        )
        applicant = User.objects.create_user(
            email="activity-applicant@test.local",
            password="testpass123",
            first_name="Мария",
            last_name="Клубова",
        )
        Player.objects.create(user=applicant)
        join = ClubJoinRequest.objects.create(club=club, user=applicant)
        event = PlatformActivityEvent.objects.filter(
            dedupe_key=f"club_join_req:{join.pk}"
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(
            event.event_type, PlatformActivityEvent.EventType.CLUB_JOIN_REQUESTED
        )
        self.assertIn("Теннис-Клуб", event.description)

    def test_activity_csv_export(self) -> None:
        """CSV-экспорт ленты доступен staff и содержит заголовки."""
        user = User.objects.create_user(
            email="activity-csv@test.local",
            password="testpass123",
            first_name="Эдуард",
            last_name="Экспортов",
        )
        Player.objects.create(user=user)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("platform_activity_export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        content = response.content.decode("utf-8")
        self.assertIn("Статус", content)
        self.assertIn("Эдуард Экспортов", content)

    def test_platform_dashboard_shows_expiring_player_subscription(self) -> None:
        """Дашборд показывает игрока с истекающей подпиской и корректный текст."""
        player_user = User.objects.create_user(
            email="expiring-sub@test.local",
            password="testpass123",
            first_name="Александр",
            last_name="Шевченко",
        )
        Player.objects.create(user=player_user)
        make_subscription(player_user, duration_days=3)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("platform_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Александр Шевченко")
        self.assertContains(response, "Подписка игрока Александр Шевченко закончится")
        self.assertNotContains(response, "пользовательских подписок закончатся")

    def test_platform_activity_unseen_indicator(self) -> None:
        """Индикатор новых событий показывается до просмотра панели и скрывается после."""
        cache.clear()
        log_activity(
            event_type=PlatformActivityEvent.EventType.REGISTRATION,
            actor=self.staff,
            description="Событие для индикатора",
            dedupe_key=f"test:indicator-{uuid.uuid4()}",
        )
        self.client.force_login(self.staff)

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["platform_activity_unseen"])
        self.assertContains(response, "notify-dot")

        response = self.client.get(reverse("platform_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["platform_activity_unseen"])

        log_activity(
            event_type=PlatformActivityEvent.EventType.PAYMENT_DONATION,
            actor=self.staff,
            description="Новое событие после просмотра",
            dedupe_key=f"test:indicator-{uuid.uuid4()}",
            amount=Decimal("100.00"),
        )
        response = self.client.get(reverse("home"))
        self.assertTrue(response.context["platform_activity_unseen"])
