"""Публичный каталог сохраняет сведения, навигацию и понятные состояния поиска."""

from pathlib import Path

from django.db import connection
from django.test import TestCase
from django.urls import reverse

from apps.training.models import Training
from tests.support.training_catalog import make_catalog_training


class TrainingCatalogTests(TestCase):
    """Приёмка содержимого обновлённой страницы без изменения заявок и географии."""

    def setUp(self) -> None:
        """Создать опубликованную тренировку со всеми доступными сведениями."""
        self.training = make_catalog_training()

    def test_card_keeps_all_information_and_adds_duration(self) -> None:
        """Карточка сохраняет все уровни, форматы, города, цены и тренера."""
        response = self.client.get(reverse("training_list"), secure=True)
        self.assertEqual(response.status_code, 200)
        assert self.training.coach is not None
        for text in (
            self.training.title,
            self.training.city,
            self.training.short_description,
            self.training.coach.name,
            self.training.price_display,
            self.training.court_price_display,
            *self.training.sports_display,
            *self.training.training_types_display,
            *self.training.skill_levels_display,
            "90 мин",
            "Подробнее",
            "Найдено: <strong>1</strong>",
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertContains(
            response,
            f'href="{reverse("training_detail", kwargs={"slug": self.training.slug})}"',
        )
        html = response.content.decode()
        self.assertLess(
            html.index('class="training-offer__title"'),
            html.index('class="training-offer__facts"'),
        )

    def test_form_applies_explicitly_and_keeps_selected_city(self) -> None:
        """Поля не отправляются неожиданно; GET-форма работает и без JavaScript."""
        response = self.client.get(
            reverse("training_list"), {"city": "Раменское", "sport": "all"}, secure=True
        )
        self.assertContains(response, 'value="Раменское"')
        self.assertContains(response, 'value="all" selected')
        self.assertContains(response, 'type="submit"')
        self.assertContains(response, "Показать тренировки")
        self.assertContains(response, "Сбросить фильтры")
        self.assertContains(response, "Найдено: <strong>1</strong>")
        self.assertNotContains(response, 'onchange="this.form.submit()"')

    def test_empty_filter_has_recovery_actions(self) -> None:
        """Пустая выдача поясняет результат и предлагает сброс и профили тренеров."""
        response = self.client.get(
            reverse("training_list"), {"city": "Казань"}, secure=True
        )
        self.assertContains(response, "По этим условиям ничего не найдено")
        self.assertContains(response, "Найдено: <strong>0</strong>")
        self.assertContains(response, "Все тренировки")
        self.assertContains(response, f'href="{reverse("coach_list")}"')
        self.assertNotContains(response, 'class="training-offer"')

    def test_combined_filters_return_only_matching_published_offer(self) -> None:
        """Город, спорт, формат и уровень применяются одновременно на PostgreSQL."""
        if connection.vendor != "postgresql":
            self.skipTest("JSON contains требует рабочего PostgreSQL")
        Training.objects.create(
            title="Другая тренировка",
            slug="catalog-other-format",
            city="Раменское",
            sport="tennis",
            type_prices={"individual": 2500},
            skill_levels=["advanced"],
        )
        Training.objects.create(
            title="Неопубликованная тренировка",
            slug="catalog-unpublished",
            city="Раменское",
            type_prices={"group": 1500},
            skill_levels=["novice"],
            is_active=False,
        )
        response = self.client.get(
            reverse("training_list"),
            {
                "city": "Раменское",
                "sport": "tennis",
                "type": "group",
                "level": "novice",
            },
            secure=True,
        )
        self.assertContains(response, self.training.title)
        self.assertContains(response, "Найдено: <strong>1</strong>")
        self.assertContains(response, 'value="group" selected')
        self.assertContains(response, 'value="novice" selected')
        self.assertNotContains(response, "Другая тренировка")
        self.assertNotContains(response, "Неопубликованная тренировка")

    def test_missing_price_or_coach_does_not_hide_court_fee(self) -> None:
        """Предложение без цены и тренера не теряет сведения об отдельной аренде."""
        Training.objects.all().update(
            price_min=None, price_max=None, coach=None, short_description=""
        )
        response = self.client.get(reverse("training_list"), secure=True)
        self.assertContains(response, "Цена по запросу")
        self.assertContains(response, "+ корт")
        self.assertContains(response, self.training.description)
        self.assertNotContains(response, 'class="training-offer__coach"')

    def test_styles_use_only_platform_color_tokens(self) -> None:
        """Локальная тема не вводит ни новых цветов, ни внешних шрифтов."""
        css = (
            Path(__file__).resolve().parents[2]
            / "static/css/pages/training_catalog.css"
        ).read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b|(?:rgb|hsl|oklch)\(")
        self.assertNotIn("@import", css)
        self.assertIn("var(--color-primary)", css)
        self.assertIn("var(--color-text)", css)
        self.assertIn("prefers-reduced-motion", css)
