"""Редактирование проверенного профиля, приватность фото и компактные настройки."""

from io import BytesIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.juniors.competition import register
from apps.juniors.models import AuditEvent, ChildProfile, GuardianLink, LegalRelease
from apps.juniors.services import update_draft
from tests.integration.test_junior_competition import participant, setup_tournament
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def private_profile(client, settings, tmp_path, monkeypatch):
    settings.SECURE_SSL_REDIRECT = False
    settings.JUNIOR_LEAGUE_ENABLED = True
    storage = FileSystemStorage(location=tmp_path / "private")
    monkeypatch.setattr(ChildProfile._meta.get_field("photo"), "storage", storage)
    _, tournament, release = setup_tournament()
    parent, child = participant(1, release)
    # В режиме разработки UI действий доступен только администраторам.
    parent.is_staff = True
    parent.save(update_fields=["is_staff"])
    client.force_login(parent)
    return parent, child, tournament, release, storage


def photo_file():
    buffer = BytesIO()
    image = Image.new("RGB", (1400, 900), "green")
    image.save(buffer, "JPEG", exif=Image.Exif())
    return SimpleUploadedFile(
        "child-private-name.jpg", buffer.getvalue(), content_type="image/jpeg"
    )


def payload(child):
    return {
        "first_name": child.first_name,
        "last_name": child.last_name,
        "birth_date": child.birth_date.isoformat(),
        "city": "Казань",
        "gender": child.gender,
        "ball": child.ball,
    }


def test_verified_parent_can_edit_city_and_photo_through_form(client, private_profile):
    parent, child, _, _, storage = private_profile
    url = reverse("juniors:edit_child", args=[child.pk])
    page = client.get(url)
    assert page.status_code == 200
    assert 'enctype="multipart/form-data"' in page.content.decode()
    assert page.context["form"].fields["birth_date"].disabled
    data = payload(child)
    data.update(photo=photo_file(), photo_accepted="on", first_name="Подменённое имя")
    response = client.post(url, data)
    assert response.status_code == 302
    child.refresh_from_db()
    assert child.city == "Казань"
    assert child.first_name != "Подменённое имя"
    assert child.status == "active"
    assert storage.exists(child.photo.name)
    assert "child-private-name" not in child.photo.name
    with child.photo.open("rb") as stream:
        image = Image.open(stream)
        assert max(image.size) <= 1024
        assert not image.getexif()
    assert AuditEvent.objects.filter(
        child=child, actor=parent, action="photo.accepted"
    ).exists()
    profile = client.get(reverse("juniors:child", args=[child.pk])).content.decode()
    assert 'id="profile-settings"' in profile
    assert "Ваши согласия" not in profile
    assert "Как ребёнок виден другим" not in profile
    assert "jl-badge--verified" in profile
    assert "Редактировать профиль" in profile
    photo = client.get(reverse("juniors:child_photo", args=[child.pk]))
    assert photo.status_code == 200
    assert photo["Content-Type"] == "image/jpeg"
    assert "no-store" in photo["Cache-Control"]
    photo.close()
    outsider = make_user(email="photo-stranger@kids.test", is_staff=True)
    client.force_login(outsider)
    assert (
        client.get(reverse("juniors:child_photo", args=[child.pk])).status_code == 403
    )
    client.logout()
    assert (
        client.get(reverse("juniors:child_photo", args=[child.pk])).status_code == 302
    )
    public = client.get(
        reverse("juniors:tournament", args=[private_profile[2].pk])
    ).content.decode()
    assert "/photo/" not in public


def test_photo_requires_confirmation_and_valid_image(client, private_profile):
    _, child, _, _, storage = private_profile
    url = reverse("juniors:edit_child", args=[child.pk])
    for upload, confirmed in [
        (photo_file(), False),
        (
            SimpleUploadedFile("bad.jpg", b"not-an-image", content_type="image/jpeg"),
            True,
        ),
    ]:
        data = payload(child)
        data.update(photo=upload)
        if confirmed:
            data["photo_accepted"] = "on"
        response = client.post(url, data)
        assert response.status_code == 200
        assert response.context["form"].errors
    child.refresh_from_db()
    assert not child.photo
    assert child.city != "Казань"
    assert storage.listdir("")[1] == [] if storage.exists("") else True


