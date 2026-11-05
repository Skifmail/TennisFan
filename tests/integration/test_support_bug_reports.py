"""Отчёты об ошибках: приватные скриншоты, общий диалог и ответы администратора."""

import json
from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from apps.core.models import BugReport, SupportAttachment, SupportMessage, SupportThread
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def support_settings(settings, tmp_path, monkeypatch):
    settings.SECURE_SSL_REDIRECT = False
    storage = FileSystemStorage(location=tmp_path / "private-support")
    monkeypatch.setattr(SupportAttachment._meta.get_field("image"), "storage", storage)
    with (
        patch("apps.core.views.send_admin_support_notification"),
        patch("apps.core.views.send_user_support_reply_async"),
        patch("apps.core.support_notifications.send_user_support_reply_async"),
    ):
        yield storage


def image():
    data = BytesIO()
    Image.new("RGB", (80, 60), "green").save(data, "PNG")
    return SimpleUploadedFile(
        "secret-name.png", data.getvalue(), content_type="image/png"
    )


def post_report(client):
    return client.post(
        reverse("feedback_submit"),
        {
            "kind": "bug",
            "message": "На странице турнира кнопка не работает. Нажимаю запись, ничего не происходит.",
            "guest_name": "Тестовый гость",
            "guest_contact": "guest@support.test",
            "page_url": "https://tennisfan.ru/kids/tournaments/?secret=hidden#token",
            "attachments": image(),
        },
    )


