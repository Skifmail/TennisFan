"""Тесты загрузки фото участниками турнира."""

from __future__ import annotations

import io
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentPhoto,
    TournamentPhotoPromptDismissal,
    TournamentStatus,
    TournamentWithdrawal,
)
from apps.tournaments.photo_services import (
    can_participant_delete_photo,
    can_participant_upload_photo,
    get_participant_photo_count,
    is_active_tournament_participant,
    should_show_photo_upload_prompt,
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

    def test_staff_manager_can_upload_without_participation(self) -> None:
        staff = User.objects.create_user(
            email="staff-photos@test.local",
            password="testpass123",
            is_staff=True,
        )
        Player.objects.create(user=staff)
        self.client.force_login(staff)

        detail = self.client.get(self.detail_url, secure=True)
        self.assertContains(detail, "Добавить фото")
        self.assertNotContains(detail, "ваших фото")

        response = self.client.post(
            self.upload_url,
            {"image": _make_test_image("staff.jpg"), "caption": "Орг"},
            secure=True,
        )
        self.assertRedirects(response, self.detail_url, fetch_redirect_response=False)
        self.assertEqual(TournamentPhoto.objects.count(), 1)

        # Лимит участников на управляющих не действует.
        for i in range(TournamentPhoto.PARTICIPANT_PHOTO_LIMIT + 1):
            response = self.client.post(
                self.upload_url,
                {"image": _make_test_image(f"staff-extra-{i}.jpg")},
                secure=True,
            )
            self.assertRedirects(
                response, self.detail_url, fetch_redirect_response=False
            )
        self.assertGreater(
            TournamentPhoto.objects.count(),
            TournamentPhoto.PARTICIPANT_PHOTO_LIMIT,
        )

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


PROMPT_TEXT = "Поделитесь впечатлениями"


class PhotoUploadPromptServiceTestCase(TestCase):
    def setUp(self) -> None:
        self.tournament = Tournament.objects.create(
            name="Активный фото-турнир",
            slug="active-photo-tournament",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.ACTIVE,
        )
        self.participant_user = User.objects.create_user(
            email="prompt-participant@test.local",
            password="testpass123",
        )
        self.participant = Player.objects.create(user=self.participant_user)
        self.other = Player.objects.create(
            user=User.objects.create_user(
                email="prompt-other@test.local",
                password="testpass123",
            )
        )
        self.tournament.participants.add(self.participant)

    def test_shows_for_active_participant_without_photos(self) -> None:
        self.assertTrue(
            should_show_photo_upload_prompt(self.tournament, self.participant)
        )

    def test_hides_for_upcoming_tournament(self) -> None:
        self.tournament.status = TournamentStatus.UPCOMING
        self.tournament.save(update_fields=["status"])
        self.assertFalse(
            should_show_photo_upload_prompt(self.tournament, self.participant)
        )

    def test_hides_for_completed_tournament(self) -> None:
        self.tournament.status = TournamentStatus.COMPLETED
        self.tournament.save(update_fields=["status"])
        self.assertFalse(
            should_show_photo_upload_prompt(self.tournament, self.participant)
        )

    def test_shows_for_group_stage_and_playoffs(self) -> None:
        for status in (TournamentStatus.GROUP_STAGE, TournamentStatus.PLAYOFFS):
            self.tournament.status = status
            self.tournament.save(update_fields=["status"])
            self.assertTrue(
                should_show_photo_upload_prompt(self.tournament, self.participant),
                msg=status,
            )

    def test_hides_for_non_participant(self) -> None:
        self.assertFalse(should_show_photo_upload_prompt(self.tournament, self.other))

    def test_hides_after_player_uploaded_photo(self) -> None:
        TournamentPhoto.objects.create(
            tournament=self.tournament,
            image=_make_test_image("prompt.jpg"),
            uploaded_by=self.participant,
        )
        self.assertFalse(
            should_show_photo_upload_prompt(self.tournament, self.participant)
        )

    def test_hides_after_player_dismissed_prompt(self) -> None:
        TournamentPhotoPromptDismissal.objects.create(
            tournament=self.tournament,
            player=self.participant,
        )
        self.assertFalse(
            should_show_photo_upload_prompt(self.tournament, self.participant)
        )


class PhotoUploadPromptViewsTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.tournament = Tournament.objects.create(
            name="Промпт views",
            slug="photo-prompt-views",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.ACTIVE,
        )
        self.participant_user = User.objects.create_user(
            email="prompt-views@test.local",
            password="testpass123",
        )
        self.participant = Player.objects.create(user=self.participant_user)
        self.other_user = User.objects.create_user(
            email="prompt-other-views@test.local",
            password="testpass123",
        )
        Player.objects.create(user=self.other_user)
        self.opponent = Player.objects.create(
            user=User.objects.create_user(
                email="prompt-opponent@test.local",
                password="testpass123",
            )
        )
        self.tournament.participants.add(self.participant, self.opponent)
        self.detail_url = reverse(
            "tournament_detail", kwargs={"slug": self.tournament.slug}
        )
        self.tables_url = reverse(
            "tournament_tables_detail", kwargs={"slug": self.tournament.slug}
        )
        self.dismiss_url = reverse(
            "tournament_photo_prompt_dismiss",
            kwargs={"slug": self.tournament.slug},
        )
        self.match = Match.objects.create(
            tournament=self.tournament,
            player1=self.participant,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
        )
        self.match_url = reverse("match_detail", kwargs={"pk": self.match.pk})

    def test_participant_sees_prompt_on_tournament_pages(self) -> None:
        self.client.force_login(self.participant_user)
        for url in (self.detail_url, self.match_url, self.tables_url):
            response = self.client.get(url, secure=True)
            self.assertContains(response, PROMPT_TEXT, msg_prefix=url)
            self.assertContains(response, "Не напоминать больше", msg_prefix=url)
            self.assertContains(response, "Позже", msg_prefix=url)

    def test_non_participant_does_not_see_prompt(self) -> None:
        self.client.force_login(self.other_user)
        response = self.client.get(self.detail_url, secure=True)
        self.assertNotContains(response, PROMPT_TEXT)

    def test_dismiss_hides_prompt_on_next_visit(self) -> None:
        self.client.force_login(self.participant_user)
        response = self.client.post(self.dismiss_url, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TournamentPhotoPromptDismissal.objects.filter(
                tournament=self.tournament,
                player=self.participant,
            ).exists()
        )
        detail = self.client.get(self.detail_url, secure=True)
        self.assertNotContains(detail, PROMPT_TEXT)

    def test_later_without_dismiss_shows_prompt_again(self) -> None:
        self.client.force_login(self.participant_user)
        first = self.client.get(self.detail_url, secure=True)
        self.assertContains(first, PROMPT_TEXT)
        second = self.client.get(self.detail_url, secure=True)
        self.assertContains(second, PROMPT_TEXT)

    def test_dismiss_requires_login(self) -> None:
        response = self.client.post(self.dismiss_url, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_non_participant_cannot_dismiss(self) -> None:
        self.client.force_login(self.other_user)
        response = self.client.post(self.dismiss_url, secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TournamentPhotoPromptDismissal.objects.exists())
