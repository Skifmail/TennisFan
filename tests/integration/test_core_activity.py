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
from apps.core.activity import HOME_ACTIVITY_SEEN_COOKIE, log_activity
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

    def test_home_shows_public_activity_and_hides_private_events(self) -> None:
        """На главной видна публичная лента без оплат, отклонений и сумм."""
        player_user = User.objects.create_user(
            email="home-feed-player@test.local",
            password="testpass123",
            first_name="Никита",
            last_name="Лентов",
        )
        Player.objects.create(user=player_user)
        log_activity(
            event_type=PlatformActivityEvent.EventType.SPARRING_CREATED,
            actor=player_user,
            description="Создал заявку на спарринг (Казань)",
            target_url="/sparring/",
            dedupe_key=f"test:home-sparring-{uuid.uuid4()}",
        )
        log_activity(
            event_type=PlatformActivityEvent.EventType.PAYMENT_DONATION,
            actor=player_user,
            description="Секретный донат 777",
            amount=Decimal("777.00"),
            dedupe_key=f"test:home-payment-{uuid.uuid4()}",
        )
        log_activity(
            event_type=PlatformActivityEvent.EventType.MATCH_RESULT_REJECTED,
            actor=player_user,
            description="Отклонил результат матча скрытно",
            dedupe_key=f"test:home-reject-{uuid.uuid4()}",
        )

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "События платформы")
        self.assertNotContains(response, "Сейчас на платформе")
        self.assertNotContains(response, "Лента активности")
        self.assertContains(response, "Никита Лентов")
        self.assertContains(response, "Создал заявку на спарринг (Казань)")
        self.assertContains(response, "Зарегистрировался на платформе")
        self.assertNotContains(response, "Секретный донат 777")
        self.assertNotContains(response, "Отклонил результат матча скрытно")
        self.assertNotContains(response, "777 ₽")
        events = list(response.context["home_activity_events"])
        event_types = {event.event_type for event in events}
        self.assertIn(PlatformActivityEvent.EventType.REGISTRATION, event_types)
        self.assertIn(PlatformActivityEvent.EventType.SPARRING_CREATED, event_types)
        self.assertNotIn(PlatformActivityEvent.EventType.PAYMENT_DONATION, event_types)
        self.assertNotIn(
            PlatformActivityEvent.EventType.MATCH_RESULT_REJECTED, event_types
        )

    def test_home_activity_does_not_expose_admin_urls(self) -> None:
        """Публичная лента не ведёт в админку даже если в журнале admin-ссылка."""
        user = User.objects.create_user(
            email="home-feed-admin-url@test.local",
            password="testpass123",
            first_name="Ольга",
            last_name="Публичная",
        )
        Player.objects.create(user=user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ольга Публичная")
        self.assertNotContains(response, 'href="/admin/')

    def test_tournament_photo_creates_public_activity_event(self) -> None:
        """Загрузка фото в турнир попадает в журнал и на главную."""
        import io
        from datetime import date

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from apps.tournaments.models import Tournament, TournamentPhoto

        buffer = io.BytesIO()
        Image.new("RGB", (800, 600), color=(120, 180, 90)).save(
            buffer, format="JPEG", quality=85
        )
        buffer.seek(0)

        owner = User.objects.create_user(
            email="home-feed-photo@test.local",
            password="testpass123",
            first_name="Кирилл",
            last_name="Фотов",
        )
        player = Player.objects.create(user=owner)
        tournament = Tournament.objects.create(
            name="Кубок ленты",
            slug="activity-feed-photo-cup",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
        )
        TournamentPhoto.objects.create(
            tournament=tournament,
            image=SimpleUploadedFile(
                "feed-photo.jpg",
                buffer.read(),
                content_type="image/jpeg",
            ),
            uploaded_by=player,
        )

        event = PlatformActivityEvent.objects.filter(
            event_type=PlatformActivityEvent.EventType.PHOTO_ADDED,
            actor=owner,
        ).first()
        self.assertIsNotNone(event)
        self.assertIn("Кубок ленты", event.description)
        self.assertIn("фото", event.description.lower())

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Кирилл Фотов")
        self.assertContains(response, "Кубок ленты")

    def test_first_home_visit_does_not_mark_feed_as_new(self) -> None:
        """Первый заход только запоминает ленту и не орёт «всё новое»."""
        user = User.objects.create_user(
            email="home-feed-first-visit@test.local",
            password="testpass123",
            first_name="Первый",
            last_name="Визит",
        )
        Player.objects.create(user=user)

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["home_activity_new_count"], 0)
        self.assertNotContains(response, "home-activity__row--new")
        self.assertNotContains(response, 'class="home-activity-nudge"')
        self.assertIn(HOME_ACTIVITY_SEEN_COOKIE, response.cookies)
        latest_id = max(event.id for event in response.context["home_activity_events"])
        self.assertEqual(
            response.cookies[HOME_ACTIVITY_SEEN_COOKIE].value, str(latest_id)
        )

    def test_home_feed_script_requires_real_viewport_intersection(self) -> None:
        """Скрипт ленты помнит просмотр в localStorage и не снимает плашку на загрузке."""
        response = self.client.get(reverse("home"))
        self.assertContains(response, "localStorage")
        self.assertContains(response, "getBoundingClientRect")
        self.assertContains(response, "scrollIntoView")

    def test_returning_visitor_sees_new_home_activity(self) -> None:
        """После новых событий возвращающийся посетитель видит бейдж и плашку."""
        old_user = User.objects.create_user(
            email="home-feed-seen-old@test.local",
            password="testpass123",
            first_name="Старый",
            last_name="След",
        )
        Player.objects.create(user=old_user)
        seen_event = (
            PlatformActivityEvent.objects.filter(
                event_type__in=PlatformActivityEvent.PUBLIC_FEED_EVENT_TYPES
            )
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(seen_event)

        new_user = User.objects.create_user(
            email="home-feed-seen-new@test.local",
            password="testpass123",
            first_name="Свежий",
            last_name="Игрок",
        )
        Player.objects.create(user=new_user)

        self.client.cookies[HOME_ACTIVITY_SEEN_COOKIE] = str(seen_event.id)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.context["home_activity_new_count"], 1)
        self.assertContains(response, "home-activity__row--new")
        self.assertContains(response, "Новые события - посмотреть")
        self.assertContains(response, "Свежий Игрок")
        self.assertNotIn(HOME_ACTIVITY_SEEN_COOKIE, response.cookies)

    def test_staff_announcement_appears_on_home_with_brand_badge(self) -> None:
        """Админ публикует сообщение: на главной бейдж TennisFan, без личного имени."""
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("platform_activity_announce"),
            {"message": "Завтра вечером техническое обслуживание."},
        )
        self.assertEqual(response.status_code, 302)

        event = PlatformActivityEvent.objects.filter(
            event_type=PlatformActivityEvent.EventType.ADMIN_ANNOUNCEMENT
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.description, "Завтра вечером техническое обслуживание.")
        self.assertTrue(event.shows_brand_actor())

        self.client.logout()
        home = self.client.get(reverse("home"))
        self.assertContains(home, "Завтра вечером техническое обслуживание.")
        self.assertContains(home, "tf-brand-badge")
        self.assertContains(home, "Tennis")
        self.assertContains(home, "Fan")
        self.assertNotContains(home, "Админ Платформы")
        self.assertNotContains(home, "club-dashboard-activity__badge--announcement")

    def test_player_cannot_publish_announcement(self) -> None:
        """Обычный игрок не может писать в ленту от имени платформы."""
        player_user = User.objects.create_user(
            email="announce-player@test.local",
            password="testpass123",
            first_name="Игрок",
            last_name="Безправ",
        )
        Player.objects.create(user=player_user)
        self.client.force_login(player_user)
        response = self.client.post(
            reverse("platform_activity_announce"),
            {"message": "Попытка спама в ленту"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            PlatformActivityEvent.objects.filter(
                event_type=PlatformActivityEvent.EventType.ADMIN_ANNOUNCEMENT
            ).exists()
        )

    def test_empty_announcement_is_rejected(self) -> None:
        """Пустое сообщение не попадает в ленту."""
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("platform_activity_announce"),
            {"message": "   "},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            PlatformActivityEvent.objects.filter(
                event_type=PlatformActivityEvent.EventType.ADMIN_ANNOUNCEMENT
            ).exists()
        )

    def test_announcement_triggers_new_events_nudge(self) -> None:
        """Новое объявление админа поднимает плашку новых событий."""
        old_user = User.objects.create_user(
            email="announce-seen-old@test.local",
            password="testpass123",
            first_name="Старый",
            last_name="Визит",
        )
        Player.objects.create(user=old_user)
        seen_event = (
            PlatformActivityEvent.objects.filter(
                event_type__in=PlatformActivityEvent.PUBLIC_FEED_EVENT_TYPES
            )
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(seen_event)

        self.client.force_login(self.staff)
        self.client.post(
            reverse("platform_activity_announce"),
            {"message": "Открыта запись на новый турнир."},
        )
        self.client.logout()

        anon = Client()
        anon.cookies[HOME_ACTIVITY_SEEN_COOKIE] = str(seen_event.id)
        home = anon.get(reverse("home"))
        self.assertGreaterEqual(home.context["home_activity_new_count"], 1)
        self.assertContains(home, "Новые события - посмотреть")
        self.assertContains(home, "Открыта запись на новый турнир.")

    def test_home_shows_announce_form_only_to_staff(self) -> None:
        """Форма «Сообщение в ленту» на главной видна только админу."""
        anon_home = self.client.get(reverse("home"))
        self.assertEqual(anon_home.status_code, 200)
        self.assertNotContains(anon_home, "Сообщение в ленту событий")
        self.assertNotContains(anon_home, "platform/dashboard/activity/announce/")

        player_user = User.objects.create_user(
            email="home-announce-player@test.local",
            password="testpass123",
            first_name="Игрок",
            last_name="Ленты",
        )
        Player.objects.create(user=player_user)
        self.client.force_login(player_user)
        player_home = self.client.get(reverse("home"))
        self.assertNotContains(player_home, "Сообщение в ленту событий")
        self.assertNotContains(player_home, "platform/dashboard/activity/announce/")

        self.client.force_login(self.staff)
        staff_home = self.client.get(reverse("home"))
        self.assertContains(staff_home, "Сообщение в ленту событий")
        self.assertContains(staff_home, "platform/dashboard/activity/announce/")
        self.assertContains(staff_home, 'name="next"')
        self.assertContains(staff_home, 'value="home"')

    def test_staff_announcement_from_home_stays_on_home(self) -> None:
        """Публикация с главной возвращает админа к ленте на главной."""
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("platform_activity_announce"),
            {
                "message": "Сообщение с главной страницы.",
                "next": "home",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/?focus=home-activity")
        self.assertTrue(
            PlatformActivityEvent.objects.filter(
                event_type=PlatformActivityEvent.EventType.ADMIN_ANNOUNCEMENT,
                description="Сообщение с главной страницы.",
            ).exists()
        )

    def _create_announcement(
        self, text: str = "Официальное сообщение."
    ) -> PlatformActivityEvent:
        """Опубликовать объявление от лица staff и вернуть событие."""
        self.client.force_login(self.staff)
        self.client.post(
            reverse("platform_activity_announce"),
            {"message": text},
        )
        event = PlatformActivityEvent.objects.filter(
            event_type=PlatformActivityEvent.EventType.ADMIN_ANNOUNCEMENT,
            description=text,
        ).first()
        assert event is not None
        return event

    def test_staff_can_edit_announcement(self) -> None:
        """Админ меняет текст своего сообщения, дата события не сдвигается."""
        event = self._create_announcement("Старый текст объявления.")
        created_at = event.created_at

        response = self.client.post(
            reverse("platform_activity_announce_edit", args=[event.pk]),
            {"message": "Новый текст объявления.", "next": "home"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/?focus=home-activity")
        event.refresh_from_db()
        self.assertEqual(event.description, "Новый текст объявления.")
        self.assertEqual(event.created_at, created_at)

        home = self.client.get(reverse("home"))
        self.assertContains(home, "Новый текст объявления.")
        self.assertNotContains(home, "Старый текст объявления.")

    def test_staff_can_delete_announcement(self) -> None:
        """Админ удаляет ручное сообщение из ленты."""
        event = self._create_announcement("Сообщение к удалению.")
        event_id = event.pk

        response = self.client.post(
            reverse("platform_activity_announce_delete", args=[event.pk]),
            {"next": "home"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/?focus=home-activity")
        self.assertFalse(PlatformActivityEvent.objects.filter(pk=event_id).exists())

        home = self.client.get(reverse("home"))
        self.assertNotContains(home, "Сообщение к удалению.")

    def test_player_cannot_edit_or_delete_announcement(self) -> None:
        """Обычный игрок не может менять и удалять объявления платформы."""
        event = self._create_announcement("Защищённое объявление.")
        player_user = User.objects.create_user(
            email="announce-edit-player@test.local",
            password="testpass123",
            first_name="Игрок",
            last_name="Правки",
        )
        Player.objects.create(user=player_user)

        self.client.force_login(player_user)
        edit = self.client.post(
            reverse("platform_activity_announce_edit", args=[event.pk]),
            {"message": "Взлом ленты"},
        )
        delete = self.client.post(
            reverse("platform_activity_announce_delete", args=[event.pk]),
        )
        self.assertEqual(edit.status_code, 302)
        self.assertEqual(delete.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.description, "Защищённое объявление.")
        self.assertTrue(PlatformActivityEvent.objects.filter(pk=event.pk).exists())

        home = self.client.get(reverse("home"))
        self.assertContains(home, "Защищённое объявление.")
        self.assertNotContains(home, "activity-announce-manage")

    def test_staff_cannot_edit_non_announcement_event(self) -> None:
        """Системные события ленты нельзя править как объявление."""
        user = User.objects.create_user(
            email="announce-reg@test.local",
            password="testpass123",
            first_name="Системный",
            last_name="Игрок",
        )
        Player.objects.create(user=user)
        event = PlatformActivityEvent.objects.filter(
            event_type=PlatformActivityEvent.EventType.REGISTRATION,
            actor=user,
        ).first()
        self.assertIsNotNone(event)
        original = event.description

        self.client.force_login(self.staff)
        self.client.post(
            reverse("platform_activity_announce_edit", args=[event.pk]),
            {"message": "Подмена регистрации"},
        )
        self.client.post(
            reverse("platform_activity_announce_delete", args=[event.pk]),
        )
        event.refresh_from_db()
        self.assertEqual(event.description, original)
        self.assertTrue(PlatformActivityEvent.objects.filter(pk=event.pk).exists())

    def test_staff_home_shows_manage_controls_for_announcement(self) -> None:
        """На главной у объявления админ видит Изменить и Удалить."""
        event = self._create_announcement("Сообщение с кнопками управления.")
        home = self.client.get(reverse("home"))
        self.assertContains(home, "activity-announce-manage")
        self.assertContains(
            home, reverse("platform_activity_announce_edit", args=[event.pk])
        )
        self.assertContains(
            home, reverse("platform_activity_announce_delete", args=[event.pk])
        )
