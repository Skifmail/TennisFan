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
