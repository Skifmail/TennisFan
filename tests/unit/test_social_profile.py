"""Проверка нормализации и безопасной загрузки данных социальных профилей."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings
from PIL import Image

from apps.users.social_avatar import (
    MAX_DOWNLOAD_BYTES,
    download_avatar,
    safe_avatar_url,
)
from apps.users.social_oauth import fetch_identity, get_provider, normalize_birth_date

PROVIDERS = {
    key: {
        "enabled": True,
        "client_id": "app",
        "client_secret": "secret",
        "redirect_uri": "https://testserver/callback/",
    }
    for key in ("yandex", "vk")
}


@override_settings(SOCIAL_AUTH_PROVIDERS=PROVIDERS)
class SocialProfileParsingTests(SimpleTestCase):
    """Оба провайдера передают общую структуру профиля без выдуманных значений."""

    def test_yandex_extracts_all_available_fields(self) -> None:
        profile = {
            "id": "123",
            "first_name": "Иван",
            "last_name": "Тестов",
            "default_email": "test@example.com",
            "default_phone": {"number": "+79991234567"},
            "default_avatar_id": "12345/abc-def",
            "is_avatar_empty": False,
            "birthday": "1990-05-24",
            "sex": "male",
        }
        with patch(
            "apps.users.social_oauth._request_json",
            side_effect=[{"access_token": "token"}, profile],
        ):
            result = fetch_identity(
                get_provider("yandex"), code="code", verifier="verifier", state="state"
            )
        self.assertEqual(result["phone"], "+79991234567")
        self.assertEqual(result["birth_date"], "1990-05-24")
        self.assertEqual(result["gender"], "male")
        self.assertEqual(
            result["avatar_url"],
            "https://avatars.yandex.net/get-yapic/12345/abc-def/islands-200",
        )

    def test_vk_extracts_birthday_gender_and_avatar(self) -> None:
        for sex, gender in ((1, "female"), (2, "male"), (0, "")):
            with patch(
                "apps.users.social_oauth._request_json",
                side_effect=[
                    {"access_token": "token", "user_id": "123", "state": "state"},
                    {
                        "user": {
                            "user_id": "123",
                            "birthday": "24.05.1990",
                            "sex": sex,
                            "avatar": "https://sun9.userapi.com/photo.jpg",
                            "phone": "79991234567",
                        }
                    },
                ],
            ):
                result = fetch_identity(
                    get_provider("vk"),
                    code="code",
                    verifier="verifier",
                    state="state",
                    device_id="device",
                )
            self.assertEqual(result["birth_date"], "1990-05-24")
            self.assertEqual(result["gender"], gender)
            self.assertEqual(result["avatar_url"], "https://sun9.userapi.com/photo.jpg")

    def test_missing_or_incomplete_profile_is_allowed(self) -> None:
        with patch(
            "apps.users.social_oauth._request_json",
            side_effect=[
                {"access_token": "token"},
                {
                    "id": "123",
                    "birthday": "0000-05-24",
                    "sex": None,
                    "default_phone": None,
                    "default_avatar_id": "0",
                    "is_avatar_empty": True,
                },
            ],
        ):
            result = fetch_identity(
                get_provider("yandex"), code="code", verifier="verifier", state="state"
            )
        for key in ("phone", "birth_date", "gender", "avatar_url"):
            self.assertEqual(result[key], "")

    def test_invalid_incomplete_and_future_dates_are_empty(self) -> None:
        for value in (
            None,
            "",
            "24.05",
            "0000-05-24",
            "31.02.1990",
            "2999-01-01",
            "not a date",
        ):
            self.assertEqual(normalize_birth_date(value), "")

    def test_yandex_requests_optional_profile_permissions(self) -> None:
        self.assertEqual(
            set(get_provider("yandex").optional_scope.split()),
            {"login:default_phone", "login:avatar", "login:birthday"},
        )


class SocialAvatarTests(SimpleTestCase):
    """Разрешены лишь ограниченные по размеру изображения доверенных CDN."""

    def photo(self) -> bytes:
        """Создать маленькое валидное фото для проверки импорта."""
        output = io.BytesIO()
        Image.new("RGB", (16, 16), "green").save(output, "PNG")
        return output.getvalue()

    def response(
        self, data: bytes, status: int = 200, headers: dict[str, str] | None = None
    ) -> MagicMock:
        """Собрать потоковый HTTP-ответ."""
        response = MagicMock(status_code=status, headers=headers or {})
        response.iter_content.return_value = [data]
        response.__enter__.return_value = response
        return response

    def test_rejects_private_and_untrusted_avatar_urls_without_request(self) -> None:
        for url in (
            "http://sun9.userapi.com/a.jpg",
            "https://127.0.0.1/a.jpg",
            "https://169.254.169.254/",
            "https://userapi.com.evil.test/a.jpg",
            "https://evil.test@userapi.com/a.jpg",
            "https://userapi.com:8443/a.jpg",
            "file:///etc/passwd",
        ):
            with patch("apps.users.social_avatar.requests.get") as request:
                self.assertEqual(safe_avatar_url("vk", url), "")
                self.assertIsNone(download_avatar("vk", url))
                request.assert_not_called()
        self.assertEqual(
            safe_avatar_url("yandex", "https://sun9.userapi.com/a.jpg"), ""
        )

    def test_download_decodes_and_saves_jpeg(self) -> None:
        with patch(
            "apps.users.social_avatar.requests.get",
            return_value=self.response(self.photo()),
        ) as request:
            photo = download_avatar("vk", "https://sun9.userapi.com/a.jpg")
        self.assertIsNotNone(photo)
        assert photo is not None
        self.assertEqual(Image.open(photo).format, "JPEG")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_invalid_oversize_and_redirect_responses_are_rejected(self) -> None:
        for response in (
            self.response(b"html"),
            self.response(self.photo(), status=302),
            self.response(b"", headers={"Content-Length": str(MAX_DOWNLOAD_BYTES + 1)}),
            self.response(b"x" * (MAX_DOWNLOAD_BYTES + 1)),
        ):
            with patch("apps.users.social_avatar.requests.get", return_value=response):
                self.assertIsNone(
                    download_avatar("vk", "https://sun9.userapi.com/a.jpg")
                )
        with patch(
            "apps.users.social_avatar.requests.get", side_effect=requests.Timeout
        ):
            self.assertIsNone(download_avatar("vk", "https://sun9.userapi.com/a.jpg"))