def test_photo_can_be_removed_and_replaced(
    client, private_profile, django_capture_on_commit_callbacks
):
    parent, child, _, _, storage = private_profile
    for index in range(2):
        old_name = child.photo.name
        with django_capture_on_commit_callbacks(execute=True):
            child = update_draft(
                actor=parent,
                child_id=child.pk,
                data={"photo": photo_file(), "photo_accepted": True},
            )
        if index:
            assert not storage.exists(old_name)
    old_name = child.photo.name
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("juniors:edit_child", args=[child.pk]),
            {**payload(child), "remove_photo": "on"},
        )
        assert response.status_code == 302
    child.refresh_from_db()
    assert not child.photo
    assert not storage.exists(old_name)
    assert (
        client.get(reverse("juniors:child_photo", args=[child.pk])).status_code == 404
    )


def test_edit_service_protects_identity_and_live_ball(private_profile):
    parent, child, tournament, _, _ = private_profile
    for field, value in [
        ("first_name", "Другое имя"),
        ("birth_date", child.birth_date.replace(year=child.birth_date.year - 1)),
        ("gender", "male" if child.gender == "female" else "female"),
    ]:
        with pytest.raises(ValidationError):
            update_draft(actor=parent, child_id=child.pk, data={field: value})
    register(
        actor=parent,
        child_id=child.pk,
        tournament_id=tournament.pk,
        regulation_version=1,
    )
    with pytest.raises(ValidationError, match="Нельзя менять мяч"):
        update_draft(actor=parent, child_id=child.pk, data={"ball": "orange"})
    with pytest.raises(PermissionDenied):
        update_draft(
            actor=make_user(email="edit-stranger@kids.test"),
            child_id=child.pk,
            data={"city": "Казань"},
        )
    GuardianLink.objects.filter(child=child, user=parent).update(status="revoked")
    with pytest.raises(PermissionDenied):
        update_draft(actor=parent, child_id=child.pk, data={"city": "Казань"})


def test_name_switch_withdraws_publication_but_keeps_processing(
    client, private_profile
):
    parent, child, _, release, _ = private_profile
    url = reverse("juniors:consent", args=[child.pk])
    assert (
        client.post(
            url, {"action": "set_publication", "release": release.pk, "accepted": "yes"}
        ).status_code
        == 302
    )
    assert (
        child.guardians.get(user=parent)
        .consents.filter(kind="publication", withdrawn_at__isnull=True)
        .exists()
    )
    release.is_current = False
    release.save()
    assert (
        client.post(url, {"action": "set_publication", "accepted": "yes"}).status_code
        == 302
    )
    assert client.post(url, {"action": "set_publication"}).status_code == 302
    assert (
        not child.guardians.get(user=parent)
        .consents.filter(kind="publication", withdrawn_at__isnull=True)
        .exists()
    )
    assert (
        child.guardians.get(user=parent)
        .consents.filter(kind="processing", withdrawn_at__isnull=True)
        .exists()
    )
    child.refresh_from_db()
    assert child.status == "active"


def test_settings_are_rendered_once_with_working_controls(client, private_profile):
    _, child, _, _, _ = private_profile
    html = client.get(reverse("juniors:child", args=[child.pk])).content.decode()
    assert html.count('id="profile-settings"') == 1
    assert html.count('id="name-visibility-title"') == 1
    assert html.count('name="action" value="set_publication"') == 1
    assert "Настройка станет доступна" not in html
    assert "Оформление согласия пока недоступно" not in html


def test_empty_settings_are_hidden_until_there_is_an_action(client, private_profile):
    _, child, _, release, _ = private_profile
    link = child.guardians.get()
    link.consents.all().delete()
    link.status = "pending"
    link.save(update_fields=["status"])
    release.is_current = False
    release.save(update_fields=["is_current"])
    child.status = "draft"
    child.save(update_fields=["status"])
    html = client.get(reverse("juniors:child", args=[child.pk])).content.decode()
    assert 'id="profile-settings"' not in html
    assert "Имя в результатах" not in html
    assert "Оформление согласия пока недоступно" not in html


def test_existing_processing_consent_can_be_withdrawn_without_current_release(
    client, private_profile
):
    _, child, _, release, _ = private_profile
    release.is_current = False
    release.save(update_fields=["is_current"])
    html = client.get(reverse("juniors:child", args=[child.pk])).content.decode()
    assert html.count('id="profile-settings"') == 1
    assert "Отозвать согласие на обработку" in html
    assert LegalRelease.objects.get(is_current=True).reviewed


def test_regular_parent_can_manage_consents(client, private_profile):
    parent, child, _, _, _ = private_profile
    parent.is_staff = False
    parent.save(update_fields=["is_staff"])
    html = client.get(reverse("juniors:child", args=[child.pk])).content.decode()
    assert html.count('id="profile-settings"') == 1
    assert "<summary>Настройки профиля</summary>" in html
    assert "<summary>Согласия</summary>" not in html
    assert "Посмотреть выданное согласие" in html
    assert 'name="action" value="set_publication"' in html
