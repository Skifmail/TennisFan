"""Канонические покрытия корта: чекбоксы вместо свободного текста."""

from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.courts.admin import CourtAdmin
from apps.courts.forms import CourtApplicationForm
from apps.courts.models import Court
from apps.courts.surfaces import (
    CourtSurface,
    compose_surface_display,
    parse_split_aggregated,
    parse_surface_text,
)


class ParseSurfaceTextTestCase(SimpleTestCase):
    """Свободный текст покрытия сводится к фиксированным кодам."""

    def test_maps_hard_and_clay_variants(self) -> None:
        self.assertEqual(parse_surface_text("Хард"), [CourtSurface.HARD])
        self.assertEqual(parse_surface_text("хард"), [CourtSurface.HARD])
        self.assertEqual(parse_surface_text("Грунт"), [CourtSurface.CLAY])
        self.assertEqual(parse_surface_text("грунт"), [CourtSurface.CLAY])

    def test_maps_combined_and_typos(self) -> None:
        self.assertEqual(
            parse_surface_text("Хард, грунт"),
            [CourtSurface.HARD, CourtSurface.CLAY],
        )
        self.assertEqual(
            parse_surface_text("хард, грун"),
            [CourtSurface.HARD, CourtSurface.CLAY],
        )

    def test_maps_indoor_prefix_and_grass_to_other(self) -> None:
        self.assertEqual(
            parse_surface_text("Крытые: Хард, грунт, трава"),
            [CourtSurface.HARD, CourtSurface.CLAY, CourtSurface.OTHER],
        )
        self.assertEqual(
            parse_surface_text("Линолеум спортивный"),
            [CourtSurface.OTHER],
        )
        self.assertEqual(parse_surface_text("Терафлекс"), [CourtSurface.TERAFLEX])

    def test_empty_text_returns_empty_list(self) -> None:
        self.assertEqual(parse_surface_text(""), [])
        self.assertEqual(parse_surface_text("   "), [])

    def test_split_aggregated_indoor_and_outdoor(self) -> None:
        indoor, outdoor = parse_split_aggregated(
            "Крытые: Хард, грунт; Открытые: Терафлекс"
        )
        self.assertEqual(indoor, [CourtSurface.HARD, CourtSurface.CLAY])
        self.assertEqual(outdoor, [CourtSurface.TERAFLEX])


class ComposeSurfaceDisplayTestCase(SimpleTestCase):
    """Витрина показывает человекочитаемые названия, не сырые коды."""

    def test_joins_indoor_and_outdoor_labels(self) -> None:
        text = compose_surface_display(
            is_indoor=True,
            indoor_surfaces=[CourtSurface.HARD, CourtSurface.CLAY],
            is_outdoor=True,
            outdoor_surfaces=[CourtSurface.TERAFLEX],
        )
        self.assertEqual(text, "Крытые: Хард, Грунт; Открытые: Терафлекс")

    def test_compact_when_only_one_format(self) -> None:
        text = compose_surface_display(
            is_indoor=True,
            indoor_surfaces=[CourtSurface.HARD],
            is_outdoor=False,
            outdoor_surfaces=[],
        )
        self.assertEqual(text, "Хард")


class CourtSurfaceModelTestCase(TestCase):
    """На корте хранятся списки канонических покрытий."""

    def test_save_syncs_display_from_checkbox_values(self) -> None:
        court = Court.objects.create(
            name="Спортклуб",
            slug="sportclub-surfaces",
            city="Москва",
            address="ул. Тестовая, 1",
            is_indoor=True,
            is_outdoor=True,
            indoor_surfaces=[CourtSurface.HARD],
            outdoor_surfaces=[CourtSurface.CLAY],
        )
        self.assertEqual(court.surface, "Крытые: Хард; Открытые: Грунт")

    def test_choices_are_the_four_canonical_surfaces(self) -> None:
        self.assertEqual(
            [choice.label for choice in CourtSurface],
            ["Хард", "Грунт", "Терафлекс", "Другое"],
        )


