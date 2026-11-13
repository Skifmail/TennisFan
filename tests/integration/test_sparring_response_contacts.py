"""Отклик на спарринг: контакты игрока в письме и на странице «Мои заявки»."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.chat.models import ChatRoom
from apps.core.email_service import send_sparring_response_email
from apps.notifications.models import EventPolicy, NotificationPreference
from apps.sparring.models import SparringRequest, SparringResponse
from apps.sparring.services import respond_to_sparring
from apps.users.models import Notification, Player, User
from tests.support.factories import make_player, make_subscription, make_user


@override_settings(
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    NOTIFICATION_CENTER_ENABLED=False,
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
)
class SparringResponseContactsTestCase(TestCase):
    """Автор заявки получает контакты откликнувшегося игрока и может сразу написать."""

    def setUp(self) -> None:
        """Создать автора заявки, откликнувшегося игрока и саму заявку."""
        self.author = make_player(
            email_suffix="resp-author",
            first_name="Иван",
            last_name="Автор",
            phone="+79990000001",
        )
        self.respondent = Player.objects.create(
            user=make_user(
                email="p_resp-player@test.local",
                first_name="Леонид",
                last_name="Ермолаев",
                phone="+79990000002",
            ),
            total_points=1000.0,
            hidden_rating=1000.0,
            telegram="leonid",
            whatsapp="+7 (999) 000-00-03",
            max_contact="https://max.ru/u/leonid",
            birth_date=date(1994, 1, 1),
            skill_level="amateur",
            matches_played=12,
            matches_won=7,
        )
        self.author.is_verified = True
        Player.objects.filter(pk=self.author.pk).update(is_verified=True)
        make_subscription(
            self.respondent.user,
            tier_name="sparring-response-silver",
            tier_kwargs={"has_sparring": True},
        )
        self.sparring = SparringRequest.objects.create(
            player=self.author,
            city="Москва",
            description="Ищу партнёра на вечер",
            preferred_days="Понедельник",
            preferred_time="19:00",
            preferred_location="Корт Орион",
            desired_partner_age_min=20,
            desired_partner_age_max=40,
        )

    def _respond(self) -> SparringResponse:
        """Создать отклик при отключённом Telegram-боте."""
        with self.captureOnCommitCallbacks(execute=True):
            with patch(
                "apps.telegram_bot.notifications.bot.is_configured", return_value=False
            ):
                response, _ = respond_to_sparring(
                    self.sparring.pk, self.respondent, contact_method="telegram"
                )
        return response

    def test_email_contains_contacts_when_bot_is_disabled(self) -> None:
        """Письмо автору уходит с контактами даже без настроенного бота."""
        self._respond()

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        assert isinstance(message, EmailMultiAlternatives)
        self.assertEqual(message.to, [self.author.user.email])
        self.assertIn("Леонид Ермолаев", message.subject)
        html = message.alternatives[0][0]
        assert isinstance(html, str)
        self.assertIn("Леонид Ермолаев", html)
        self.assertIn("https://t.me/leonid", html)
        self.assertIn("https://wa.me/79990000003", html)
        self.assertIn("https://max.ru/u/leonid", html)
        self.assertIn("+79990000002", html)
        self.assertIn("Любитель", html)
        expected_age = timezone.now().year - 1994
        self.assertIn(f"<span>Возраст:</span> {expected_age} ", html)
        self.assertIn("Понедельник", html)
        self.assertIn("19:00", html)
        self.assertIn("20–40 лет", html)
        self.assertIn("Корт Орион", html)
        self.assertIn(reverse("sparring_my_requests"), html)
        self.assertEqual(Notification.objects.filter(user=self.author.user).count(), 1)

    def test_plain_email_contains_contact_urls_without_css(self) -> None:
        """Текстовая версия письма содержит ссылки, а не CSS из HTML-шаблона."""
        self._respond()

        body = mail.outbox[0].body
        self.assertIn("https://t.me/leonid", body)
        self.assertIn("https://wa.me/79990000003", body)
        self.assertIn("+79990000002", body)
        self.assertIn("Ищу партнёра на вечер", body)
        self.assertIn(f"Возраст: {timezone.now().year - 1994} ", body)
        self.assertNotIn("font-family", body)

    def test_email_without_contacts_points_to_platform_chat(self) -> None:
        """Обе версии письма без контактов подсказывают актуальную кнопку чата."""
        Player.objects.filter(pk=self.respondent.pk).update(
            telegram="", whatsapp="", max_contact=""
        )
        self.respondent.refresh_from_db()
        self.respondent.user.phone = ""
        self.respondent.user.save(update_fields=["phone"])
        self._respond()

        message = mail.outbox[0]
        assert isinstance(message, EmailMultiAlternatives)
        html = message.alternatives[0][0]
        assert isinstance(html, str)
        for channel, content in (("text", message.body), ("html", html)):
            with self.subTest(channel=channel):
                self.assertIn("Игрок не указал внешние контакты", content)
                self.assertIn("«Написать в чат»", content)
                self.assertNotIn("«Написать»", content)

    def test_email_waits_for_successful_commit(self) -> None:
        """Письмо не отправляется, пока создание отклика не зафиксировано."""
        with self.captureOnCommitCallbacks(execute=True):
            with patch(
                "apps.telegram_bot.notifications.bot.is_configured", return_value=False
            ):
                respond_to_sparring(
                    self.sparring.pk, self.respondent, contact_method="telegram"
                )
            self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_rollback_does_not_send_email(self) -> None:
        """Откат действия отменяет и запланированное письмо автору."""
        with self.captureOnCommitCallbacks(execute=True):
            with patch(
                "apps.telegram_bot.notifications.bot.is_configured", return_value=False
            ):
                with self.assertRaisesMessage(RuntimeError, "Откат отклика"):
                    with transaction.atomic():
                        respond_to_sparring(
                            self.sparring.pk, self.respondent, contact_method="telegram"
                        )
                        raise RuntimeError("Откат отклика")

        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(SparringResponse.objects.exists())
        self.assertFalse(Notification.objects.filter(user=self.author.user).exists())

    def test_author_deactivated_before_commit_does_not_receive_email(self) -> None:
        """Перед отправкой проверяется актуальный статус аккаунта, а не старый объект."""
        with self.captureOnCommitCallbacks(execute=True):
            with patch(
                "apps.telegram_bot.notifications.bot.is_configured", return_value=False
            ):
                respond_to_sparring(
                    self.sparring.pk, self.respondent, contact_method="telegram"
                )
            User.objects.filter(pk=self.author.user_id).update(is_active=False)

        self.assertEqual(len(mail.outbox), 0)

    def test_email_failure_does_not_rollback_response(self) -> None:
        """Сбой почты не отменяет сохранённый отклик и уведомление в личном кабинете."""
        with patch(
            "apps.core.email_service.send_sparring_response_email",
            side_effect=RuntimeError("Почта недоступна"),
        ):
            self._respond()

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(SparringResponse.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.author.user).count(), 1)

    @override_settings(USER_NOTIFICATIONS_EMAIL_ENABLED=False)
    def test_disabled_email_channel_does_not_send(self) -> None:
        """Общий выключатель почты соблюдается с центром уведомлений и без него."""
        response = SparringResponse.objects.create(
            sparring_request=self.sparring, respondent=self.respondent
        )
        for center_enabled in (False, True):
            with self.subTest(center_enabled=center_enabled):
                with override_settings(NOTIFICATION_CENTER_ENABLED=center_enabled):
                    sent = send_sparring_response_email(
                        self.author.user, request=self.sparring, response=response
                    )
                self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(NOTIFICATION_CENTER_ENABLED=True)
    def test_email_opt_out_preserves_inbox_notification(self) -> None:
        """Отказ от писем не отменяет единственную запись о новом отклике в ЛК."""
        NotificationPreference.objects.create(
            user=self.author.user, email_enabled=False
        )
        self._respond()

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.filter(user=self.author.user).count(), 1)

    @override_settings(NOTIFICATION_CENTER_ENABLED=True)
    def test_event_policy_can_disable_response_email(self) -> None:
        """Политика события отключает почту, но не запись отклика в ЛК."""
        EventPolicy.objects.filter(key="sparring.response").update(email_enabled=False)
        self._respond()

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.filter(user=self.author.user).count(), 1)

    def test_repeated_response_does_not_duplicate_email(self) -> None:
        """Изменение способа связи не создаёт второе письмо о том же отклике."""
        self._respond()
        self._respond()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Notification.objects.filter(user=self.author.user).count(), 1)

    @override_settings(NOTIFICATION_CENTER_ENABLED=True)
    def test_email_does_not_duplicate_notification_center_event(self) -> None:
        """Отдельное письмо не создаёт второе событие для ЛК и push."""
        self._respond()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Notification.objects.filter(user=self.author.user).count(), 1)

    def test_player_and_request_text_are_escaped_in_html_notifications(self) -> None:
        """Текст пользователя остаётся текстом в HTML-письме и сообщении Telegram."""
        self.respondent.user.first_name = "<b>Леонид</b>"
        self.respondent.user.last_name = "Ермолаев & партнёр"
        self.respondent.user.save(update_fields=["first_name", "last_name"])
        self.sparring.description = '<img src="x" onerror="alert(1)">'
        self.sparring.save(update_fields=["description"])

        with self.captureOnCommitCallbacks(execute=True):
            with (
                patch(
                    "apps.telegram_bot.notifications.bot.is_configured",
                    return_value=True,
                ),
                patch(
                    "apps.telegram_bot.notifications.send_to_user_by_user",
                    return_value=True,
                ) as send,
            ):
                respond_to_sparring(
                    self.sparring.pk, self.respondent, contact_method="telegram"
                )

        message = mail.outbox[0]
        assert isinstance(message, EmailMultiAlternatives)
        html = message.alternatives[0][0]
        assert isinstance(html, str)
        telegram_text = send.call_args.args[1]
        for channel, content in (("email", html), ("telegram", telegram_text)):
            with self.subTest(channel=channel):
                self.assertIn("&lt;b&gt;Леонид&lt;/b&gt;", content)
                self.assertIn("Ермолаев &amp; партнёр", content)
                self.assertNotIn("<b>Леонид</b>", content)
        self.assertNotIn(self.sparring.description, html)
        self.assertIn("&lt;img", html)

    def test_telegram_contains_contacts_without_generic_email_duplicate(self) -> None:
        """Бот получает ссылки и телефон, а HTML-письмо уходит только один раз."""
        with self.captureOnCommitCallbacks(execute=True):
            with (
                patch(
                    "apps.telegram_bot.notifications.bot.is_configured",
                    return_value=True,
                ),
                patch(
                    "apps.telegram_bot.notifications.send_to_user_by_user",
                    return_value=True,
                ) as send,
            ):
                respond_to_sparring(
                    self.sparring.pk, self.respondent, contact_method="telegram"
                )

        send.assert_called_once()
        text = send.call_args.args[1]
        self.assertIn("https://t.me/leonid", text)
        self.assertIn("https://wa.me/79990000003", text)
        self.assertIn("+79990000002", text)
        self.assertTrue(send.call_args.kwargs["skip_email"])
        self.assertEqual(len(mail.outbox), 1)

    def test_my_requests_page_shows_contact_links_and_write_button(self) -> None:
        """Карточка разделяет внешние контакты, звонок и кнопку «Написать в чат»."""
        self._respond()

        self.client.force_login(self.author.user)
        page = self.client.get(reverse("sparring_my_requests"), secure=True)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "https://t.me/leonid")
        self.assertContains(page, "tel:+79990000002")
        self.assertContains(page, "Написать в чат")
        self.assertContains(
            page,
            f'action="{reverse("chat:direct_start", args=[self.respondent.pk])}"',
        )
        self.assertContains(page, 'class="sparring-contact__value"', count=1)
        self.assertNotContains(page, "@leonid")
        self.assertNotContains(page, "Написать в Telegram")
        self.assertContains(page, "Ожидает вашего подтверждения")
        self.assertNotContains(page, 'id="sparring-contact-modal"')

    def test_card_with_messengers_starts_platform_chat_with_the_respondent(
        self,
    ) -> None:
        """Наличие Telegram не заменяет нижнюю кнопку личного чата на платформе."""
        self._respond()
        self.client.force_login(self.author.user)
        direct_url = reverse("chat:direct_start", args=[self.respondent.pk])
        page = self.client.get(reverse("sparring_my_requests"), secure=True)
        self.assertContains(page, f'action="{direct_url}"')
        self.assertContains(page, "Написать в чат")

        started = self.client.post(direct_url, secure=True)
        room = ChatRoom.objects.get()

        self.assertRedirects(
            started,
            reverse("chat:room", args=[room.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            {room.direct_user_low_id, room.direct_user_high_id},
            {self.author.user_id, self.respondent.user_id},
        )
        self.client.post(direct_url, secure=True)
        self.assertEqual(ChatRoom.objects.count(), 1)

    def test_card_without_messengers_opens_direct_chat(self) -> None:
        """При одном телефоне или пустых контактах кнопка открывает чат с нужным игроком."""
        self.respondent.telegram = ""
        self.respondent.whatsapp = ""
        self.respondent.max_contact = ""
        Player.objects.filter(pk=self.respondent.pk).update(
            telegram="", whatsapp="", max_contact=""
        )
        self._respond()
        self.client.force_login(self.author.user)
        direct_url = reverse("chat:direct_start", args=[self.respondent.pk])

        for phone in ("+79990000002", ""):
            with self.subTest(phone=phone):
                self.respondent.user.phone = phone
                self.respondent.user.save(update_fields=["phone"])
                page = self.client.get(reverse("sparring_my_requests"), secure=True)

                self.assertContains(page, f'action="{direct_url}"')
                self.assertNotContains(page, "Написать в Телефон")

        started = self.client.post(direct_url, secure=True)
        room = ChatRoom.objects.get()
        self.assertRedirects(
            started,
            reverse("chat:room", args=[room.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            {room.direct_user_low_id, room.direct_user_high_id},
            {self.author.user_id, self.respondent.user_id},
        )

    def test_contacts_are_not_shown_in_another_players_requests(self) -> None:
        """Контакты откликнувшегося игрока доступны только владельцу заявки."""
        self._respond()
        other = make_player(email_suffix="resp-other")
        other.is_verified = True
        Player.objects.filter(pk=other.pk).update(is_verified=True)
        self.client.force_login(other.user)

        page = self.client.get(reverse("sparring_my_requests"), secure=True)

        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "https://t.me/leonid")
        self.assertNotContains(page, "+79990000002")

    def test_contact_links_and_primary_contact(self) -> None:
        """Контакты имеют стабильный порядок и сохраняют выбранный внешний способ связи."""
        self._respond()
        response = SparringResponse.objects.get(sparring_request=self.sparring)

        links = response.contact_links()
        self.assertEqual(
            [link["key"] for link in links], ["telegram", "whatsapp", "max", "phone"]
        )
        primary = response.primary_contact()
        assert primary is not None
        self.assertEqual(primary["key"], "telegram")

        response.contact_method = "whatsapp"
        response.save(update_fields=["contact_method"])
        chosen = response.primary_contact()
        assert chosen is not None
        self.assertEqual(chosen["key"], "whatsapp")
