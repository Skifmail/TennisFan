"""Автотесты Tennison."""

from django.test import TestCase

from apps.users.models import Player, User


class DisplayNameFormatTestCase(TestCase):
    """Единый формат отображения имён: Имя Фамилия."""

    def test_user_and_player_display_name(self) -> None:
        user = User.objects.create_user(
            email="name@test.local",
            password="x",
            first_name="Кристина",
            last_name="Козубова",
        )
        player = Player.objects.create(user=user)

        self.assertEqual(user.get_display_name(), "Кристина Козубова")
        self.assertEqual(player.get_display_name(), "Кристина Козубова")
        self.assertEqual(str(player), "Кристина Козубова")

    def test_user_admin_label_includes_name_and_email(self) -> None:
        from apps.users.display import format_user_admin_label

        user = User.objects.create_user(
            email="admin-label@test.local",
            password="x",
            first_name="Иван",
            last_name="Петров",
        )
        nameless = User.objects.create_user(
            email="no-name@test.local",
            password="x",
        )

        self.assertEqual(
            format_user_admin_label(user),
            "Иван Петров — admin-label@test.local",
        )
        self.assertEqual(format_user_admin_label(nameless), "no-name@test.local")
