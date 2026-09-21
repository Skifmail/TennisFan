"""Геокодер кортов не должен оставлять точку в центре Москвы, если есть точный адрес."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from apps.courts.admin import CourtAdmin, _should_geocode_on_save
from apps.courts.forms import CourtAdminForm
from apps.courts.geocoder import geocode_address
from apps.courts.models import Court
from apps.courts.surfaces import CourtSurface


def _geo_member(
    *,
    kind: str,
    pos: str,
    name: str = "объект",
    precision: str = "exact",
) -> dict:
    """Собрать featureMember в формате ответа Yandex Geocoder."""
    return {
        "GeoObject": {
            "name": name,
            "Point": {"pos": pos},
            "metaDataProperty": {
                "GeocoderMetaData": {
                    "kind": kind,
                    "precision": precision,
                    "text": name,
                }
            },
        }
    }


MOSCOW_CENTER_POS = "37.617644 55.755819"
DOLGOPRUDNY_CITY_POS = "37.51423 55.933302"
SALUT_HOUSE_POS = "37.511293 55.935143"


class GeocodeAddressFallbackTestCase(TestCase):
    """Рамка города не должна перекрывать точный дом за её пределами."""

    def test_retries_without_bbox_when_city_box_returns_only_locality(self) -> None:
        """В рамке Москвы часто находится только «Москва» — тогда ищем адрес без rspn."""

        def fake_request(
            geocode_query: str,
            *,
            results: int = 10,
            rspn: int = 0,
            ll: tuple[float, float] | None = None,
            **kwargs: object,
        ) -> list | None:
            if results == 1:
                return [
                    _geo_member(
                        kind="locality",
                        pos=MOSCOW_CENTER_POS,
                        name="Москва",
                    )
                ]
            if rspn:
                return [
                    _geo_member(
                        kind="locality",
                        pos=MOSCOW_CENTER_POS,
                        name="Москва",
                    )
                ]
            return [
                _geo_member(
                    kind="house",
                    pos=SALUT_HOUSE_POS,
                    name="проспект Ракетостроителей, 4",
                    precision="exact",
                )
            ]

        with patch("apps.courts.geocoder._request_yandex", side_effect=fake_request):
            lat, lon = geocode_address(
                "Долгопрудный, московская область, проспект Ракетостроителей, 4",
                api_key="test-key",
                hint_city="Москва",
            )

        self.assertEqual(lat, 55.935143)
        self.assertEqual(lon, 37.511293)

    def test_rejects_house_in_another_region(self) -> None:
        """Дом в другом регионе не должен выигрывать у точки рядом с hint_city."""

        def fake_request(
            geocode_query: str,
            *,
            results: int = 10,
            rspn: int = 0,
            **kwargs: object,
        ) -> list | None:
            if results == 1 or rspn:
                return [
                    _geo_member(
                        kind="locality",
                        pos=MOSCOW_CENTER_POS,
                        name="Москва",
                    )
                ]
            return [
                _geo_member(
                    kind="house",
                    pos="131.885 43.115",
                    name="улица Ленина, 1",
                )
            ]

        with patch("apps.courts.geocoder._request_yandex", side_effect=fake_request):
            lat, lon = geocode_address(
                "ул. Ленина, 1",
                api_key="test-key",
                hint_city="Москва",
            )

        self.assertEqual(lat, 55.755819)
        self.assertEqual(lon, 37.617644)

    def test_keeps_house_from_city_bbox_without_unrestricted_override(self) -> None:
        """Если в рамке города уже есть дом, чужой дом снаружи не подменяем."""
        house_pos = "37.511293 55.935143"

        def fake_request(
            geocode_query: str,
            *,
            results: int = 10,
            rspn: int = 0,
            **kwargs: object,
        ) -> list | None:
            if results == 1:
                return [
                    _geo_member(
                        kind="locality",
                        pos=DOLGOPRUDNY_CITY_POS,
                        name="Долгопрудный",
                    )
                ]
            if rspn:
                return [
                    _geo_member(
                        kind="house",
                        pos=house_pos,
                        name="проспект Ракетостроителей, 4",
                    )
                ]
            return [
                _geo_member(
                    kind="house",
                    pos="30.0 60.0",
                    name="чужой дом",
                )
            ]

        with patch("apps.courts.geocoder._request_yandex", side_effect=fake_request):
            lat, lon = geocode_address(
                "Долгопрудный, проспект Ракетостроителей, 4",
                api_key="test-key",
                hint_city="Долгопрудный",
            )

        self.assertEqual(lat, 55.935143)
        self.assertEqual(lon, 37.511293)


class ShouldGeocodeOnSaveTestCase(TestCase):
    """Автогеокодирование при смене адреса, даже если старые координаты уже есть."""

    def test_geocodes_when_coords_missing(self) -> None:
        court = Court(city="Долгопрудный", address="проспект Ракетостроителей, 4")
        self.assertTrue(_should_geocode_on_save(court, changed_data=[], change=True))

    def test_geocodes_when_address_changed_and_coords_not_edited(self) -> None:
        court = Court(
            city="Долгопрудный",
            address="проспект Ракетостроителей, 4",
            latitude=Decimal("55.755819"),
            longitude=Decimal("37.617644"),
        )
        self.assertTrue(
            _should_geocode_on_save(
                court,
                changed_data=["address"],
                change=True,
            )
        )

    def test_skips_when_user_set_coords_manually(self) -> None:
        court = Court(
            city="Долгопрудный",
            address="проспект Ракетостроителей, 4",
            latitude=Decimal("55.935143"),
            longitude=Decimal("37.511293"),
        )
        self.assertFalse(
            _should_geocode_on_save(
                court,
                changed_data=["address", "latitude"],
                change=True,
            )
        )

    def test_skips_when_address_unchanged_and_coords_exist(self) -> None:
        court = Court(
            city="Долгопрудный",
            address="проспект Ракетостроителей, 4",
            latitude=Decimal("55.935143"),
            longitude=Decimal("37.511293"),
        )
        self.assertFalse(
            _should_geocode_on_save(court, changed_data=["name"], change=True)
        )


class CourtAdminSaveGeocodeTestCase(TestCase):
    """Админка пересчитывает координаты после смены адреса."""

    def _form_data(self, court: Court, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "name": court.name,
            "slug": court.slug,
            "city": court.city,
            "address": court.address,
            "courts_count": court.courts_count,
            "is_indoor": court.is_indoor,
            "indoor_surfaces": [CourtSurface.HARD],
            "is_outdoor": False,
            "is_active": True,
            "latitude": str(court.latitude) if court.latitude is not None else "",
            "longitude": str(court.longitude) if court.longitude is not None else "",
        }
        data.update(overrides)
        return data

    def test_save_model_regeocodes_stale_moscow_center(self) -> None:
        user_model = get_user_model()
        user = user_model.objects.create_superuser(
            email="geo-admin@example.com",
            password="pass",
        )
        court = Court.objects.create(
            name='ФСК "Салют"',
            slug="fsk-salut-test",
            city="Долгопрудный",
            address="старый адрес",
            courts_count=1,
            is_indoor=True,
            indoor_surfaces=[CourtSurface.HARD],
            is_active=True,
            latitude=Decimal("55.755819"),
            longitude=Decimal("37.617644"),
        )
        form = CourtAdminForm(
            data=self._form_data(
                court,
                address="московская область, проспект Ракетостроителей, 4",
            ),
            instance=court,
        )
        self.assertTrue(form.is_valid(), form.errors)

        request = RequestFactory().post("/admin/courts/court/1/change/")
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)

        with patch(
            "apps.courts.admin.geocode_address",
            return_value=(55.935143, 37.511293),
        ):
            CourtAdmin(Court, AdminSite()).save_model(
                request,
                form.save(commit=False),
                form,
                change=True,
            )

        court.refresh_from_db()
        self.assertEqual(float(court.latitude), 55.935143)
        self.assertEqual(float(court.longitude), 37.511293)
        self.assertEqual(
            court.address,
            "московская область, проспект Ракетостроителей, 4",
        )
