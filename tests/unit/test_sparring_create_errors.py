"""Ошибки формы заявки на спарринг остаются понятными и конкретными."""

from django.test import SimpleTestCase

from apps.sparring.forms import (
    DoublesMatchRequestForm,
    SparringRequestForm,
    sparring_form_error_messages,
)


class SparringCreateErrorMessageTest(SimpleTestCase):
    """Пустые обязательные поля называют, что именно заполнить."""

    def test_singles_names_city_and_description(self) -> None:
        form = SparringRequestForm(
            data={
                "city": "  ",
                "description": "",
                "preferred_gender": "open",
                "sport": "tennis",
            }
        )
        self.assertFalse(form.is_valid())
        messages = sparring_form_error_messages(form)
        self.assertIn("Укажите населённый пункт.", messages)
        self.assertIn("Напишите описание: когда и где хотите играть.", messages)

    def test_singles_without_sport_still_saves_as_tennis(self) -> None:
        form = SparringRequestForm(
            data={
                "city": "Сочи",
                "description": "Вечером на выходных",
                "preferred_gender": "open",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["sport"], "tennis")

    def test_age_order_message_stays_in_russian(self) -> None:
        form = SparringRequestForm(
            data={
                "city": "Сочи",
                "description": "Вечером",
                "preferred_gender": "open",
                "desired_partner_age_min": "40",
                "desired_partner_age_max": "20",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Минимальный возраст не может быть больше максимального.",
            sparring_form_error_messages(form),
        )

    def test_sport_all_becomes_tennis(self) -> None:
        form = SparringRequestForm(
            data={
                "city": "Сочи",
                "description": "Вечером",
                "preferred_gender": "open",
                "sport": "all",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["sport"], "tennis")

    def test_doubles_empty_form_is_valid(self) -> None:
        form = DoublesMatchRequestForm(data={})
        self.assertTrue(form.is_valid(), form.errors)
