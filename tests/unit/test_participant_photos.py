"""Тесты загрузки фото участниками турнира."""

from __future__ import annotations

import io
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from apps.tournaments.models import Tournament, TournamentPhoto, TournamentWithdrawal
from apps.tournaments.photo_services import (
    can_participant_delete_photo,
    can_participant_upload_photo,
    get_participant_photo_count,
    is_active_tournament_participant,
)
from apps.users.models import Player, User
from config.validators import MAX_IMAGE_SIZE_BYTES


def _make_test_image(
    name: str = "photo.jpg", size: tuple[int, int] = (800, 600)
) -> SimpleUploadedFile:
    """Создать минимальное JPEG-изображение для тестов."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color=(120, 180, 90)).save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="image/jpeg")


class ParticipantPhotoServicesTestCase(TestCase):
    def setUp(self) -> None:
        self.tournament = Tournament.objects.create(
            name="Фото-турнир",
            slug="photo-tournament",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        self.participant_user = User.objects.create_user(
            email="participant@test.local",
            password="testpass123",
        )
        self.participant = Player.objects.create(user=self.participant_user)
        self.other_user = User.objects.create_user(
            email="other@test.local",
            password="testpass123",
        )
        self.other = Player.objects.create(user=self.other_user)
        self.tournament.participants.add(self.participant)

    def test_active_participant_can_upload(self) -> None:
        self.assertTrue(
            is_active_tournament_participant(self.tournament, self.participant)
        )
        self.assertTrue(can_participant_upload_photo(self.tournament, self.participant))

    def test_non_participant_cannot_upload(self) -> None:
        self.assertFalse(is_active_tournament_participant(self.tournament, self.other))
        self.assertFalse(can_participant_upload_photo(self.tournament, self.other))

    def test_withdrawn_participant_cannot_upload(self) -> None:
        TournamentWithdrawal.objects.create(
            tournament=self.tournament,
            player=self.participant,
        )
        self.assertFalse(
            is_active_tournament_participant(self.tournament, self.participant)
        )
        self.assertFalse(
            can_participant_upload_photo(self.tournament, self.participant)
        )

    def test_photo_limit_blocks_upload(self) -> None:
        for i in range(TournamentPhoto.PARTICIPANT_PHOTO_LIMIT):
            TournamentPhoto.objects.create(
                tournament=self.tournament,
                image=_make_test_image(f"p{i}.jpg"),
                uploaded_by=self.participant,
                order=i,
            )
        self.assertEqual(
            get_participant_photo_count(self.tournament, self.participant),
            TournamentPhoto.PARTICIPANT_PHOTO_LIMIT,
        )
        self.assertFalse(
            can_participant_upload_photo(self.tournament, self.participant)
        )

    def test_delete_only_own_photo(self) -> None:
        own = TournamentPhoto.objects.create(
            tournament=self.tournament,
            image=_make_test_image("own.jpg"),
            uploaded_by=self.participant,
        )
        admin_photo = TournamentPhoto.objects.create(
            tournament=self.tournament,
            image=_make_test_image("admin.jpg"),
        )
        self.assertTrue(can_participant_delete_photo(own, self.participant))
        self.assertFalse(can_participant_delete_photo(admin_photo, self.participant))


class ParticipantPhotoViewsTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.tournament = Tournament.objects.create(
            name="Фото-турнир views",
            slug="photo-tournament-views",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        self.participant_user = User.objects.create_user(
            email="participant-views@test.local",
            password="testpass123",
        )
        self.participant = Player.objects.create(user=self.participant_user)
        self.other_user = User.objects.create_user(
            email="other-views@test.local",
            password="testpass123",
        )
        self.other = Player.objects.create(user=self.other_user)
        self.tournament.participants.add(self.participant)
        self.upload_url = reverse(
            "tournament_photo_upload",
            kwargs={"slug": self.tournament.slug},
        )
        self.detail_url = reverse(
            "tournament_detail",
            kwargs={"slug": self.tournament.slug},
        )

    def test_non_participant_upload_forbidden(self) -> None:
        self.client.force_login(self.other_user)
        response = self.client.post(
            self.upload_url,
            {"image": _make_test_image()},
            secure=True,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TournamentPhoto.objects.count(), 0)

    def test_participant_can_upload_and_see_button(self) -> None:
        self.client.force_login(self.participant_user)
        detail = self.client.get(self.detail_url, secure=True)
        self.assertContains(detail, "Добавить фото")

        response = self.client.post(
            self.upload_url,
            {"image": _make_test_image(), "caption": "Матч дня"},
            secure=True,
        )
        self.assertRedirects(response, self.detail_url, fetch_redirect_response=False)
        photo = TournamentPhoto.objects.get()
        self.assertEqual(photo.uploaded_by_id, self.participant.id)
        self.assertEqual(photo.caption, "Матч дня")

    def test_upload_limit_enforced(self) -> None:
        self.client.force_login(self.participant_user)
        for i in range(TournamentPhoto.PARTICIPANT_PHOTO_LIMIT):
            response = self.client.post(
                self.upload_url,
                {"image": _make_test_image(f"limit-{i}.jpg")},
                secure=True,
            )
            self.assertRedirects(
                response, self.detail_url, fetch_redirect_response=False
            )

        response = self.client.post(
            self.upload_url,
            {"image": _make_test_image("limit-extra.jpg")},
            secure=True,
        )
        self.assertRedirects(response, self.detail_url, fetch_redirect_response=False)
        self.assertEqual(
            TournamentPhoto.objects.filter(uploaded_by=self.participant).count(),
            TournamentPhoto.PARTICIPANT_PHOTO_LIMIT,
        )

        detail = self.client.get(self.detail_url, secure=True)
        self.assertContains(detail, "максимальное количество фото")

    def test_participant_can_delete_own_photo_and_reupload(self) -> None:
        self.client.force_login(self.participant_user)
        for i in range(TournamentPhoto.PARTICIPANT_PHOTO_LIMIT):
            TournamentPhoto.objects.create(
                tournament=self.tournament,
                image=_make_test_image(f"del-{i}.jpg"),
                uploaded_by=self.participant,
                order=i,
            )

        photo = TournamentPhoto.objects.filter(uploaded_by=self.participant).first()
        assert photo is not None
        delete_url = reverse(
            "tournament_photo_delete",
            kwargs={"slug": self.tournament.slug, "pk": photo.pk},
        )
        response = self.client.post(delete_url, secure=True)
        self.assertRedirects(response, self.detail_url, fetch_redirect_response=False)
        self.assertFalse(TournamentPhoto.objects.filter(pk=photo.pk).exists())

        response = self.client.post(
            self.upload_url,
            {"image": _make_test_image("new-after-delete.jpg")},
            secure=True,
        )
        self.assertRedirects(response, self.detail_url, fetch_redirect_response=False)
        self.assertEqual(
            TournamentPhoto.objects.filter(uploaded_by=self.participant).count(),
            TournamentPhoto.PARTICIPANT_PHOTO_LIMIT,
        )

    def test_cannot_delete_other_participant_photo(self) -> None:
        other_photo = TournamentPhoto.objects.create(
            tournament=self.tournament,
            image=_make_test_image("other.jpg"),
            uploaded_by=self.other,
        )
        self.tournament.participants.add(self.other)
        self.client.force_login(self.participant_user)
        delete_url = reverse(
            "tournament_photo_delete",
            kwargs={"slug": self.tournament.slug, "pk": other_photo.pk},
        )
        response = self.client.post(delete_url, secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(TournamentPhoto.objects.filter(pk=other_photo.pk).exists())

    def test_non_participant_does_not_see_upload_button(self) -> None:
        self.client.force_login(self.other_user)
        response = self.client.get(self.detail_url, secure=True)
        self.assertNotContains(response, "Добавить фото")

    def test_image_is_compressed_on_save(self) -> None:
        self.client.force_login(self.participant_user)
        self.client.post(
            self.upload_url,
            {"image": _make_test_image(size=(2000, 1500))},
            secure=True,
        )
        photo = TournamentPhoto.objects.get()
        photo.image.open("rb")
        try:
            data = photo.image.read()
        finally:
            photo.image.close()
        self.assertLessEqual(len(data), MAX_IMAGE_SIZE_BYTES)
