"""Подача, модерация и уведомления о заявках стрингеров."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from io import BytesIO
from tempfile import TemporaryDirectory

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from apps.content.models import (
    StringerApplication,
    StringerCompany,
    StringerCompanyContact,
    StringerCompanyPhoto,
    StringerPage,
)
from apps.content.stringer_services import review_stringer_application
from apps.notifications.models import Delivery, NotificationPreference
from apps.users.models import Notification, User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def settings_for_applications() -> Iterator[None]:
    """Изолирует файлы и запрещает реальные внешние отправки."""
    with (
        TemporaryDirectory() as media,
        override_settings(
            SECURE_SSL_REDIRECT=False,
            ALLOWED_HOSTS=["testserver"],
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            NOTIFICATION_CENTER_ENABLED=True,
            ADMIN_NOTIFICATIONS_EMAIL="moderator@example.test",
            SITE_URL="https://tennisfan.ru",
            ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
            MEDIA_ROOT=media,
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
                },
            },
        ),
    ):
        yield


@pytest.fixture
def applicant() -> User:
    """Создаёт обычного заявителя."""
    return User.objects.create_user(email="stringer@example.test")


@pytest.fixture
def moderator() -> User:
    """Создаёт администратора с согласием на push."""
    user = User.objects.create_superuser(
        email="moderator@example.test", password="test-password"
    )
    NotificationPreference.objects.create(user=user, push_enabled=True)
    return user


def application_data() -> dict[str, str]:
    """Возвращает заполненную публичную форму с комплектом полей фотографий."""
    return {
        "name": "Струны тест",
        "address": "Москва, улица Тестовая, 1",
        "price": "от 500 руб.",
        "description": "Натяжка теннисных ракеток",
        "contact_name": "Иван",
        "email": "contact@example.test",
        "phone": "+79991234567",
        "telegram": "stringer_test",
        "whatsapp": "+79991234567",
        "max_contact": "https://max.ru/stringer_test",
        "photos-TOTAL_FORMS": "3",
        "photos-INITIAL_FORMS": "0",
        "photos-MIN_NUM_FORMS": "0",
        "photos-MAX_NUM_FORMS": "3",
    }


def test_submission_queues_admin_channels_and_hides_until_approved(
    client: Client, applicant: User, moderator: User
) -> None:
    """Заявка не публикуется до одобрения; ЛК, email и push ведут на модерацию."""
    client.force_login(applicant)
    stream = BytesIO()
    Image.new("RGB", (20, 20), "red").save(stream, format="PNG")
    data = application_data()
    data["is_active"] = "true"
    data["status"] = "approved"
    response = client.post(
        reverse("stringer_application"),
        {
            **data,
            "photos-0-image": SimpleUploadedFile(
                "photo.png", stream.getvalue(), content_type="image/png"
            ),
            "photos-0-caption": "Мастерская",
        },
    )
    assert response.status_code == 302
    application = StringerApplication.objects.get()
    assert application.status == "pending"
    assert application.applicant == applicant
    assert not StringerCompany.objects.exists()
    assert application.photos.count() == 1
    note = Notification.objects.get(
        user=moderator, event_key="stringer.application_submitted"
    )
    assert note.url == reverse(
        "admin:content_stringerapplication_change", args=[application.pk]
    )
    assert set(
        Delivery.objects.filter(notification=note).values_list("channel", flat=True)
    ) == {"email", "push"}
    assert "Струны тест" not in client.get(reverse("stringers")).content.decode()
    review_stringer_application(application.pk, actor=moderator, approve=True)
    review_stringer_application(application.pk, actor=moderator, approve=True)
    company = StringerCompany.objects.get()
    assert StringerCompanyContact.objects.filter(company=company).count() == 4
    assert StringerCompanyPhoto.objects.get(company=company).caption == "Мастерская"
    assert "Струны тест" in client.get(reverse("stringers")).content.decode()
    application.refresh_from_db()
    assert application.company == company
    assert application.reviewed_by == moderator
    assert application.reviewed_at is not None


def test_submission_requires_login_and_valid_form(
    client: Client, applicant: User
) -> None:
    """Гость входит в аккаунт, невалидная форма не создаёт заявку."""
    assert client.get(reverse("stringer_application")).status_code == 302
    client.force_login(applicant)
    data = application_data()
    data["phone"] = "invalid"
    data["name"] = ""
    assert client.post(reverse("stringer_application"), data).status_code == 200
    assert not StringerApplication.objects.exists()
    data = application_data()
    assert (
        client.post(
            reverse("stringer_application"),
            {
                **data,
                "photos-0-image": SimpleUploadedFile(
                    "fake.png", b"not an image", content_type="image/png"
                ),
            },
        ).status_code
        == 200
    )
    assert not StringerApplication.objects.exists()


def test_pending_duplicate_and_private_history(
    client: Client, applicant: User, moderator: User
) -> None:
    """Повторный POST не дублирует заявку и уведомления; чужая история закрыта."""
    client.force_login(applicant)
    client.post(reverse("stringer_application"), application_data())
    client.post(reverse("stringer_application"), application_data())
    assert StringerApplication.objects.count() == 1
    assert Notification.objects.filter(user=moderator).count() == 1
    other = User.objects.create_user(email="other@example.test")
    client.force_login(other)
    assert (
        "Струны тест"
        not in client.get(reverse("stringer_application")).content.decode()
    )
    with pytest.raises(PermissionError):
        review_stringer_application(
            StringerApplication.objects.get().pk, actor=other, approve=True
        )


def test_admin_detail_approval_and_rejection(
    client: Client, applicant: User, moderator: User
) -> None:
    """Администратор принимает решение на странице заявки; отклонённая не публикуется."""
    client.force_login(applicant)
    client.post(reverse("stringer_application"), application_data())
    application = StringerApplication.objects.get()
    client.force_login(moderator)
    url = reverse("admin:content_stringerapplication_change", args=[application.pk])
    assert client.get(url).status_code == 200
    assert (
        client.post(
            url,
            {
                "moderation_comment": "Добавьте подробности",
                "_reject": "1",
                "photos-TOTAL_FORMS": "0",
                "photos-INITIAL_FORMS": "0",
            },
        ).status_code
        == 302
    )
    application.refresh_from_db()
    assert application.status == "rejected"
    assert not StringerCompany.objects.exists()
    client.force_login(applicant)
    assert (
        "Добавьте подробности"
        in client.get(reverse("stringer_application")).content.decode()
    )
    client.post(reverse("stringer_application"), application_data())
    latest = StringerApplication.objects.filter(status="pending").get()
    client.force_login(moderator)
    assert (
        client.post(
            reverse("admin:content_stringerapplication_change", args=[latest.pk]),
            {"_approve": "1", "photos-TOTAL_FORMS": "0", "photos-INITIAL_FORMS": "0"},
        ).status_code
        == 302
    )
    assert StringerCompany.objects.count() == 1


def test_disabled_center_sends_email_after_commit(
    client: Client,
    applicant: User,
    moderator: User,
    django_capture_on_commit_callbacks: Callable[
        ..., AbstractContextManager[list[Callable[[], None]]]
    ],
) -> None:
    """При отключённом центре остаются письмо и уведомление в ЛК."""
    client.force_login(applicant)
    with (
        override_settings(NOTIFICATION_CENTER_ENABLED=False),
        django_capture_on_commit_callbacks(execute=True),
    ):
        client.post(reverse("stringer_application"), application_data())
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [moderator.email]
    assert "/change/" in mail.outbox[0].body
    assert Notification.objects.filter(user=moderator).exists()
    assert not Delivery.objects.exists()


def test_disabled_page_blocks_application(client: Client, applicant: User) -> None:
    """Отключённая страница не принимает новые заявки."""
    page = StringerPage.get_singleton()
    page.is_enabled = False
    page.save()
    client.force_login(applicant)
    assert (
        client.post(reverse("stringer_application"), application_data()).status_code
        == 404
    )
