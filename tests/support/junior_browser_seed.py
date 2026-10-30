"""Вымышленные данные только для изолированного стенда детской лиги."""

from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.juniors.models import (
    ChildConsent,
    ChildProfile,
    GuardianLink,
    JuniorSeason,
    JuniorTournament,
    LegalRelease,
)
from apps.users.models import User


def seed() -> None:
    if not str(settings.DATABASES["default"]["NAME"]).endswith(
        "tennison-juniors-preview.sqlite3"
    ):
        raise ImproperlyConfigured(
            "Демонстрационные данные разрешены только во временной БД стенда."
        )
    parent, _ = User.objects.get_or_create(
        email="parent@junior-preview.invalid",
        defaults={"first_name": "Александр", "last_name": "Волков"},
    )
    parent.set_password("junior-preview-local")
    parent.save()
    admin, _ = User.objects.get_or_create(
        email="admin@junior-preview.invalid",
        defaults={
            "is_staff": True,
            "is_superuser": True,
            "first_name": "Администратор",
        },
    )
    admin.set_password("junior-preview-local")
    admin.save()
    release, _ = LegalRelease.objects.get_or_create(
        version="DEMO-ONLY",
        defaults={
            "processing_text": "Демонстрационный текст. Не использовать для регистрации реальных детей.",
            "publication_text": "Демонстрационное разрешение на публикацию. Не является юридическим документом.",
            "reviewed": True,
            "is_current": True,
        },
    )
    year = timezone.localdate().year
    for name, last, years, ball in (
        ("Мария", "Волкова", 7, "red"),
        ("Михаил", "Волков", 9, "orange"),
    ):
        kid, _ = ChildProfile.objects.get_or_create(
            first_name=name,
            last_name=last,
            defaults={
                "birth_date": date(year - years, 1, 10),
                "city": "Москва",
                "gender": "female" if name == "Мария" else "male",
                "ball": ball,
                "status": "active",
                "birth_verified_at": timezone.now(),
            },
        )
        link, _ = GuardianLink.objects.get_or_create(
            child=kid,
            user=parent,
            defaults={
                "status": "verified",
                "verified_by": admin,
                "verified_at": timezone.now(),
                "evidence": "Вымышленная проверка стенда",
            },
        )
        ChildConsent.objects.get_or_create(
            guardian=link,
            release=release,
            kind="processing",
            defaults={"text_snapshot": release.processing_text},
        )
    season, _ = JuniorSeason.objects.get_or_create(
        name=f"Детская лига · {year}",
        defaults={"starts_on": date(year, 1, 1), "ends_on": date(year, 12, 31)},
    )
    for offset, ball, name, minimum, maximum in (
        (7, "red", "Первый старт · Красный мяч", 5, 8),
        (14, "orange", "Выходные на корте · Оранжевый мяч", 8, 10),
        (21, "green", "Зелёный кубок", 9, 10),
    ):
        start = timezone.now() + timedelta(days=offset)
        JuniorTournament.objects.get_or_create(
            name=name,
            defaults={
                "city": "Москва",
                "venue": "Демонстрационный теннисный центр",
                "organizer": admin,
                "season": season,
                "ball": ball,
                "min_age": minimum,
                "max_age": maximum,
                "starts_at": start,
                "registration_ends_at": start - timedelta(days=1),
                "capacity": 8,
                "status": "open",
                "regulation": "Демонстрационный бесплатный турнир. Формат: круговая система. Снятие до начала через семейный кабинет. Это вымышленные данные для проверки интерфейса.",
            },
        )
