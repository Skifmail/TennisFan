"""Данные публичного каталога для интеграционной и браузерной приёмки."""

from decimal import Decimal

from apps.training.models import Coach, Training


def make_catalog_training(*, slug: str = "catalog-full") -> Training:
    """Создать предложение с полным набором форматов, уровней и доплатой за корт."""
    coach = Coach.objects.create(
        name="Алексей Владимирович Артёменко",
        slug=f"{slug}-coach",
        city="Москва",
    )
    return Training.objects.create(
        title="Индивидуальные и групповые тренировки по теннису для взрослых",
        slug=slug,
        coach=coach,
        city="Москва, Раменское, Жуковский, Воскресенск",
        description="Постановка техники, работа над подачей и тактикой игры на корте.",
        short_description="Занятия для начинающих и опытных игроков. Подберём удобное время и площадку.",
        type_prices={
            "individual": 3000,
            "group": 1500,
            "mini_group": 2000,
            "sparring": 2500,
            "split": 2000,
        },
        skill_levels=["novice", "amateur", "experienced", "advanced", "professional"],
        price_min=Decimal("1500"),
        price_max=Decimal("3000"),
        court_has_extra_fee=True,
        duration_minutes=90,
    )
