"""Тесты автогенерации slug корта в админ-форме (кириллица)."""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core.sports import VenueSport
from apps.courts.forms import CourtAdminForm, _normalize_website, _unique_court_slug
from apps.courts.models import Court, CourtApplication
from apps.courts.surfaces import CourtSurface, PadelSurface


class CourtAdminSlugFormTestCase(TestCase):
    """Пустой slug при русском названии не должен валить форму и сбрасывать фото."""

    def _base_data(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "name": "Теннис-центр Сочи",
            "slug": "",
            "city": "Сочи",
            "address": "Курортный проспект, 45",
            "courts_count": 1,
            "is_indoor": True,
            "indoor_surfaces": [CourtSurface.HARD],
            "is_outdoor": False,
            "is_active": True,
        }
        data.update(overrides)
        return data

    def test_empty_slug_auto_filled_from_cyrillic_name(self) -> None:
        form = CourtAdminForm(data=self._base_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "теннис-центр-сочи")

    def test_duplicate_slug_gets_suffix(self) -> None:
        Court.objects.create(
            name="Теннис-центр Сочи",
            slug="теннис-центр-сочи",
            city="Сочи",
            address="ул. 1",
            indoor_surfaces=[CourtSurface.HARD],
            is_indoor=True,
        )
        form = CourtAdminForm(data=self._base_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "теннис-центр-сочи-1")

    def test_website_without_scheme_normalized(self) -> None:
        self.assertEqual(_normalize_website("example.com"), "https://example.com")
        form = CourtAdminForm(data=self._base_data(website="club.sochi.ru"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["website"], "https://club.sochi.ru")

    def test_form_valid_with_uploaded_image_and_empty_slug(self) -> None:
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (32, 32), color=(10, 120, 40)).save(buffer, format="JPEG")
        image = SimpleUploadedFile(
            "court.jpg",
            buffer.getvalue(),
            content_type="image/jpeg",
        )
        form = CourtAdminForm(
            data=self._base_data(),
            files={"image": image},
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["slug"])

    def test_tennis_venue_clears_padel_fields(self) -> None:
        form = CourtAdminForm(
            data=self._base_data(
                venue_sport=VenueSport.TENNIS,
                has_glass_walls=True,
                padel_surfaces=[PadelSurface.ARTIFICIAL_GRASS],
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["has_glass_walls"])
        self.assertEqual(form.cleaned_data["padel_surfaces"], [])

    def test_padel_venue_keeps_padel_fields(self) -> None:
        form = CourtAdminForm(
            data=self._base_data(
                venue_sport=VenueSport.PADEL,
                has_glass_walls=True,
                padel_surfaces=[PadelSurface.ARTIFICIAL_GRASS],
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["has_glass_walls"])
        self.assertEqual(
            form.cleaned_data["padel_surfaces"],
            [PadelSurface.ARTIFICIAL_GRASS],
        )

    def test_unique_court_slug_helper(self) -> None:
        self.assertEqual(_unique_court_slug("Hello Club"), "hello-club")

    def test_long_name_slug_fits_field_limit(self) -> None:
        """Длинное название не переполняет slug (varchar(50))."""
        long_name = (
            "Очень длинное название теннисного клуба для проверки переполнения slug"
        )
        form = CourtAdminForm(data=self._base_data(name=long_name))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertLessEqual(len(form.cleaned_data["slug"]), 50)

    def test_duplicate_long_name_gets_unique_slug(self) -> None:
        """Повторное длинное название получает уникальный slug в пределах лимита."""
        long_name = (
            "Очень длинное название теннисного клуба для проверки переполнения slug"
        )
        first = CourtAdminForm(data=self._base_data(name=long_name))
        self.assertTrue(first.is_valid(), first.errors)
        Court.objects.create(
            name=long_name,
            slug=first.cleaned_data["slug"],
            city="Сочи",
            address="ул. 1",
        )

        second = CourtAdminForm(data=self._base_data(name=long_name))
        self.assertTrue(second.is_valid(), second.errors)
        self.assertNotEqual(second.cleaned_data["slug"], first.cleaned_data["slug"])
        self.assertLessEqual(len(second.cleaned_data["slug"]), 50)


class CourtApplicationApproveSlugTestCase(TestCase):
    """Одобрение заявки с длинным названием не переполняет slug корта."""

    def test_approve_long_name_creates_valid_slug(self) -> None:
        long_name = (
            "Очень длинное название теннисного клуба для проверки переполнения slug"
        )
        application = CourtApplication.objects.create(
            applicant_name="Заявитель",
            applicant_email="applicant@test.local",
            name=long_name,
            city="Сочи",
            address="Курортный проспект, 45",
        )

        court = application.approve_and_create_court()

        self.assertLessEqual(len(court.slug), 50)
        self.assertEqual(application.court_id, court.pk)
