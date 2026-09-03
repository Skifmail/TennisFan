"""Типы населённых пунктов РФ: разбор KLADR/Yandex, справочник и автодополнение."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from apps.core.localities import (
    SettlementType,
    format_kladr_region,
    format_locality_label,
    map_kladr_type,
    parse_kladr_row,
    parse_yandex_locality,
    split_typed_name,
    strip_settlement_prefix,
    upsert_locality,
)
from apps.core.models import City
from apps.courts.forms import CourtApplicationForm
from apps.courts.models import Court, CourtApplication
from apps.users.forms import UserRegistrationForm
from apps.users.models import Player


class MapKladrTypeTestCase(TestCase):
    """Аббревиатуры КЛАДР/ФИАС → тип поселения."""

    def test_maps_common_abbreviations(self) -> None:
        cases = {
            "г": SettlementType.CITY,
            "пгт": SettlementType.PGT,
            "рп": SettlementType.PGT,
            "п": SettlementType.POSELOK,
            "с": SettlementType.SELO,
            "д": SettlementType.DEREVNYA,
            "ст-ца": SettlementType.STANITSA,
            "х": SettlementType.KHUTOR,
            "аул": SettlementType.AUL,
        }
        for abbr, expected in cases.items():
            with self.subTest(abbr=abbr):
                self.assertEqual(map_kladr_type(abbr), expected)

    def test_empty_city_column_defaults_to_city(self) -> None:
        self.assertEqual(
            map_kladr_type("", default=SettlementType.CITY),
            SettlementType.CITY,
        )

    def test_unknown_abbreviation_is_other(self) -> None:
        self.assertEqual(map_kladr_type("снт"), SettlementType.OTHER)


class ParseKladrRowTestCase(TestCase):
    """Строка справочника: город из city, иначе поселение из settlement."""

    def test_reads_city_when_settlement_empty(self) -> None:
        row = {
            "city": "Казань",
            "city_type": "г",
            "settlement": "",
            "settlement_type": "",
            "region_type": "респ",
            "region": "Татарстан",
            "geo_lat": "55.7961",
            "geo_lon": "49.1064",
        }
        parsed = parse_kladr_row(row)
        assert parsed is not None
        self.assertEqual(parsed["name"], "Казань")
        self.assertEqual(parsed["settlement_type"], SettlementType.CITY)
        self.assertEqual(parsed["region"], "Республика Татарстан")

    def test_prefers_settlement_over_parent_city(self) -> None:
        row = {
            "city": "Иваново",
            "city_type": "г",
            "settlement": "Листвянка",
            "settlement_type": "пгт",
            "region_type": "обл",
            "region": "Ивановская",
            "geo_lat": "56.9",
            "geo_lon": "41.0",
        }
        parsed = parse_kladr_row(row)
        assert parsed is not None
        self.assertEqual(parsed["name"], "Листвянка")
        self.assertEqual(parsed["settlement_type"], SettlementType.PGT)
        self.assertEqual(parsed["region"], "Ивановская область")

    def test_reads_village_without_city(self) -> None:
        row = {
            "city": "",
            "city_type": "",
            "settlement": "Кимжа",
            "settlement_type": "д",
            "region_type": "обл",
            "region": "Архангельская",
        }
        parsed = parse_kladr_row(row)
        assert parsed is not None
        self.assertEqual(parsed["name"], "Кимжа")
        self.assertEqual(parsed["settlement_type"], SettlementType.DEREVNYA)

    def test_skips_empty_row(self) -> None:
        self.assertIsNone(parse_kladr_row({"city": "", "settlement": ""}))


class FormatLocalityLabelTestCase(TestCase):
    """Подпись для поля ввода и автодополнения."""

    def test_city_is_name_only(self) -> None:
        self.assertEqual(
            format_locality_label("Москва", SettlementType.CITY, "Москва"),
            "Москва",
        )

    def test_village_includes_type_and_region(self) -> None:
        self.assertEqual(
            format_locality_label(
                "Кимжа", SettlementType.DEREVNYA, "Архангельская область"
            ),
            "деревня Кимжа, Архангельская область",
        )

    def test_pgt_uses_short_label(self) -> None:
        self.assertEqual(
            format_locality_label(
                "Листвянка", SettlementType.PGT, "Ивановская область"
            ),
            "пгт Листвянка, Ивановская область",
        )


class SplitTypedNameTestCase(TestCase):
    """Разбор «деревня Кимжа» и нормализация префиксов."""

    def test_splits_yandex_village_name(self) -> None:
        stype, name = split_typed_name("деревня Кимжа")
        self.assertEqual(stype, SettlementType.DEREVNYA)
        self.assertEqual(name, "Кимжа")

    def test_plain_city_stays_city(self) -> None:
        stype, name = split_typed_name("Москва")
        self.assertEqual(stype, SettlementType.CITY)
        self.assertEqual(name, "Москва")

    def test_strip_prefix_groups_typed_and_plain_names(self) -> None:
        self.assertEqual(strip_settlement_prefix("деревня Кимжа"), "кимжа")
        self.assertEqual(strip_settlement_prefix("Кимжа"), "кимжа")
        self.assertEqual(strip_settlement_prefix("пгт Листвянка"), "листвянка")


class FormatKladrRegionTestCase(TestCase):
    """Сборка читаемого региона из type+name КЛАДР."""

    def test_oblast(self) -> None:
        self.assertEqual(
            format_kladr_region("обл", "Архангельская"),
            "Архангельская область",
        )

    def test_republic(self) -> None:
        self.assertEqual(format_kladr_region("Респ", "Адыгея"), "Республика Адыгея")

    def test_federal_city(self) -> None:
        self.assertEqual(format_kladr_region("г", "Москва"), "Москва")


class ParseYandexLocalityTestCase(TestCase):
    """Геообъект Яндекса → населённый пункт."""

    def test_parses_village(self) -> None:
        geo = {
            "name": "деревня Кимжа",
            "description": "Мезенский район, Архангельская область, Россия",
            "Point": {"pos": "44.78 65.57"},
            "metaDataProperty": {
                "GeocoderMetaData": {
                    "kind": "locality",
                    "AddressDetails": {
                        "Country": {
                            "AdministrativeArea": {
                                "AdministrativeAreaName": "Архангельская область",
                            }
                        }
                    },
                }
            },
        }
        parsed = parse_yandex_locality(geo)
        assert parsed is not None
        self.assertEqual(parsed["name"], "Кимжа")
        self.assertEqual(parsed["settlement_type"], SettlementType.DEREVNYA)
        self.assertEqual(parsed["region"], "Архангельская область")
        self.assertEqual(parsed["lat"], 65.57)
        self.assertEqual(parsed["lng"], 44.78)

    def test_skips_non_locality(self) -> None:
        geo = {
            "name": "Тверская улица",
            "metaDataProperty": {"GeocoderMetaData": {"kind": "street"}},
        }
        self.assertIsNone(parse_yandex_locality(geo))


class CityModelLocalityTestCase(TestCase):
    """Справочник допускает одноимённые поселения разных типов и регионов."""

    def test_same_name_different_regions(self) -> None:
        City.objects.create(
            name="Мирный",
            settlement_type=SettlementType.CITY,
            region="Архангельская область",
        )
        City.objects.create(
            name="Мирный",
            settlement_type=SettlementType.CITY,
            region="Республика Саха (Якутия)",
        )
        self.assertEqual(City.objects.filter(name="Мирный").count(), 2)

    def test_suggestion_payload_for_village(self) -> None:
        city, _created = City.objects.get_or_create(
            name="Кимжа",
            settlement_type=SettlementType.DEREVNYA,
            region="Архангельская область",
        )
        payload = city.suggestion_payload()
        self.assertEqual(payload["value"], "деревня Кимжа, Архангельская область")
        self.assertEqual(payload["settlement_type"], SettlementType.DEREVNYA)

    def test_upsert_fills_region_on_legacy_city(self) -> None:
        legacy, _created = City.objects.get_or_create(
            name="Казань",
            defaults={"settlement_type": SettlementType.CITY, "region": ""},
        )
        if legacy.region:
            legacy.region = ""
            legacy.save(update_fields=["region"])
        obj, created = upsert_locality(
            name="Казань",
            settlement_type=SettlementType.CITY,
            region="Республика Татарстан",
        )
        self.assertFalse(created)
        self.assertEqual(obj.pk, legacy.pk)
        obj.refresh_from_db()
        self.assertEqual(obj.region, "Республика Татарстан")


class LoadCitiesFromCsvTestCase(TestCase):
    """Команда загрузки читает и города, и сёла/пгт из KLADR-CSV."""

    def test_loads_settlement_rows(self) -> None:
        buffer = StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "city",
                "city_type",
                "settlement",
                "settlement_type",
                "region_type",
                "region",
                "geo_lat",
                "geo_lon",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "city": "Казань",
                "city_type": "г",
                "settlement": "",
                "settlement_type": "",
                "region_type": "респ",
                "region": "Татарстан",
                "geo_lat": "55.8",
                "geo_lon": "49.1",
            }
        )
        writer.writerow(
            {
                "city": "",
                "city_type": "",
                "settlement": "Тестовосело",
                "settlement_type": "с",
                "region_type": "обл",
                "region": "Рязанская",
                "geo_lat": "54.86",
                "geo_lon": "39.76",
            }
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "places.csv"
            path.write_text(buffer.getvalue(), encoding="utf-8")
            call_command("load_cities_from_csv", path=str(path))

        village = City.objects.get(name="Тестовосело")
        self.assertEqual(village.settlement_type, SettlementType.SELO)
        self.assertEqual(village.region, "Рязанская область")
        self.assertTrue(City.objects.filter(name="Казань").exists())


class ApiCitiesAutocompleteTestCase(TestCase):
    """GET /api/cities/?q= предлагает деревни и пгт, не только города."""

    def setUp(self) -> None:
        self.client = Client()
        # Москва из seed координат; Кимжа/Листвянка — из seed типов поселений.
        City.objects.get_or_create(
            name="Москва",
            defaults={"settlement_type": SettlementType.CITY, "region": "Москва"},
        )
        self.assertTrue(
            City.objects.filter(
                name="Кимжа", settlement_type=SettlementType.DEREVNYA
            ).exists()
        )
        self.assertTrue(
            City.objects.filter(
                name="Листвянка", settlement_type=SettlementType.PGT
            ).exists()
        )

    def test_suggests_village_by_name(self) -> None:
        response = self.client.get(reverse("api_cities"), {"q": "кимж"}, secure=True)
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.json()]
        self.assertTrue(
            any("Кимжа" in label and "деревня" in label for label in labels)
        )

    def test_suggests_pgt(self) -> None:
        response = self.client.get(reverse("api_cities"), {"q": "листвян"}, secure=True)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload)
        self.assertEqual(payload[0]["settlement_type"], SettlementType.PGT)

    def test_yandex_fills_missing_localities(self) -> None:
        yandex_hit = {
            "name": "Старочеркасская",
            "settlement_type": SettlementType.STANITSA,
            "region": "Ростовская область",
            "lat": 47.24,
            "lng": 40.03,
            "label": "станица Старочеркасская, Ростовская область",
        }
        with patch(
            "apps.core.views.fetch_yandex_localities",
            return_value=[yandex_hit],
        ):
            with self.settings(YANDEX_GEOCODER_API_KEY="test-key"):
                response = self.client.get(
                    reverse("api_cities"), {"q": "старочеркас"}, secure=True
                )
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.json()]
        self.assertTrue(any("Старочеркасская" in label for label in labels))
        saved = City.objects.get(name="Старочеркасская")
        self.assertEqual(saved.settlement_type, SettlementType.STANITSA)


class RegistrationAndCourtLocalityLabelsTestCase(TestCase):
    """Регистрация и заявка на корт принимают любой тип поселения."""

    def test_registration_label_is_locality(self) -> None:
        form = UserRegistrationForm()
        self.assertEqual(form.fields["city"].label, "Населённый пункт *")

    def test_registration_accepts_village(self) -> None:
        form = UserRegistrationForm(
            data={
                "first_name": "Иван",
                "last_name": "Тестов",
                "email": "village@test.local",
                "phone": "89991234567",
                "city": "деревня Кимжа, Архангельская область",
                "ntrp_level": "3.5",
                "password": "securepass1",
                "password_confirm": "securepass1",
                "agree_legal": True,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["city"],
            "деревня Кимжа, Архангельская область",
        )

    def test_court_application_label_is_locality(self) -> None:
        field = CourtApplication._meta.get_field("city")
        self.assertEqual(field.verbose_name, "Населённый пункт")
        form = CourtApplicationForm()
        self.assertIn("пгт", form.fields["city"].widget.attrs["placeholder"].lower())

    def test_court_model_label_is_locality(self) -> None:
        self.assertEqual(Court._meta.get_field("city").verbose_name, "Населённый пункт")

    def test_player_model_label_is_locality(self) -> None:
        self.assertEqual(
            Player._meta.get_field("city").verbose_name, "Населённый пункт"
        )
