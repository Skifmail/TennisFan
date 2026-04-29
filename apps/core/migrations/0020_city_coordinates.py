from django.db import migrations, models


def seed_city_coordinates(apps, schema_editor):
    """Заполнить координаты для популярных городов из справочника и профилей игроков."""
    City = apps.get_model("core", "City")
    Player = apps.get_model("users", "Player")

    coords_by_city_name = {
        "Москва": (55.7558, 37.6173),
        "Санкт-Петербург": (59.9343, 30.3351),
        "Казань": (55.7961, 49.1064),
        "Екатеринбург": (56.8389, 60.6057),
        "Новосибирск": (55.0084, 82.9357),
        "Нижний Новгород": (56.3269, 44.0059),
        "Челябинск": (55.1644, 61.4368),
        "Самара": (53.1959, 50.1002),
        "Омск": (54.9885, 73.3242),
        "Ростов-на-Дону": (47.2357, 39.7015),
        "Уфа": (54.7388, 55.9721),
        "Краснодар": (45.0355, 38.9753),
        "Воронеж": (51.6720, 39.1843),
        "Пермь": (58.0105, 56.2502),
        "Волгоград": (48.7080, 44.5133),
        "Красноярск": (56.0153, 92.8932),
        "Сочи": (43.5855, 39.7231),
    }

    for city_name, (lat, lng) in coords_by_city_name.items():
        city_obj, _ = City.objects.get_or_create(name=city_name)
        if city_obj.lat is None or city_obj.lng is None:
            city_obj.lat = lat
            city_obj.lng = lng
            city_obj.save(update_fields=["lat", "lng"])

    # Для всех городов из игроков создаём записи в справочнике (без координат, если нет в словаре)
    for raw_city in Player.objects.exclude(city__exact="").values_list(
        "city", flat=True
    ):
        city_name = (raw_city or "").strip()
        if not city_name:
            continue
        defaults = {}
        if city_name in coords_by_city_name:
            defaults = {
                "lat": coords_by_city_name[city_name][0],
                "lng": coords_by_city_name[city_name][1],
            }
        City.objects.get_or_create(name=city_name, defaults=defaults)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_supportmessage_edited_at"),
        ("users", "0025_update_ntrp_default_to_1_5"),
    ]

    operations = [
        migrations.AddField(
            model_name="city",
            name="lat",
            field=models.FloatField(
                blank=True,
                help_text="Широта города для отображения на карте.",
                null=True,
                verbose_name="Широта",
            ),
        ),
        migrations.AddField(
            model_name="city",
            name="lng",
            field=models.FloatField(
                blank=True,
                help_text="Долгота города для отображения на карте.",
                null=True,
                verbose_name="Долгота",
            ),
        ),
        migrations.RunPython(seed_city_coordinates, migrations.RunPython.noop),
    ]
