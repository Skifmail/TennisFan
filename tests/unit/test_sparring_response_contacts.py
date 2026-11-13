"""Контакты отклика: нормализация ссылок и выбор рабочего способа связи."""

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from apps.sparring.models import SparringRequest, SparringResponse
from apps.users.models import Player, User


class SparringResponseContactLinksTests(SimpleTestCase):
    """Контакты собираются без запросов к базе и не создают пустых ссылок."""

    def setUp(self) -> None:
        """Подготовить несохранённые модели с контактами игрока."""
        self.player = Player(
            user=User(
                first_name="Леонид", last_name="Ермолаев", phone="8 (999) 123-45-67"
            ),
            telegram=" @leonid ",
            whatsapp="8 (999) 765-43-21",
            max_contact="https://max.ru/u/leonid",
        )
        self.response = SparringResponse(
            respondent=self.player, contact_method="whatsapp"
        )

    def test_all_contact_links_are_normalized(self) -> None:
        """Telegram, WhatsApp, MAX и телефон сохраняют читаемые значения и URL."""
        links = {link["key"]: link for link in self.response.contact_links()}

        self.assertEqual(links["telegram"]["value"], "@leonid")
        self.assertEqual(links["telegram"]["url"], "https://t.me/leonid")
        self.assertEqual(links["whatsapp"]["url"], "https://wa.me/79997654321")
        self.assertEqual(links["max"]["url"], "https://max.ru/u/leonid")
        self.assertEqual(links["phone"]["value"], "8 (999) 123-45-67")
        self.assertEqual(links["phone"]["url"], "tel:+79991234567")
        self.assertFalse(links["phone"]["external"])
        primary = self.response.primary_contact()
        assert primary is not None
        self.assertEqual(primary["key"], "whatsapp")

    def test_request_author_uses_the_same_contact_normalization_as_respondent(
        self,
    ) -> None:
        """Контакты автора и откликнувшегося имеют одинаковые URL, порядок и подписи."""
        request = SparringRequest(player=self.player)

        self.assertEqual(request.contact_links(), self.response.contact_links())

    def test_missing_selected_contact_falls_back_to_available_link(self) -> None:
        """Удалённый номер WhatsApp заменяется рабочей ссылкой на Telegram."""
        self.player.whatsapp = ""

        primary = self.response.primary_contact()

        assert primary is not None
        self.assertEqual(primary["key"], "telegram")

    def test_messenger_buttons_show_labels_without_contact_values(self) -> None:
        """Кнопки мессенджеров сохраняют адрес перехода, но не показывают ники."""
        for contact in self.response.contact_links():
            if contact["key"] == "phone":
                continue
            with self.subTest(messenger=contact["key"]):
                html = render_to_string(
                    "sparring/_response_contact.html", {"link": contact}
                )
                self.assertIn(f'href="{contact["url"]}"', html)
                self.assertIn(f'>{contact["label"]}</span>', html)
                self.assertNotIn('class="sparring-contact__value"', html)
                self.assertIn(f'sparring-contact--{contact["key"]}', html)

    def test_phone_button_keeps_number_and_requires_confirmation(self) -> None:
        """Кнопка звонка показывает номер и помечена для подтверждения перехода."""
        contact = next(
            link for link in self.response.contact_links() if link["key"] == "phone"
        )

        html = render_to_string("sparring/_response_contact.html", {"link": contact})

        self.assertIn('href="tel:+79991234567"', html)
        self.assertIn(">Позвонить</span>", html)
        self.assertIn("8 (999) 123-45-67", html)
        self.assertIn("data-confirm-call", html)
        self.assertNotIn(">Телефон</span>", html)

    def test_shared_chat_button_preserves_default_label(self) -> None:
        """Другие карточки и профиль сохраняют прежнюю подпись «Написать»."""
        self.player.pk = 123

        html = render_to_string(
            "includes/direct_message_button.html", {"target_player": self.player}
        )

        self.assertIn(">Написать</button>", html)
        self.assertIn(f'aria-label="Написать игроку {self.player}"', html)
        self.assertNotIn("Написать в чат", html)

    def test_shared_chat_button_accepts_sparring_label(self) -> None:
        """Отклики задают явную подпись личного чата без изменения маршрута."""
        self.player.pk = 123

        html = render_to_string(
            "includes/direct_message_button.html",
            {"target_player": self.player, "button_label": "Написать в чат"},
        )

        self.assertIn(">Написать в чат</button>", html)
        self.assertIn(f'aria-label="Написать в чат игроку {self.player}"', html)
        self.assertIn('method="post"', html)

    def test_max_without_url_is_disabled_without_exposing_its_value(self) -> None:
        """MAX без ссылки не выдаётся за рабочую кнопку и не показывает номер."""
        self.player.max_contact = "8 (999) 111-22-33"
        self.response.contact_method = "max"
        links = {link["key"]: link for link in self.response.contact_links()}
        contact = links["max"]

        self.assertEqual(contact["value"], "+79991112233")
        self.assertEqual(contact["url"], "")
        primary = self.response.primary_contact()
        assert primary is not None
        self.assertEqual(primary["key"], "telegram")
        html = render_to_string("sparring/_response_contact.html", {"link": contact})
        self.assertIn("disabled", html)
        self.assertIn("Нет ссылки", html)
        self.assertNotIn("+79991112233", html)
        self.assertNotIn("<a ", html)

    def test_no_contacts_returns_no_primary_action(self) -> None:
        """Пустой профиль не порождает контакты или кнопки с пустым адресом."""
        self.player.telegram = ""
        self.player.whatsapp = ""
        self.player.max_contact = ""
        self.player.user.phone = ""

        self.assertEqual(self.response.contact_links(), [])
        self.assertIsNone(self.response.primary_contact())

    def test_max_phone_alone_has_no_primary_link(self) -> None:
        """Один номер MAX не выдаётся за несуществующую ссылку на чат."""
        self.player.telegram = ""
        self.player.whatsapp = ""
        self.player.max_contact = "+79991112233"
        self.player.user.phone = ""

        self.assertEqual(len(self.response.contact_links()), 1)
        self.assertIsNone(self.response.primary_contact())
