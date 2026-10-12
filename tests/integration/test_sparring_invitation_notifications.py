"""Почта и уведомление в ЛК по приглашению на спарринг."""

from datetime import date
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.sparring.models import SparringInvitation
from apps.sparring.services import create_match_from_invitation
from apps.telegram_bot.notifications import (
    notify_sparring_invitation_accepted_inviter,
    notify_sparring_invitation_created,
)
from apps.users.models import Notification
from tests.support.factories import make_player, make_subscription


@override_settings(USER_NOTIFICATIONS_EMAIL_ENABLED=True)
class SparringInvitationNotificationTests(TestCase):
    """Приглашение и его принятие доходят на почту даже без Telegram-бота."""

    def setUp(self) -> None:
        self.inviter = make_player(
            email_suffix="inviter-mail",
            first_name="Иван",
            last_name="Зовёт",
        )
        self.invitee = make_player(
            email_suffix="invitee-mail",
            first_name="Пётр",
            last_name="Ждёт",
        )
        self.invitation = SparringInvitation.objects.create(
            inviter=self.inviter,
            invitee=self.invitee,
            proposed_date=date(2026, 10, 20),
        )

    def test_invitee_gets_lk_and_email_without_telegram_bot(self) -> None:
        """Приглашённому создаётся уведомление в ЛК и письмо со ссылкой."""
        with patch(
            "apps.telegram_bot.notifications.bot.is_configured",
            return_value=False,
        ):
            notify_sparring_invitation_created(self.invitation)

        notice = Notification.objects.get(user=self.invitee.user)
        self.assertIn("пригласил вас на спарринг", notice.message)
        self.assertEqual(notice.url, reverse("sparring_my_invitations"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.invitee.user.email])
        self.assertIn("Приглашение на спарринг", message.subject)
        self.assertIn("20.10.2026", message.body)
        self.assertIn(reverse("sparring_my_invitations"), message.body)

    def test_inviter_gets_lk_and_email_when_invitation_accepted(self) -> None:
        """После принятия пригласивший получает письмо и уведомление со ссылкой на матч."""
        match = create_match_from_invitation(self.invitation)
        mail.outbox.clear()
        with patch(
            "apps.telegram_bot.notifications.bot.is_configured",
            return_value=False,
        ):
            notify_sparring_invitation_accepted_inviter(self.invitation, match)

        notice = Notification.objects.filter(user=self.inviter.user).latest("pk")
        self.assertIn("принял", notice.message)
        self.assertEqual(notice.url, reverse("match_detail", args=[match.pk]))
        acceptance = [
            item
            for item in mail.outbox
            if item.to == [self.inviter.user.email]
            and "Приглашение принято" in item.subject
        ]
        self.assertEqual(len(acceptance), 1)
        self.assertIn(reverse("match_detail", args=[match.pk]), acceptance[0].body)

    def test_sent_invitation_stays_pending_for_inviter(self) -> None:
        """Исходящее приглашение видно пригласившему, пока его не приняли."""
        self.inviter.user.phone = "+79990001122"
        self.inviter.user.save(update_fields=["phone"])
        self.inviter.birth_date = date(1990, 1, 1)
        self.inviter.is_verified = True
        self.inviter.save(update_fields=["birth_date", "is_verified"])
        make_subscription(
            self.inviter.user,
            tier_name="invite-mail-silver",
            tier_kwargs={"has_sparring": True},
        )
        self.client.force_login(self.inviter.user)

        response = self.client.get(reverse("sparring_my_invitations"), secure=True)

        self.assertContains(response, "Пётр Ждёт")
        self.assertContains(response, "Ожидает ответа")
