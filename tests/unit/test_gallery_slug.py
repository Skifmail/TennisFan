"""Юнит-тесты: уникальность slug галереи и новостей."""

from django.db import IntegrityError
from django.test import TestCase

from apps.content.models import Gallery, News


class GallerySlugUniquenessTestCase(TestCase):
    """Создание второй галереи с тем же названием не должно падать."""

    def test_second_gallery_with_same_title_gets_unique_slug(self) -> None:
        """Вторая галерея с тем же title получает slug с суффиксом, без IntegrityError."""
        Gallery.objects.create(
            title="Многодневный турнир Воскресенск Tennis Fan",
            slug="mnogodnevnyj-turnir-voskresensk-tennis-fan-voskres",
        )
        second = Gallery(
            title="Многодневный турнир Воскресенск Tennis Fan",
            # Как в админке: prepopulated_fields подставляет тот же slug
            slug="mnogodnevnyj-turnir-voskresensk-tennis-fan-voskres",
        )
        second.save()

        self.assertNotEqual(
            second.slug,
            "mnogodnevnyj-turnir-voskresensk-tennis-fan-voskres",
        )
        self.assertTrue(
            second.slug.startswith("mnogodnevnyj-turnir-voskresensk-tennis")
        )
        self.assertLessEqual(len(second.slug), 50)

    def test_blank_slug_auto_generated_unique(self) -> None:
        """Пустой slug генерируется из title и уникализируется при коллизии."""
        Gallery.objects.create(title="Кубок города", slug="кубок-города")
        second = Gallery(title="Кубок города", slug="")
        second.save()

        self.assertEqual(second.slug, "кубок-города-2")

    def test_editing_gallery_keeps_own_slug(self) -> None:
        """При повторном сохранении своей же галереи slug не меняется."""
        gallery = Gallery.objects.create(title="Фото дня", slug="foto-dnya")
        gallery.description = "обновлено"
        gallery.save()

        self.assertEqual(gallery.slug, "foto-dnya")


class NewsSlugUniquenessTestCase(TestCase):
    """Та же логика уникальности для новостей."""

    def test_second_news_with_same_prepopulated_slug_gets_suffix(self) -> None:
        """Вторая новость с тем же prepopulated slug получает суффикс."""
        News.objects.create(
            title="Итоги турнира",
            slug="itogi-turnira",
            content="Текст",
        )
        second = News(
            title="Итоги турнира",
            slug="itogi-turnira",
            content="Другой текст",
        )
        try:
            second.save()
        except IntegrityError:
            self.fail("News.save() не должен падать на дубликате slug")

        self.assertEqual(second.slug, "itogi-turnira-2")
