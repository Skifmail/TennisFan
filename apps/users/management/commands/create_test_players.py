"""
Добавить 15 тестовых игроков: разные города, уровни, сила, имена.

Запуск: python manage.py create_test_players

Игроки создаются с email testplayer_N@test.local и паролем testpass123.
Повторный запуск не создаёт дубликаты (проверка по email).
"""

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand

from apps.users.models import Gender, Player, SkillLevel, User

TEST_PLAYERS = [
    # (first_name, last_name, city, skill_level, ntrp_level, total_points, gender)
    (
        "Дмитрий",
        "Волков",
        "Москва",
        SkillLevel.PROFESSIONAL,
        Decimal("5.5"),
        4800.0,
        Gender.MALE,
    ),
    (
        "Мария",
        "Соколова",
        "Санкт-Петербург",
        SkillLevel.ADVANCED,
        Decimal("5.0"),
        4200.0,
        Gender.FEMALE,
    ),
    (
        "Артём",
        "Козлов",
        "Казань",
        SkillLevel.EXPERIENCED,
        Decimal("4.5"),
        3600.0,
        Gender.MALE,
    ),
    (
        "Анна",
        "Лебедева",
        "Нижний Новгород",
        SkillLevel.EXPERIENCED,
        Decimal("4.0"),
        3200.0,
        Gender.FEMALE,
    ),
    (
        "Игорь",
        "Новиков",
        "Екатеринбург",
        SkillLevel.AMATEUR,
        Decimal("3.5"),
        2800.0,
        Gender.MALE,
    ),
    (
        "Елена",
        "Морозова",
        "Новосибирск",
        SkillLevel.AMATEUR,
        Decimal("3.0"),
        2400.0,
        Gender.FEMALE,
    ),
    (
        "Павел",
        "Федоров",
        "Краснодар",
        SkillLevel.AMATEUR,
        Decimal("2.5"),
        1900.0,
        Gender.MALE,
    ),
    (
        "Ольга",
        "Алексеева",
        "Сочи",
        SkillLevel.NOVICE,
        Decimal("2.5"),
        1700.0,
        Gender.FEMALE,
    ),
    (
        "Сергей",
        "Петров",
        "Ростов-на-Дону",
        SkillLevel.NOVICE,
        Decimal("2.0"),
        1400.0,
        Gender.MALE,
    ),
    (
        "Наталья",
        "Смирнова",
        "Самара",
        SkillLevel.NOVICE,
        Decimal("2.0"),
        1200.0,
        Gender.FEMALE,
    ),
    (
        "Алексей",
        "Кузнецов",
        "Воронеж",
        SkillLevel.EXPERIENCED,
        Decimal("4.0"),
        3100.0,
        Gender.MALE,
    ),
    (
        "Татьяна",
        "Попова",
        "Пермь",
        SkillLevel.AMATEUR,
        Decimal("3.5"),
        2600.0,
        Gender.FEMALE,
    ),
    (
        "Михаил",
        "Васильев",
        "Волгоград",
        SkillLevel.NOVICE,
        Decimal("2.5"),
        1600.0,
        Gender.MALE,
    ),
    (
        "Ирина",
        "Михайлова",
        "Уфа",
        SkillLevel.ADVANCED,
        Decimal("4.5"),
        3800.0,
        Gender.FEMALE,
    ),
    (
        "Андрей",
        "Семёнов",
        "Калининград",
        SkillLevel.PROFESSIONAL,
        Decimal("5.0"),
        4400.0,
        Gender.MALE,
    ),
]

EMAIL_PREFIX = "testplayer"
EMAIL_DOMAIN = "test.local"
DEFAULT_PASSWORD = "testpass123"


class Command(BaseCommand):
    help = "Создать 15 тестовых игроков (разные города, уровни, сила)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Не пропускать существующих по email, попытаться обновить (не перезаписывает пароль).",
        )

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for i, (
            first_name,
            last_name,
            city,
            skill_level,
            ntrp_level,
            total_points,
            gender,
        ) in enumerate(TEST_PLAYERS, 1):
            email = f"{EMAIL_PREFIX}{i}@{EMAIL_DOMAIN}"
            user = User.objects.filter(email=email).first()
            if user:
                if not options["force"]:
                    skipped += 1
                    self.stdout.write(f"  Пропуск: {email} (уже есть).")
                    continue
                try:
                    player = user.player
                except Player.DoesNotExist:
                    player = None
                if player and not player.is_bye:
                    player.city = city
                    player.skill_level = skill_level
                    player.ntrp_level = ntrp_level
                    player.total_points = total_points
                    player.hidden_rating = total_points
                    player.gender = gender
                    player.user.first_name = first_name
                    player.user.last_name = last_name
                    player.user.save(update_fields=["first_name", "last_name"])
                    player.save(
                        update_fields=[
                            "city",
                            "skill_level",
                            "ntrp_level",
                            "total_points",
                            "hidden_rating",
                            "gender",
                        ]
                    )
                    self.stdout.write(
                        f"  Обновлён: {last_name} {first_name} ({email})."
                    )
                continue

            user = User.objects.create(
                email=email,
                first_name=first_name,
                last_name=last_name,
                password=make_password(DEFAULT_PASSWORD),
                is_active=True,
            )
            Player.objects.create(
                user=user,
                city=city,
                skill_level=skill_level,
                ntrp_level=ntrp_level,
                total_points=total_points,
                hidden_rating=total_points,
                gender=gender,
            )
            created += 1
            self.stdout.write(
                f"  Создан: {last_name} {first_name}, {city}, {skill_level}, рейтинг {total_points} ({email})."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Создано: {created}, пропущено: {skipped}. "
                f"Пароль для тестовых: {DEFAULT_PASSWORD}"
            )
        )
