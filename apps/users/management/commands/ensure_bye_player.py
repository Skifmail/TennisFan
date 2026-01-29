"""
Восстановить служебного игрока «Свободный круг» (bye) для FAN-турниров.

Запуск: python manage.py ensure_bye_player

Служебный игрок нужен при нечётном числе участников/победителей:
- R1: при 15 участниках — сеяный 1 получает матч «игрок vs Свободный круг» (walkover)
- R2 и далее: при 7 победителях R1 — победитель матча 7 получает bye в R2
- Подвал: при 7 проигравших R1 — один из них получает матч vs Свободный круг (walkover)

Не удаляйте этого игрока — он используется системой автоматически.
"""

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand

from apps.users.models import Player, User

BYE_EMAIL = "bye@tennisfan.local"


class Command(BaseCommand):
    help = "Создать/восстановить служебного игрока «Свободный круг» для FAN-турниров."

    def handle(self, *args, **options):
        user = User.objects.filter(email=BYE_EMAIL).first()
        if user:
            try:
                player = user.player
            except Player.DoesNotExist:
                player = None
            if player and player.is_bye:
                self.stdout.write(self.style.SUCCESS("Служебный игрок «Свободный круг» уже существует."))
                return
            if player:
                player.is_bye = True
                player.save(update_fields=["is_bye"])
                self.stdout.write(self.style.SUCCESS("Игрок восстановлен (is_bye=True)."))
                return
            Player.objects.create(user=user, is_bye=True)
            self.stdout.write(self.style.SUCCESS("Игрок «Свободный круг» создан (User существовал без Player)."))
            return

        user = User.objects.create(
            email=BYE_EMAIL,
            first_name="",
            last_name="",
            is_active=False,
            password=make_password(None),
        )
        Player.objects.create(user=user, is_bye=True)
        self.stdout.write(self.style.SUCCESS("Служебный игрок «Свободный круг» создан."))
