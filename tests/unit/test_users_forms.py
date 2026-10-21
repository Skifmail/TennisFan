"""Юнит-тесты: валидаторы формы регистрации пользователя."""

from decimal import Decimal

from django.test import TestCase

from apps.users.forms import UserRegistrationForm
from tests.support.factories import make_user


class UserRegistrationFormCleanTestCase(TestCase):
    """Нормализация телефона, email и уровня NTRP."""

    def setUp(self) -> None:
        self.existing = make_user(email="taken@test.local", phone="+79990001122")

    def _base_data(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "first_name": "Иван",
            "last_name": "Тестов",
            "email": "new@test.local",
            "phone": "89991234567",
            "city": "Москва",
            "ntrp_level": "3.5",
            "password": "securepass1",
            "password_confirm": "securepass1",
            "agree_legal": True,
        }
        data.update(overrides)
        return data

    def test_phone_normalized_to_plus7(self) -> None:
        form = UserRegistrationForm(data=self._base_data(phone="8 (999) 123-45-67"))
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["phone"], "+79991234567")

    def test_duplicate_email_rejected(self) -> None:
        form = UserRegistrationForm(
            data=self._base_data(email="TAKEN@test.local"),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_duplicate_phone_rejected(self) -> None:
        form = UserRegistrationForm(
            data=self._base_data(phone="+79990001122"),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

    def test_ntrp_out_of_range_rejected(self) -> None:
        form = UserRegistrationForm(data=self._base_data(ntrp_level="8.0"))
        self.assertFalse(form.is_valid())
        self.assertIn("ntrp_level", form.errors)

    def test_valid_ntrp_accepted(self) -> None:
        form = UserRegistrationForm(data=self._base_data(ntrp_level="4.2"))
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["ntrp_level"], Decimal("4.2"))


class UserRegistrationPasswordTestCase(TestCase):
    """Правила пароля одинаковы для браузера и сервера."""

    def _data(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "first_name": "Иван",
            "last_name": "Тестов",
            "email": "password-check@test.local",
            "phone": "9991234567",
            "city": "Москва",
            "ntrp_level": "3.5",
            "password": "StrongPass123",
            "password_confirm": "StrongPass123",
            "agree_legal": True,
        }
        data.update(overrides)
        return data

    def test_nine_character_password_rejected(self) -> None:
        form = UserRegistrationForm(
            data=self._data(password="Abcdef123", password_confirm="Abcdef123")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)
        self.assertIn("10", form.errors["password"][0])

    def test_numeric_password_rejected(self) -> None:
        form = UserRegistrationForm(
            data=self._data(password="1234567890", password_confirm="1234567890")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_password_spaces_are_preserved(self) -> None:
        form = UserRegistrationForm(
            data=self._data(
                password="StrongPass123 ", password_confirm="StrongPass123 "
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["password"], "StrongPass123 ")


class UserRegistrationNtrpTestCase(TestCase):
    """NTRP принимается с запятой и округляется до сотых."""

    def _data(self, ntrp: str) -> dict[str, object]:
        return {
            "first_name": "Иван",
            "last_name": "Тестов",
            "email": "ntrp-check@test.local",
            "phone": "9991234567",
            "city": "Москва",
            "ntrp_level": ntrp,
            "password": "StrongPass123",
            "password_confirm": "StrongPass123",
            "agree_legal": True,
        }

    def test_comma_separator_accepted(self) -> None:
        form = UserRegistrationForm(data=self._data("3,75"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["ntrp_level"], Decimal("3.75"))

    def test_extra_decimals_are_rounded(self) -> None:
        form = UserRegistrationForm(data=self._data("3.755"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["ntrp_level"], Decimal("3.76"))

    def test_comma_out_of_range_rejected(self) -> None:
        form = UserRegistrationForm(data=self._data("7,5"))
        self.assertFalse(form.is_valid())
        self.assertIn("ntrp_level", form.errors)

    def test_empty_ntrp_has_human_message(self) -> None:
        form = UserRegistrationForm(data=self._data(""))
        self.assertFalse(form.is_valid())
        self.assertIn("Укажите уровень силы", form.errors["ntrp_level"][0])


class UserRegistrationPhoneTestCase(TestCase):
    """Телефон нормализуется к +7XXXXXXXXXX."""

    def _data(self, phone: str) -> dict[str, object]:
        return {
            "first_name": "Иван",
            "last_name": "Тестов",
            "email": "phone-check@test.local",
            "phone": phone,
            "city": "Москва",
            "ntrp_level": "3.5",
            "password": "StrongPass123",
            "password_confirm": "StrongPass123",
            "agree_legal": True,
        }

    def test_ten_digits_get_country_code(self) -> None:
        form = UserRegistrationForm(data=self._data("999 123-45-67"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["phone"], "+79991234567")

    def test_eight_prefix_replaced(self) -> None:
        form = UserRegistrationForm(data=self._data("8 999 123-45-67"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["phone"], "+79991234567")

    def test_wrong_country_code_rejected(self) -> None:
        form = UserRegistrationForm(data=self._data("99912345678"))
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)
