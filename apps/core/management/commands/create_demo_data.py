"""
Management command to create demo data for TennisFan.
"""

import random
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.content.models import News, Page
from apps.courts.models import Court
from apps.sparring.models import SparringRequest
from apps.tournaments.models import Match, Tournament
from apps.training.models import Coach, Training
from apps.users.models import Player, PlayerCategory, SkillLevel, User


def _map_ntrp_to_skill_level(level: Decimal) -> str:
    normalized = int(level.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if normalized <= 2:
        return SkillLevel.NOVICE
    if normalized <= 4:
        return SkillLevel.AMATEUR
    if normalized == 5:
        return SkillLevel.EXPERIENCED
    if normalized == 6:
        return SkillLevel.ADVANCED
    return SkillLevel.PROFESSIONAL


class Command(BaseCommand):
    help = "Creates demo data for TennisFan"

    def handle(self, *args, **options):
        self.stdout.write("Creating demo data...")

        # Create demo users and players
        demo_players = []
        player_data = [
            ("Михаил", "Марголин", "masters", 400),
            ("Андрей", "Комраков", "challenger", 350),
            ("Никита", "Овсянников", "hard", 320),
            ("Евгений", "Жаров", "tour", 280),
            ("Иван", "Чабан", "tour", 260),
            ("Артем", "Попов", "hard", 240),
            ("Владимир", "Соколов", "tour", 220),
            ("Дмитрий", "Поляков", "challenger", 300),
            ("Кирилл", "Макеев", "base", 180),
            ("Александр", "Комиссаров", "base", 170),
        ]

        for first_name, last_name, category, points in player_data:
            email = f"{first_name.lower()}.{last_name.lower()}@demo.tennisfan.ru"
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "phone": f"+7999{random.randint(1000000, 9999999)}",
                },
            )
            if created:
                user.set_password("demo12345")
                user.save()

            ntrp_level = Decimal(random.randint(1, 7))
            player, _ = Player.objects.get_or_create(
                user=user,
                defaults={
                    "category": category,
                    "ntrp_level": ntrp_level,
                    "skill_level": _map_ntrp_to_skill_level(ntrp_level),
                    "total_points": points,
                    "matches_played": random.randint(10, 50),
                    "matches_won": random.randint(5, 30),
                    "is_verified": True,
                    "telegram": f"@{first_name.lower()}_{last_name.lower()}",
                },
            )
            demo_players.append(player)
            self.stdout.write(f"  Created player: {player}")

        # Create courts
        courts_data = [
            ("Tennispark Строгино", "moscow", "Строгино, ул. Исаковского, 33", "hard"),
            ("Теннисный центр Динамо", "moscow", "Ленинградское ш., 36", "hard"),
            ("Marina Tennis Club", "moscow", "Болотниковская, 12-2", "indoor"),
            ("Корт Юбилейный", "spb", "Добролюбова пр., 18", "hard"),
            ("Теннисный клуб Крестовский", "spb", "Крестовский остров", "clay"),
        ]

        demo_courts = []
        for name, city, address, surface in courts_data:
            slug = name.lower().replace(" ", "-").replace(".", "")
            court, created = Court.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "city": city,
                    "address": address,
                    "surface": surface,
                    "courts_count": random.randint(2, 6),
                    "price_per_hour": random.randint(1500, 4000),
                    "phone": f"+7495{random.randint(1000000, 9999999)}",
                },
            )
            demo_courts.append(court)
            if created:
                self.stdout.write(f"  Created court: {court}")

        # Create tournaments (with ASCII slugs)
        tournaments_data = [
            ("Москва 250 Auckland Север", "moscow-250-auckland-north", "moscow", "challenger", 250),
            ("Москва 250 Auckland Юг", "moscow-250-auckland-south", "moscow", "tour", 250),
            ("Москва 100 Winter Open", "moscow-100-winter-open", "moscow", "base", 100),
            ("СПБ 500 Neva Cup", "spb-500-neva-cup", "spb", "hard", 500),
            ("Москва 1000 Australian Open Playoff", "moscow-1000-australian-open-playoff", "moscow", "challenger", 1000),
        ]

        demo_tournaments = []
        for i, (name, slug, city, category, points) in enumerate(tournaments_data):
            start_date = timezone.now().date() - timedelta(days=random.randint(0, 30))
            tournament, created = Tournament.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "city": city,
                    "category": category,
                    "points_multiplier": points,
                    "start_date": start_date,
                    "status": "active" if i < 2 else "completed",
                },
            )
            demo_tournaments.append(tournament)
            if created:
                self.stdout.write(f"  Created tournament: {tournament}")

        # Create matches
        for tournament in demo_tournaments:
            for _ in range(random.randint(3, 8)):
                p1, p2 = random.sample(demo_players, 2)
                winner = random.choice([p1, p2])
                match, created = Match.objects.get_or_create(
                    tournament=tournament,
                    player1=p1,
                    player2=p2,
                    defaults={
                        "court": random.choice(demo_courts),
                        "winner": winner,
                        "player1_set1": random.randint(0, 7),
                        "player2_set1": random.randint(0, 7),
                        "player1_set2": random.randint(0, 7),
                        "player2_set2": random.randint(0, 7),
                        "scheduled_datetime": timezone.now() - timedelta(hours=random.randint(1, 200)),
                        "completed_datetime": timezone.now() - timedelta(hours=random.randint(1, 100)),
                        "status": "completed",
                    },
                )
                if created:
                    self.stdout.write(f"  Created match: {match}")

        # Create coaches (with ASCII slugs)
        coaches_data = [
            ("Алексей Петров", "alexey-petrov", "Индивидуальные тренировки, работа с подачей", 15),
            ("Мария Иванова", "maria-ivanova", "Групповые занятия, детский теннис", 10),
            ("Сергей Козлов", "sergey-kozlov", "Тактика игры, соревновательный опыт", 20),
        ]

        for name, slug, spec, exp in coaches_data:
            coach, created = Coach.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "specialization": spec,
                    "experience_years": exp,
                    "city": "moscow",
                    "phone": f"+7916{random.randint(1000000, 9999999)}",
                },
            )
            if created:
                self.stdout.write(f"  Created coach: {coach}")

                # Create training for each coach
                Training.objects.create(
                    title=f"Индивидуальные занятия с {name}",
                    slug=f"individual-{slug}",
                    description="Персональные тренировки для игроков любого уровня.",
                    coach=coach,
                    training_type="individual",
                    skill_level="all",
                    duration_minutes=60,
                    price=3000 + exp * 100,
                    city="moscow",
                )

        # Create news (with ASCII slugs)
        news_data = [
            ("Открытие зимнего сезона 2026", "winter-season-2026-opening", "Стартует новый сезон турниров TennisFan!"),
            ("Результаты Auckland Open", "auckland-open-results", "Подведены итоги крупнейшего турнира января."),
            ("Новые корты в сети", "new-courts-network", "Добавлены партнёрские площадки в Строгино и на Динамо."),
        ]

        for title, slug, excerpt in news_data:
            News.objects.get_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "excerpt": excerpt,
                    "content": f"{excerpt}\n\nПодробности скоро...",
                    "is_published": True,
                },
            )
            self.stdout.write(f"  Created news: {title}")


        # Create sparring requests
        for player in random.sample(demo_players, 3):
            SparringRequest.objects.get_or_create(
                player=player,
                defaults={
                    "city": "moscow",
                    "description": f"Ищу партнёра для регулярных игр. Уровень {player.get_category_display()}.",
                    "preferred_days": "Выходные",
                    "preferred_time": "10:00-14:00",
                },
            )

        # Create rules page
        Page.objects.get_or_create(
            slug="rules",
            defaults={
                "title": "Правила турниров",
                "content": """
<h2>Как это работает</h2>
<p>TennisFan — это платформа любительских теннисных турниров, аналог ATP Tour для любителей.</p>

<h3>Категории игроков</h3>
<ul>
<li><strong>Futures</strong> — начинающие игроки (NTRP 2.0-2.5)</li>
<li><strong>Base</strong> — базовый уровень (NTRP 2.5-3.0)</li>
<li><strong>Tour</strong> — средний уровень (NTRP 3.0-3.5)</li>
<li><strong>Hard</strong> — продвинутый уровень (NTRP 3.5-4.0)</li>
<li><strong>Challenger</strong> — высокий уровень (NTRP 4.0-4.5)</li>
<li><strong>Masters</strong> — эксперты (NTRP 4.5+)</li>
</ul>

<h3>Типы турниров</h3>
<ul>
<li><strong>100</strong> — еженедельные турниры</li>
<li><strong>250</strong> — крупные турниры</li>
<li><strong>500</strong> — мастерс-турниры</li>
<li><strong>1000</strong> — главные турниры сезона</li>
</ul>
                """,
                "is_published": True,
            },
        )

        self.stdout.write(self.style.SUCCESS("Demo data created successfully!"))