class CourtSurfaceAdminTestCase(SimpleTestCase):
    """В админке покрытия задаются чекбоксами, не свободным текстом."""

    def test_characteristics_use_surface_lists(self) -> None:
        admin = CourtAdmin(Court, AdminSite())
        feature_fields = next(
            fields
            for title, opts in admin.fieldsets
            if title == "Характеристики"
            for fields in [opts["fields"]]
        )
        self.assertIn("indoor_surfaces", feature_fields)
        self.assertIn("outdoor_surfaces", feature_fields)
        self.assertNotIn("indoor_surface", feature_fields)
        self.assertNotIn("outdoor_surface", feature_fields)
        self.assertNotIn("surface", feature_fields)


class CourtApplicationSurfaceFormTestCase(TestCase):
    """Заявка на корт принимает несколько покрытий чекбоксами."""

    def _base_data(self) -> dict[str, object]:
        return {
            "applicant_name": "Иван Иванов",
            "applicant_email": "owner@example.com",
            "name": "Новый клуб",
            "city": "Москва",
            "address": "ул. Новая, 1",
            "courts_count": 2,
            "has_lighting": True,
            "is_indoor": True,
            "is_outdoor": True,
            "agree_legal": True,
        }

    def test_saves_multiple_surface_checkboxes(self) -> None:
        data = self._base_data()
        data["indoor_surfaces"] = [CourtSurface.HARD, CourtSurface.CLAY]
        data["outdoor_surfaces"] = [CourtSurface.TERAFLEX]
        form = CourtApplicationForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        app = form.save()
        self.assertEqual(app.indoor_surfaces, [CourtSurface.HARD, CourtSurface.CLAY])
        self.assertEqual(app.outdoor_surfaces, [CourtSurface.TERAFLEX])
        self.assertEqual(app.surface, "Крытые: Хард, Грунт; Открытые: Терафлекс")

    def test_requires_indoor_surface_when_indoor_selected(self) -> None:
        data = self._base_data()
        data["is_outdoor"] = False
        form = CourtApplicationForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("indoor_surfaces", form.errors)


class CourtListSurfaceFilterTestCase(TestCase):
    """В каталоге кортов фильтр покрытия — фиксированные чекбоксы."""

    def setUp(self) -> None:
        Court.objects.create(
            name="Хард-клуб",
            slug="hard-club",
            city="Москва",
            address="ул. А, 1",
            is_indoor=True,
            indoor_surfaces=[CourtSurface.HARD],
            is_active=True,
        )
        Court.objects.create(
            name="Грунт-клуб",
            slug="clay-club",
            city="Москва",
            address="ул. Б, 1",
            is_outdoor=True,
            outdoor_surfaces=[CourtSurface.CLAY],
            is_active=True,
        )
        Court.objects.create(
            name="Смешанный клуб",
            slug="mixed-club",
            city="Москва",
            address="ул. В, 1",
            is_indoor=True,
            is_outdoor=True,
            indoor_surfaces=[CourtSurface.HARD],
            outdoor_surfaces=[CourtSurface.TERAFLEX],
            is_active=True,
        )

    def test_list_exposes_canonical_checkboxes_not_free_text_options(self) -> None:
        response = self.client.get(reverse("court_list"), secure=True)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertContains(response, 'name="surface"')
        self.assertContains(response, 'value="hard"')
        self.assertContains(response, 'value="clay"')
        self.assertContains(response, 'value="teraflex"')
        self.assertContains(response, 'value="other"')
        self.assertNotContains(response, "Крытые: Хард, грунт")
        self.assertIn("Хард", html)
        self.assertIn("Грунт", html)
        self.assertIn("Терафлекс", html)
        self.assertIn("Другое", html)

    def test_multiple_checkboxes_match_any_selected_surface(self) -> None:
        response = self.client.get(
            reverse("court_list"),
            {"surface": [CourtSurface.CLAY, CourtSurface.TERAFLEX]},
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Грунт-клуб")
        self.assertContains(response, "Смешанный клуб")
        self.assertNotContains(response, "Хард-клуб")
