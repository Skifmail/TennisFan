"""Аватары, адресная категория и региональное закрепление каталогов."""

from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from apps.training.catalog_presentation import HIGHEST_CATEGORY_COACH_SLUG
from apps.training.models import Coach, Training
from apps.users.models import Player
from tests.support.factories import make_player


class TrainingCatalogPresentationTests(TestCase):
    """Отличать выбранный профиль от однофамильцев и сохранять фильтры."""

    def setUp(self) -> None:
        """Создать тренеров и несколько предложений с пересекающейся географией."""
        self.player = make_player(email_suffix="catalog-avatar")
        self.player.avatar = "avatars/catalog-player.jpg"
        self.player.city = "Раменское"
        Player.objects.filter(pk=self.player.pk).update(
            avatar=self.player.avatar.name, city=self.player.city
        )
        self.coach = Coach.objects.create(
            user=self.player.user,
            name="Леонид Ермолаев",
            slug=HIGHEST_CATEGORY_COACH_SLUG,
            city="Раменское, Жуковский, Москва",
            photo="coaches/catalog-coach.jpg",
        )
        self.other_coach = Coach.objects.create(
            name="Алексей Тренер",
            slug="catalog-other-coach",
            city="Раменское, Жуковский, Москва",
        )
        self.offer = Training.objects.create(
            title="Тренировка Леонида",
            slug="catalog-leonid",
            coach=self.coach,
            city="Раменское, Жуковский, Москва",
            type_prices={"individual": 3000},
            skill_levels=["novice"],
        )
        self.second_offer = Training.objects.create(
            title="Вторая тренировка Леонида",
            slug="catalog-leonid-second",
            coach=self.coach,
            city=self.offer.city,
        )
        self.other_offer = Training.objects.create(
            title="Более новая тренировка",
            slug="catalog-more-recent",
            coach=self.other_coach,
            city=self.offer.city,
            type_prices={"group": 1500},
            skill_levels=["novice"],
        )

    def test_player_avatar_wins_over_coach_photo(self) -> None:
        """В строке тренера использовать профиль игрока даже при отдельном фото."""
        response = self.client.get(reverse("training_list"), secure=True)
        self.assertContains(response, self.player.avatar.url, count=2)
        self.assertNotContains(response, self.coach.photo.url)
        self.assertContains(response, "training-offer--highest-category", count=2)
        self.assertContains(response, "Тренер высшей категории", count=2)

    def test_avatar_falls_back_to_photo_then_initial(self) -> None:
        """Нет аватара — показать фото; нет ни одного снимка — сохранить инициалы."""
        self.player.avatar = ""
        Player.objects.filter(pk=self.player.pk).update(avatar="")
        response = self.client.get(reverse("training_list"), secure=True)
        self.assertContains(response, self.coach.photo.url, count=2)
        self.coach.photo = ""
        self.coach.save(update_fields=["photo"])
        response = self.client.get(reverse("training_list"), secure=True)
        self.assertContains(response, "training-offer__avatar--placeholder", count=3)
        self.assertNotContains(response, 'src=""')

    def test_unlinked_coach_and_missing_player_are_safe(self) -> None:
        """Связанный пользователь без игрока и тренер без пользователя не ломают выдачу."""
        self.player.delete()
        coach = Coach.objects.select_related("user__player").get(pk=self.coach.pk)
        self.assertEqual(coach.catalog_avatar, coach.photo)
        self.assertIsNone(self.other_coach.catalog_avatar)

    def test_same_name_is_not_highlighted(self) -> None:
        """Имя не определяет категорию: другой профиль не получает рамку и бейдж."""
        self.other_coach.name = self.coach.name
        self.other_coach.save(update_fields=["name"])
        self.assertFalse(self.other_coach.is_highest_category)
        response = self.client.get(reverse("training_list"), secure=True)
        self.assertContains(response, "Тренер высшей категории", count=2)

    def test_city_filters_pin_all_matching_leonid_offers(self) -> None:
        """В двух целевых городах сначала все предложения Леонида, новые выше старых."""
        for city in ("Раменское", "Жуковский", "  рАмЕнСкОе  "):
            with self.subTest(city=city):
                response = self.client.get(
                    reverse("training_list"), {"city": city}, secure=True
                )
                self.assertEqual(
                    [training.pk for training in response.context["trainings"]],
                    [self.second_offer.pk, self.offer.pk, self.other_offer.pk],
                )

    def test_viewer_city_pins_local_offers_without_breaking_sections(self) -> None:
        """В ленте игрока обеих целевых территорий закрепление остаётся в местной секции."""
        remote_offer = Training.objects.create(
            title="Леонид в другом городе",
            slug="catalog-leonid-remote",
            coach=self.coach,
            city="Ростов-на-Дону",
        )
        for city in ("Раменское", "Жуковский"):
            with self.subTest(city=city):
                self.player.city = city
                Player.objects.filter(pk=self.player.pk).update(city=city)
                self.client.force_login(self.player.user)
                response = self.client.get(reverse("training_list"), secure=True)
                entries = response.context["trainings"]
                self.assertEqual(entries[0].pk, self.second_offer.pk)
                self.assertEqual(entries[-1].pk, remote_offer.pk)
                self.assertContains(response, "Рядом с вами", count=1)
                self.assertContains(response, "В других городах", count=1)

    def test_other_cities_and_guest_keep_original_training_order(self) -> None:
        """За пределами целевых городов порядок по новизне не меняется."""
        for params in ({}, {"city": "Москва"}):
            with self.subTest(params=params):
                response = self.client.get(
                    reverse("training_list"), params, secure=True
                )
                self.assertEqual(
                    response.context["trainings"][0].pk, self.other_offer.pk
                )

    def test_pinning_does_not_bypass_active_or_sport_filters(self) -> None:
        """Закреплённые карточки не возвращаются после исключения обычными фильтрами."""
        response = self.client.get(
            reverse("training_list"),
            {"city": "Раменское", "sport": "padel"},
            secure=True,
        )
        self.assertEqual(response.context["trainings"], [])
        self.offer.is_active = False
        self.offer.save(update_fields=["is_active"])
        self.second_offer.is_active = False
        self.second_offer.save(update_fields=["is_active"])
        response = self.client.get(
            reverse("training_list"), {"city": "Раменское"}, secure=True
        )
        self.assertEqual(
            [training.pk for training in response.context["trainings"]],
            [self.other_offer.pk],
        )
        self.assertNotContains(response, "Тренер высшей категории")

    def test_coach_list_pins_global_and_regional_results(self) -> None:
        """Сам тренер первый в общем каталоге и целевых городах; рамка постоянная."""
        self.client.force_login(self.player.user)
        for city in ("", "Раменское", "Жуковский"):
            with self.subTest(city=city):
                response = self.client.get(
                    reverse("coach_list"), {"city": city}, secure=True
                )
                self.assertEqual(response.context["coaches"][0].pk, self.coach.pk)
                self.assertContains(
                    response, "coach-list-card--highest-category", count=1
                )
                self.assertContains(response, "css/pages/coach_catalog.css")
        response = self.client.get(
            reverse("coach_list"), {"city": "Москва"}, secure=True
        )
        self.assertEqual(response.context["coaches"][0].pk, self.other_coach.pk)
        response = self.client.get(
            reverse("coach_list"), {"sport": "padel"}, secure=True
        )
        self.assertEqual(response.context["coaches"], [])

    def test_format_and_level_filters_do_not_reintroduce_pinned_offers(self) -> None:
        """Региональный приоритет не отменяет JSON-фильтры формата и уровня."""
        if connection.vendor != "postgresql":
            self.skipTest("JSON contains требует рабочего PostgreSQL")
        response = self.client.get(
            reverse("training_list"),
            {"city": "Жуковский", "type": "group", "level": "novice"},
            secure=True,
        )
        self.assertEqual(
            [training.pk for training in response.context["trainings"]],
            [self.other_offer.pk],
        )

    def test_card_avatars_need_no_additional_queries(self) -> None:
        """Все профили загружаются одним запросом, без N+1 для аватара и категории."""
        with self.assertNumQueries(1):
            for training in Training.objects.select_related("coach__user__player"):
                render_to_string("training/_training_card.html", {"training": training})
