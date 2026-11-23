"""Распределение заявок: права, уведомления, повторные нажатия и доступ тренеров."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.email_service import send_training_enrollment_email
from apps.notifications.domain.rules import INBOX_LIMIT
from apps.notifications.models import Delivery, EventPolicy, NotificationPreference
from apps.training.enrollment_services import distribute_enrollment
from apps.training.models import Coach, Training, TrainingEnrollment
from apps.users.models import Notification
from tests.support.factories import make_player, make_user


@override_settings(
    NOTIFICATION_CENTER_ENABLED=True,
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
)
class TrainingEnrollmentDistributionTests(TestCase):
    """Только назначение администратором раскрывает заявку тренеру."""

    def setUp(self) -> None:
        self.admin = make_user(
            email="distributor@test.local",
            first_name="Анна",
            last_name="Администратор",
            is_staff=True,
            is_superuser=True,
        )
        self.original_user = make_user(email="original@test.local")
        self.other_user = make_user(email="other@test.local")
        self.original = Coach.objects.create(
            user=self.original_user, name="Иван", slug="original", city="Москва"
        )
        self.other = Coach.objects.create(
            user=self.other_user, name="Мария", slug="other", city="Москва"
        )
        self.inactive = Coach.objects.create(
            name="Неактивный", slug="inactive", city="Москва", is_active=False
        )
        self.training = Training.objects.create(
            coach=self.original,
            title="Тренировка Ивана",
            slug="original-training",
            city="Москва",
            description="Описание",
        )
        self.enrollment = TrainingEnrollment.objects.create(
            training=self.training,
            full_name="Пётр Игрок",
            telegram="@player",
            message="Хочу утром",
        )
        EventPolicy.objects.filter(key="training.enrollment").update(
            inbox_enabled=True, push_enabled=True, email_enabled=True
        )
        for user in (self.admin, self.original_user, self.other_user):
            NotificationPreference.objects.update_or_create(
                user=user, defaults={"push_enabled": True, "email_enabled": True}
            )
        self.url = reverse(
            "admin:training_trainingenrollment_distribute", args=[self.enrollment.pk]
        )
        self.list_url = reverse("admin:training_trainingenrollment_changelist")
        self.detail_url = reverse("enrollment_detail", args=[self.enrollment.pk])
        self.client.force_login(self.admin)

    def _assign(self, action: str, coach: Coach | None = None) -> None:
        """Нажать кнопку администратора и выполнить отложенные уведомления."""
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url,
                {"action": action, "coach_id": coach.pk if coach else ""},
                secure=True,
            )
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.enrollment.refresh_from_db()

    def test_pending_enrollment_notifies_only_administrators_on_all_channels(
        self,
    ) -> None:
        """До распределения нет email, ЛК и push исходному или другому тренеру."""
        send_training_enrollment_email(self.enrollment)
        self.assertEqual([message.to for message in mail.outbox], [[self.admin.email]])
        note = Notification.objects.get(event_key="training.enrollment")
        self.assertEqual(note.user.pk, self.admin.pk)
        for value in (
            "Пётр Игрок",
            "Иван",
            "@player",
            "Хочу утром",
            "Тренировка Ивана",
        ):
            self.assertIn(value, note.message)
        self.assertEqual(
            note.url,
            reverse(
                "admin:training_trainingenrollment_change", args=[self.enrollment.pk]
            ),
        )
        delivery = Delivery.objects.get(event_key="training.enrollment", channel="push")
        self.assertEqual(delivery.user_id, self.admin.pk)
        self.assertEqual(urlsplit(delivery.url).path, note.url)

    def test_long_training_enrollment_form_notifies_all_administrators(self) -> None:
        """Длинная реальная заявка не теряет ЛК, push и email ни одного администратора."""
        second_admin = make_user(
            email="second-distributor@test.local", is_staff=True, is_superuser=True
        )
        NotificationPreference.objects.create(
            user=second_admin, push_enabled=True, email_enabled=True
        )
        player = make_player(email_suffix="long-distribution-player")
        long_message = (
            "Хочу индивидуальную тренировку вечером. " * 60 + "Последнее пожелание."
        )
        for user in (None, player.user):
            with self.subTest(authenticated=user is not None):
                self.client.logout()
                if user is not None:
                    self.client.force_login(user)
                emails_before = len(mail.outbox)
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        reverse("training_enroll", args=[self.training.slug]),
                        {
                            "full_name": "Александр Шатайло",
                            "contact_method": "email",
                            "contact_value": "long-player@test.local",
                            "message": long_message,
                            "agree_legal": "on",
                        },
                        secure=True,
                    )
                self.assertEqual(response.status_code, 302)
                enrollment = TrainingEnrollment.objects.latest("pk")
                url = reverse(
                    "admin:training_trainingenrollment_change", args=[enrollment.pk]
                )
                notes = Notification.objects.filter(
                    event_key="training.enrollment", url=url
                )
                self.assertEqual(
                    set(notes.values_list("user_id", flat=True)),
                    {self.admin.pk, second_admin.pk},
                )
                for note in notes:
                    note.full_clean()
                    self.assertLessEqual(len(note.message), INBOX_LIMIT)
                    self.assertTrue(note.message.endswith("…"))
                    self.assertIn("Александр Шатайло", note.message)
                    self.assertTrue(note.is_visible)
                    push = Delivery.objects.get(notification=note, channel="push")
                    self.assertEqual(push.user_id, note.user.pk)
                emails = mail.outbox[emails_before:]
                self.assertEqual(len(emails), 2)
                for email in emails:
                    assert isinstance(email, EmailMultiAlternatives)
                    self.assertIn(long_message, str(email.alternatives[0][0]))
                self.assertEqual(enrollment.message, long_message)
                self.assertIsNone(enrollment.assigned_at)

    def test_long_training_enrollment_assignment_preserves_all_channels(self) -> None:
        """Каждая кнопка распределения доставляет длинную заявку назначенному получателю."""
        long_message = "Нужны упражнения для подачи. " * 80 + "Последнее пожелание."
        for action, recipient in (
            ("original", self.original_user),
            ("other", self.other_user),
            ("self", self.admin),
        ):
            with self.subTest(action=action):
                enrollment = TrainingEnrollment.objects.create(
                    training=self.training, full_name="Игрок", message=long_message
                )
                url = reverse(
                    "admin:training_trainingenrollment_distribute", args=[enrollment.pk]
                )
                emails_before = len(mail.outbox)
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        url,
                        {"action": action, "coach_id": self.other.pk},
                        secure=True,
                    )
                self.assertEqual(response.status_code, 302)
                note = Notification.objects.get(
                    url=reverse("enrollment_detail", args=[enrollment.pk]),
                    event_key="training.enrollment",
                )
                self.assertEqual(note.user.pk, recipient.pk)
                note.full_clean()
                self.assertLessEqual(len(note.message), INBOX_LIMIT)
                self.assertTrue(note.message.endswith("…"))
                push = Delivery.objects.get(notification=note, channel="push")
                self.assertEqual(push.user_id, recipient.pk)
                emails = mail.outbox[emails_before:]
                self.assertEqual(len(emails), 1)
                self.assertEqual(emails[0].to, [recipient.email])
                assert isinstance(emails[0], EmailMultiAlternatives)
                self.assertIn(long_message, str(emails[0].alternatives[0][0]))

    def test_authenticated_and_guest_forms_both_go_to_distribution(self) -> None:
        """Обе формы сохраняют исходного тренера и не назначают его автоматически."""
        player = make_player(email_suffix="distribution-player")
        for user in (None, player.user):
            self.client.logout()
            if user:
                self.client.force_login(user)
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("training_enroll", args=[self.training.slug]),
                    {
                        "full_name": "Игрок из формы",
                        "contact_method": "email",
                        "contact_value": "form@test.local",
                        "agree_legal": "on",
                        "assigned_coach": str(self.other.pk),
                        "action": "other",
                    },
                    secure=True,
                )
            self.assertEqual(response.status_code, 302)
        forms = TrainingEnrollment.objects.filter(full_name="Игрок из формы")
        self.assertEqual(forms.count(), 2)
        self.assertEqual(
            forms.filter(
                assigned_at__isnull=True,
                assigned_coach__isnull=True,
                requested_coach=self.original,
            ).count(),
            2,
        )
        self.assertEqual(forms.filter(player=player).count(), 1)
        self.assertFalse(
            Notification.objects.filter(
                user__in=[self.original_user, self.other_user]
            ).exists()
        )

    def test_pending_request_is_hidden_in_account_detail_and_contact(self) -> None:
        """Даже владелец тренировки не может получить нераспределённую заявку."""
        self.client.force_login(self.original_user)
        self.assertNotContains(
            self.client.get(reverse("my_trainings"), secure=True), "Пётр Игрок"
        )
        self.assertEqual(self.client.get(self.detail_url, secure=True).status_code, 404)
        contact = self.client.get(
            reverse("enrollment_contact", args=[self.enrollment.pk, "telegram"]),
            secure=True,
        )
        self.assertNotIn("t.me", contact["Location"])
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, TrainingEnrollment.Status.PENDING)

    def test_original_button_assigns_and_notifies_original_coach(self) -> None:
        """Отдать N раскрывает подробности исходному тренеру и создаёт три канала."""
        self._assign("original")
        self.assertEqual(self.enrollment.assigned_coach_id, self.original.pk)
        self.assertEqual(self.enrollment.assigned_by_id, self.admin.pk)
        self.assertEqual(mail.outbox[0].to, [self.original_user.email])
        self.assertEqual(Notification.objects.get().url, self.detail_url)
        self.assertEqual(
            Delivery.objects.get(channel="push").user_id, self.original_user.pk
        )
        self.client.force_login(self.original_user)
        self.assertContains(
            self.client.get(reverse("my_trainings"), secure=True), "Пётр Игрок"
        )
        self.assertContains(self.client.get(self.detail_url, secure=True), "Хочу утром")

    def test_other_button_preserves_training_and_original_coach(self) -> None:
        """Передаётся только заявка, а не сама тренировка; доступ получает новый тренер."""
        self._assign("other", self.other)
        self.training.refresh_from_db()
        self.assertEqual(self.training.coach, self.original)
        self.assertEqual(self.enrollment.requested_coach_id, self.original.pk)
        self.assertEqual(self.enrollment.assigned_coach_id, self.other.pk)
        self.assertEqual(mail.outbox[0].to, [self.other_user.email])
        for user, visible in ((self.original_user, False), (self.other_user, True)):
            self.client.force_login(user)
            response = self.client.get(reverse("my_trainings"), secure=True)
            if visible:
                self.assertContains(response, "Пётр Игрок")
            else:
                self.assertNotContains(response, "Пётр Игрок")
            self.assertEqual(
                self.client.get(self.detail_url, secure=True).status_code,
                200 if visible else 404,
            )
        response = self.client.get(
            reverse("enrollment_contact", args=[self.enrollment.pk, "telegram"]),
            secure=True,
        )
        self.assertEqual(response["Location"], "https://t.me/player")

    def test_take_self_creates_hidden_coach_and_adds_request_to_account(self) -> None:
        """Администратор без профиля получает скрытую карточку и собственную заявку."""
        self._assign("self")
        coach = Coach.objects.get(user=self.admin)
        self.assertFalse(coach.is_active)
        self.assertEqual(coach.name, "Анна Администратор")
        self.assertEqual(self.enrollment.assigned_coach_id, coach.pk)
        self.assertEqual(mail.outbox[0].to, [self.admin.email])
        self.assertEqual(Notification.objects.get().user.pk, self.admin.pk)
        self.assertEqual(Delivery.objects.get(channel="push").user_id, self.admin.pk)
        self.assertContains(
            self.client.get(reverse("my_trainings"), secure=True), "Пётр Игрок"
        )
        self.assertEqual(self.client.get(self.detail_url, secure=True).status_code, 200)

    def test_take_self_reuses_existing_profile(self) -> None:
        """Существующий профиль администратора не заменяется и не публикуется заново."""
        existing = Coach.objects.create(
            user=self.admin, name="Свой профиль", slug="admin-coach", city="Москва"
        )
        self._assign("self")
        self.assertEqual(self.enrollment.assigned_coach_id, existing.pk)
        self.assertEqual(Coach.objects.filter(user=self.admin).count(), 1)

    def test_duplicate_and_conflicting_posts_do_not_reassign_or_notify_twice(
        self,
    ) -> None:
        """Повторный или устаревший POST не отнимает заявку у назначенного тренера."""
        self._assign("original")
        assigned_at = self.enrollment.assigned_at
        self._assign("original")
        self._assign("other", self.other)
        self.assertEqual(self.enrollment.assigned_coach_id, self.original.pk)
        self.assertEqual(self.enrollment.assigned_at, assigned_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(Delivery.objects.filter(channel="push").count(), 1)

    def test_invalid_inactive_or_missing_coach_does_not_assign(self) -> None:
        """Подмена идентификатора не позволяет назначить неактивного тренера."""
        for action, coach_id in (
            ("other", self.inactive.pk),
            ("other", 999999),
            ("invalid", ""),
            ("other", "bad"),
        ):
            self.client.post(
                self.url, {"action": action, "coach_id": coach_id}, secure=True
            )
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.assigned_at)
        self.assertFalse(Notification.objects.exists())

    def test_selector_get_has_no_side_effects_and_only_active_other_coaches(
        self,
    ) -> None:
        """GET предлагает выбор, но не передаёт заявку и не создаёт уведомлений."""
        response = self.client.get(self.url, {"action": "self"}, secure=True)
        self.assertContains(response, "Отдать Мария")
        self.assertNotContains(response, "Неактивный")
        self.assertNotContains(response, "Отдать Иван")
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.assigned_at)
        self.assertFalse(Notification.objects.exists())

    def test_list_and_change_form_have_three_buttons_and_post_form(self) -> None:
        """У каждой нераспределённой записи есть три действия и отдельная CSRF-форма."""
        for url in (
            self.list_url,
            reverse(
                "admin:training_trainingenrollment_change", args=[self.enrollment.pk]
            ),
        ):
            response = self.client.get(url, secure=True)
            for label in (
                "Отдать Иван",
                "Забрать себе",
                "Отдать другому тренеру",
                f"enrollment-distribute-{self.enrollment.pk}",
                "csrfmiddlewaretoken",
            ):
                self.assertContains(response, label)

    def test_compact_list_preserves_search_filters_and_status_editing(self) -> None:
        """Компактный список сохраняет стандартный поиск, фильтры и изменение статуса."""
        response = self.client.get(self.list_url, secure=True)
        self.assertContains(response, "Заявки на тренировки")
        self.assertContains(response, "training_enrollment.css")
        self.assertContains(response, self.enrollment.get_display_name())
        changelist = response.context["cl"]
        self.assertEqual(len(changelist.list_display), 6)  # Пять колонок и checkbox.
        form = changelist.formset.forms[0]
        status = form.fields["status"]
        self.assertEqual(status.widget.attrs["aria-label"], "Статус заявки")
        data = {
            "_save": "Сохранить",
            f"{form.prefix}-id": str(self.enrollment.pk),
            f"{form.prefix}-status": TrainingEnrollment.Status.CONTACTED,
        }
        data.update(
            {
                field.html_name: str(field.value())
                for field in changelist.formset.management_form
            }
        )
        response = self.client.post(self.list_url, data, secure=True)
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, TrainingEnrollment.Status.CONTACTED)
        self.assertIsNone(self.enrollment.assigned_at)
        self.assertFalse(Notification.objects.exists())
        filtered = self.client.get(
            self.list_url, {"status__exact": "contacted", "q": "Пётр"}, secure=True
        )
        self.assertContains(filtered, "Пётр Игрок")
        self.assertContains(filtered, "Фильтры · применены")
        empty = self.client.get(
            self.list_url, {"status__exact": "completed"}, secure=True
        )
        self.assertNotContains(empty, "Пётр Игрок")

    def test_compact_list_escapes_user_fields_and_keeps_profile_name(self) -> None:
        """Поля заявки экранируются, а игрок без ФИО не превращается в пустую строку."""
        player = make_player(email_suffix="compact-list-player")
        self.enrollment.player = player
        self.enrollment.full_name = ""
        self.enrollment.telegram = '<script>alert("contact")</script>'
        self.enrollment.save(update_fields=["player", "full_name", "telegram"])
        self.training.title = '<script>alert("training")</script>'
        self.training.save(update_fields=["title"])
        response = self.client.get(self.list_url, secure=True)
        self.assertContains(response, player.user.get_display_name())
        self.assertNotContains(response, '<script>alert("contact")</script>')
        self.assertNotContains(response, '<script>alert("training")</script>')
        self.assertContains(
            response, "&lt;script&gt;alert(&quot;contact&quot;)&lt;/script&gt;"
        )

    def test_staff_without_permission_and_ordinary_coach_cannot_distribute(
        self,
    ) -> None:
        """Статус сотрудника сам по себе не даёт доступа к распределению."""
        staff = make_user(email="unprivileged@test.local", is_staff=True)
        for user in (staff, self.original_user):
            with self.assertRaises(PermissionDenied):
                distribute_enrollment(self.enrollment.pk, actor=user, action="self")
        self.client.force_login(staff)
        self.assertEqual(
            self.client.post(self.url, {"action": "self"}, secure=True).status_code, 403
        )
        self.assertEqual(self.client.get(self.list_url, secure=True).status_code, 403)

    def test_staff_with_explicit_permission_receives_and_distributes_requests(
        self,
    ) -> None:
        """Администратор с явным правом изменения записей получает их наряду с superuser."""
        staff = make_user(email="dispatcher@test.local", is_staff=True)
        staff.user_permissions.add(
            Permission.objects.get(codename="change_trainingenrollment")
        )
        send_training_enrollment_email(self.enrollment)
        self.assertEqual(
            set(Notification.objects.values_list("user_id", flat=True)),
            {self.admin.pk, staff.pk},
        )
        self.client.force_login(staff)
        self._assign("self")
        self.assertEqual(self.enrollment.assigned_coach.user_id, staff.pk)

    def test_csrf_is_required_for_distribution(self) -> None:
        """Распределение нельзя выполнить сторонней формой без CSRF-токена."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(
            client.post(self.url, {"action": "original"}, secure=True).status_code, 403
        )

    def test_email_disabled_does_not_suppress_inbox_or_push(self) -> None:
        """Отключённый email не мешает уведомлениям остальных каналов."""
        NotificationPreference.objects.filter(user=self.admin).update(
            email_enabled=False
        )
        send_training_enrollment_email(self.enrollment)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.get().user.pk, self.admin.pk)
        self.assertEqual(Delivery.objects.get(channel="push").user_id, self.admin.pk)

    def test_rollback_does_not_notify_or_persist_assignment(self) -> None:
        """Откат транзакции не раскрывает заявку и не оставляет уведомлений."""
        with (
            self.captureOnCommitCallbacks(execute=True),
            self.assertRaises(ValueError),
            transaction.atomic(),
        ):
            distribute_enrollment(
                self.enrollment.pk, actor=self.admin, action="original"
            )
            raise ValueError("Откат")
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.assigned_at)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(Notification.objects.exists())

    def test_smtp_failure_keeps_assignment_inbox_and_push(self) -> None:
        """Проблемы SMTP не отменяют назначение и другие каналы."""
        with patch("apps.core.email_service._send_html_email", return_value=False):
            self._assign("original")
        self.assertEqual(self.enrollment.assigned_coach_id, self.original.pk)
        self.assertEqual(Notification.objects.get().user.pk, self.original_user.pk)
        self.assertEqual(
            Delivery.objects.get(channel="push").user_id, self.original_user.pk
        )

    def test_source_coach_snapshot_survives_training_owner_change(self) -> None:
        """Отдать N использует выбор игрока, а не нового владельца тренировки."""
        self.training.coach = self.other
        self.training.save()
        self._assign("original")
        self.assertEqual(self.enrollment.assigned_coach_id, self.original.pk)
        self.assertEqual(self.enrollment.requested_coach_name, "Иван")

    @override_settings(NOTIFICATION_CENTER_ENABLED=False)
    def test_legacy_mode_still_creates_admin_inbox_without_push(self) -> None:
        """Даже без нового центра заявка приходит администратору в ЛК и на email."""
        send_training_enrollment_email(self.enrollment)
        self.assertEqual(Notification.objects.get().user.pk, self.admin.pk)
        self.assertEqual(mail.outbox[0].to, [self.admin.email])
        self.assertFalse(Delivery.objects.filter(channel="push").exists())

    def test_assigned_coach_without_email_still_receives_inbox_and_push(self) -> None:
        """Отсутствие email не лишает назначенного тренера остальных каналов."""
        self.other_user.email = ""
        self.other_user.save(update_fields=["email"])
        self._assign("other", self.other)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.get().user.pk, self.other_user.pk)
        self.assertEqual(
            Delivery.objects.get(channel="push").user_id, self.other_user.pk
        )

    def test_deleted_original_coach_requires_manual_selection(self) -> None:
        """Удаление карточки не теряет имя исходного тренера и не назначает случайного."""
        self.original.delete()
        self._assign("original")
        self.assertIsNone(self.enrollment.assigned_at)
        self.assertEqual(self.enrollment.requested_coach_name, "Иван")
        self._assign("other", self.other)
        self.assertEqual(self.enrollment.assigned_coach_id, self.other.pk)
