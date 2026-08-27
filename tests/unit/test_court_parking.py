"""Опции парковки корта: одна галочка «Автомобильная парковка»."""

import importlib

from django.apps import apps
from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase, TestCase

from apps.courts.admin import CourtAdmin
from apps.courts.models import Court

_working_hours_migration = importlib.import_module(
    "apps.courts.migrations.0016_court_working_hours"
)


class CourtParkingModelTestCase(TestCase):
    """Модель корта хранит одну булеву опцию парковки."""

    def test_has_parking_verbose_name(self) -> None:
        field = Court._meta.get_field("has_parking")
        self.assertEqual(field.verbose_name, "Автомобильная парковка")

    def test_has_parking_defaults_to_false(self) -> None:
        court = Court.objects.create(
            name="Корт без парковки",
            slug="court-no-parking",
            city="Москва",
            address="ул. Тестовая, 1",
            surface="хард",
        )
        self.assertFalse(court.has_parking)

    def test_legacy_parking_fields_removed(self) -> None:
        field_names = {field.name for field in Court._meta.get_fields()}
        self.assertNotIn("parking_on_site", field_names)
        self.assertNotIn("parking_nearby", field_names)


class CourtParkingAdminTestCase(SimpleTestCase):
    """В админке при создании корта одна опция парковки."""

    def test_admin_features_fieldset_has_single_parking_option(self) -> None:
        admin = CourtAdmin(Court, AdminSite())
        feature_fields = next(
            fields
            for title, opts in admin.fieldsets
            if title == "Особенности"
            for fields in [opts["fields"]]
        )
        self.assertIn("has_parking", feature_fields)
        self.assertNotIn("parking_on_site", feature_fields)
        self.assertNotIn("parking_nearby", feature_fields)
        self.assertEqual(feature_fields.count("has_parking"), 1)


class CourtWorkingHoursAdminTestCase(SimpleTestCase):
    """В админке время работы задаётся рядом с телефоном."""

    def test_admin_contacts_include_working_hours_after_phone(self) -> None:
        admin = CourtAdmin(Court, AdminSite())
        contact_fields = next(
            fields
            for title, opts in admin.fieldsets
            if title == "Контакты"
            for fields in [opts["fields"]]
        )
        self.assertIn("working_hours", contact_fields)
        self.assertLess(
            contact_fields.index("phone"),
            contact_fields.index("working_hours"),
        )

    def test_working_hours_verbose_name(self) -> None:
        field = Court._meta.get_field("working_hours")
        self.assertEqual(field.verbose_name, "Время работы")


class CourtWorkingHoursMigrationTestCase(TestCase):
    """Перенос «Время работы» из описания в отдельное поле."""

    def test_copies_hours_only_description(self) -> None:
        court = Court.objects.create(
            name="Корт с часами в описании",
            slug="court-hours-in-description",
            city="Москва",
            address="ул. Тестовая, 1",
            surface="хард",
            description="Время работы: 7:00 до 23:00",
        )

        _working_hours_migration.copy_working_hours_from_description(apps, None)
        court.refresh_from_db()

        self.assertEqual(court.working_hours, "7:00 до 23:00")
        self.assertEqual(court.description, "")

    def test_leaves_mixed_description_untouched(self) -> None:
        original = "Большой клуб. Время работы: 7:00 до 23:00"
        court = Court.objects.create(
            name="Корт с обычным описанием",
            slug="court-mixed-description",
            city="Москва",
            address="ул. Тестовая, 1",
            surface="хард",
            description=original,
        )

        _working_hours_migration.copy_working_hours_from_description(apps, None)
        court.refresh_from_db()

        self.assertEqual(court.working_hours, "")
        self.assertEqual(court.description, original)

    def test_reverse_restores_hours_into_empty_description(self) -> None:
        court = Court.objects.create(
            name="Корт с полем часов",
            slug="court-hours-field",
            city="Москва",
            address="ул. Тестовая, 1",
            surface="хард",
            working_hours="8:00 до 22:00",
            description="",
        )

        _working_hours_migration.restore_working_hours_into_description(apps, None)
        court.refresh_from_db()

        self.assertEqual(court.description, "Время работы: 8:00 до 22:00")