def test_report_and_images_live_in_same_support_thread(client, support_settings):
    parent = make_user(email="reporter@support.test")
    client.force_login(parent)
    response = post_report(client)
    assert response.status_code == 200
    report = BugReport.objects.select_related("message").get()
    assert report.message.thread.user_id == parent.pk
    assert report.page_url == "/kids/tournaments/"
    assert report.status == "new"
    attachment = report.message.attachments.get()
    assert "secret-name" not in attachment.image.name
    with attachment.image.open("rb") as file:
        assert Image.open(file).format == "JPEG"
    url = reverse("support_attachment", args=[attachment.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    response.close()
    payload = client.get(reverse("feedback_threads")).json()["threads"][0]["messages"][
        0
    ]
    assert payload["bug_report"]["id"] == report.pk
    assert payload["attachments"][0]["url"] == url
    stranger = make_user(email="stranger@support.test")
    client.force_login(stranger)
    assert client.get(url).status_code == 403
    client.logout()
    assert client.get(url).status_code == 403


def test_guest_owns_attachment_by_session_and_receives_reply(client):
    assert post_report(client).status_code == 200
    report = BugReport.objects.get()
    attachment = report.message.attachments.get()
    url = reverse("support_attachment", args=[attachment.pk])
    assert client.get(url).status_code == 200
    other = Client()
    assert other.get(url).status_code == 403
    admin = make_user(email="admin@support.test", is_staff=True, is_superuser=True)
    other.force_login(admin)
    assert other.get(url).status_code == 200
    assert (
        other.post(
            reverse("support_admin_reply"),
            data=json.dumps(
                {
                    "thread_id": report.message.thread_id,
                    "bug_report_id": report.pk,
                    "text": "Спасибо! Проверяем кнопку.",
                }
            ),
            content_type="application/json",
        ).status_code
        == 200
    )
    report.refresh_from_db()
    assert report.status == "in_progress"
    reply = report.replies.get()
    assert reply.thread_id == report.message.thread_id and reply.is_from_admin
    assert (
        client.get(reverse("feedback_threads")).json()["threads"][0]["messages"][-1][
            "bug_reply_to"
        ]
        == report.pk
    )


@pytest.mark.parametrize(
    "case", ["not_image", "too_many", "oversized", "empty_text", "long_text"]
)
def test_invalid_submission_does_not_create_records_or_files(
    client, support_settings, case
):
    data = {
        "kind": "bug",
        "message": "Описание ошибки",
        "guest_name": "Гость",
        "guest_contact": "guest@support.test",
        "attachments": image(),
    }
    if case == "not_image":
        data["attachments"] = SimpleUploadedFile(
            "bad.svg", b"<svg><script>alert(1)</script></svg>", content_type="image/png"
        )
    elif case == "too_many":
        data["attachments"] = [image() for _ in range(4)]
    elif case == "oversized":
        data["attachments"] = SimpleUploadedFile(
            "huge.png", b"x" * (5 * 1024 * 1024 + 1), content_type="image/png"
        )
    elif case == "empty_text":
        data["message"] = ""
    else:
        data["message"] = "x" * 6001
    assert client.post(reverse("feedback_submit"), data).status_code == 400
    assert not SupportMessage.objects.exists()
    assert not BugReport.objects.exists()
    assert not SupportAttachment.objects.exists()
    assert not support_settings.exists("screenshots")


def test_normal_json_messages_and_image_messages_still_work(client):
    user = make_user(email="regular@support.test")
    client.force_login(user)
    response = client.post(
        reverse("feedback_submit"),
        json.dumps({"message": "Обычный вопрос"}),
        content_type="application/json",
    )
    assert response.status_code == 200 and response.json()["bug_report"] is None
    response = client.post(
        reverse("feedback_submit"), {"message": "Вопрос с фото", "attachments": image()}
    )
    assert response.status_code == 200
    assert SupportMessage.objects.count() == 2 and not BugReport.objects.exists()
    assert SupportThread.objects.count() == 1
    assert SupportAttachment.objects.count() == 1


def test_admin_error_page_and_support_dialog_reply(
    client, django_capture_on_commit_callbacks
):
    assert post_report(client).status_code == 200
    report = BugReport.objects.get()
    admin = make_user(email="bug-admin@support.test", is_staff=True, is_superuser=True)
    client.force_login(admin)
    detail_url = reverse("admin:core_bugreport_change", args=[report.pk])
    assert client.get(reverse("admin:core_bugreport_changelist")).status_code == 200
    html = client.get(detail_url).content.decode()
    assert "Ответ пользователю" in html and "Скриншоты" in html
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            detail_url,
            {
                "status": "resolved",
                "reply": "Кнопка исправлена. Попробуйте ещё раз.",
                "_save": "Сохранить",
            },
        )
    assert response.status_code == 302
    report.refresh_from_db()
    assert report.status == "resolved"
    assert report.replies.count() == 1
    thread_html = client.get(
        reverse("support_admin_thread", args=[report.message.thread_id])
    ).content.decode()
    assert "support-message--bug" in thread_html
    assert "Сообщение об ошибке" in thread_html
    assert "Кнопка исправлена" in thread_html


def test_reply_cannot_be_attached_to_other_thread_and_needs_admin(client):
    assert post_report(client).status_code == 200
    report = BugReport.objects.get()
    user = make_user(email="no-admin@support.test")
    client.force_login(user)
    payload = {
        "thread_id": report.message.thread_id,
        "bug_report_id": report.pk,
        "text": "Ответ",
    }
    assert (
        client.post(
            reverse("support_admin_reply"),
            json.dumps(payload),
            content_type="application/json",
        ).status_code
        == 403
    )
    client.post(reverse("feedback_submit"), {"message": "Другой диалог"})
    another = SupportThread.objects.get(user=user)
    user.is_staff = True
    user.is_superuser = True
    user.save()
    payload["thread_id"] = another.pk
    assert (
        client.post(
            reverse("support_admin_reply"),
            json.dumps(payload),
            content_type="application/json",
        ).status_code
        == 400
    )
    assert report.replies.count() == 0


def test_report_post_requires_csrf():
    client = Client(enforce_csrf_checks=True)
    assert post_report(client).status_code == 403


def test_deleting_message_removes_private_file_after_commit(
    client, support_settings, django_capture_on_commit_callbacks
):
    assert post_report(client).status_code == 200
    message = SupportMessage.objects.get()
    filename = message.attachments.get().image.name
    assert support_settings.exists(filename)
    with django_capture_on_commit_callbacks(execute=True):
        message.delete()
    assert not support_settings.exists(filename)
    assert not BugReport.objects.exists()


def test_guest_continues_without_entering_contacts_again(client):
    assert post_report(client).status_code == 200
    original_thread = BugReport.objects.get().message.thread_id
    response = client.post(
        reverse("feedback_submit"),
        {"message": "Дополнение после повторного открытия чата"},
    )
    assert response.status_code == 200
    assert SupportMessage.objects.latest("pk").thread_id == original_thread
    assert SupportThread.objects.count() == 1


@pytest.mark.parametrize("payload", [[], {"message": 123}, {"kind": []}])
def test_invalid_json_payload_is_rejected(client, payload):
    response = client.post(
        reverse("feedback_submit"), json.dumps(payload), content_type="application/json"
    )
    assert response.status_code == 400
    assert not SupportMessage.objects.exists()
