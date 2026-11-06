"""Вымышленные кемпы исключительно для временного браузерного стенда."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.camps.models import Camp, CampPackage, CampSession
from apps.training.models import Coach
from apps.users.models import User


def seed() -> None:
    """Создаёт демонстрационную программу, не затрагивая рабочую базу."""
    if not str(settings.DATABASES["default"]["NAME"]).endswith(
        "tennison-camps-preview.sqlite3"
    ):
        raise ImproperlyConfigured(
            "Демо разрешено только в изолированной временной базе."
        )
    manager, _ = User.objects.get_or_create(
        email="admin@camp-preview.invalid",
        defaults={"is_staff": True, "is_superuser": True, "first_name": "Команда"},
    )
    manager.set_password("camp-preview-local")
    manager.save()
    coach, _ = Coach.objects.get_or_create(
        slug="demo-coach",
        defaults={
            "name": "Александр · демо",
            "city": "Москва",
            "specialization": "Техника и тактика любительского тенниса",
            "experience_years": 12,
        },
    )
    camp, _ = Camp.objects.get_or_create(
        slug="demo-sochi",
        defaults={
            "title": "Неделя тенниса у моря",
            "subtitle": "Утро на корте, новые соперники и время для себя. Семь дней с командой TennisFan.",
            "description": "Демонстрационная программа для проверки интерфейса. Это вымышленное предложение, не открытая продажа.\n\nСобираемся небольшой командой, работаем над техникой и играем друг с другом. Между тренировками — море, прогулки и общие планы на вечер.",
            "levels": "Начинающие и любители. Группы формируем по уровню игры.",
            "program": "День 1 · Знакомимся и определяем группы.\nДни 2–5 · Утренние тренировки, отдых и вечерние матчи.\nДень 6 · Товарищеские встречи и разбор игры.\nДень 7 · Подводим итоги и прощаемся до следующей поездки.",
            "preparation": "Ракетка, обувь для корта, удобная одежда, головной убор и бутылка для воды.",
            "is_published": True,
        },
    )
    camp.coaches.add(coach)
    start = timezone.localdate() + timedelta(days=45)
    session, _ = CampSession.objects.get_or_create(
        camp=camp,
        start_date=start,
        defaults={
            "end_date": start + timedelta(days=6),
            "registration_deadline": start - timedelta(days=7),
            "city": "Сочи",
            "venue": "Демонстрационный теннисный центр",
            "surface": "Открытые хардовые корты",
            "capacity": 16,
            "minimum_players": 8,
            "tennis_hours": 18,
            "status": "open",
            "coordinator": manager,
            "contact_email": "team@camp-preview.invalid",
            "accommodation": "Шесть ночей в двухместном номере, завтраки. Условия приведены только для демонстрации.",
            "included": "Тренировки с командой\nМатчи и разбор техники\nОбщая программа кемпа",
            "excluded": "Дорога до Сочи\nТрансфер\nОбеды и ужины",
            "terms": "Демонстрационные условия. Место подтверждает координатор после согласования пакета и стоимости. Не использовать для реальной продажи.",
            "weather_policy": "При непогоде согласуем перенос занятий.",
        },
    )
    for name, price, accommodation, description in [
        (
            "Спортпакет",
            "45000",
            False,
            "18 часов тенниса, работа в группе и товарищеские встречи. Проживание оформляете самостоятельно.",
        ),
        (
            "Теннис и проживание",
            "85000",
            True,
            "Спортивная программа, шесть ночей в двухместном номере и завтраки.",
        ),
    ]:
        CampPackage.objects.get_or_create(
            session=session,
            name=name,
            defaults={
                "price": Decimal(price),
                "with_accommodation": accommodation,
                "description": description,
            },
        )
