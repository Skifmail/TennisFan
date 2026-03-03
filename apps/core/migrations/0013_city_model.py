"""
Модель City для автодополнения городов.
Расширение pg_trgm и GIN-индекс только для PostgreSQL.
"""

from django.db import migrations, models


def enable_pg_trgm_and_index(apps, schema_editor):
    """Включить pg_trgm и создать GIN trigram индекс для City.name (только PostgreSQL)."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS core_city_name_gin_trgm
            ON core_city USING gin (name gin_trgm_ops);
            """
        )


def drop_gin_index(apps, schema_editor):
    """Удалить GIN-индекс при откате (расширение не удаляем)."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS core_city_name_gin_trgm;")


def populate_cities(apps, schema_editor):
    """Собрать уникальные города из Court, Coach, Training и др."""
    City = apps.get_model("core", "City")
    seen = set()

    # Court
    try:
        Court = apps.get_model("courts", "Court")
        for name in Court.objects.values_list("city", flat=True).distinct():
            if name and name.strip() and name.strip() not in seen:
                seen.add(name.strip())
                City.objects.get_or_create(name=name.strip(), defaults={})
    except LookupError:
        pass

    # Coach (training)
    try:
        Coach = apps.get_model("training", "Coach")
        for name in Coach.objects.values_list("city", flat=True).distinct():
            if name and name.strip() and name.strip() not in seen:
                seen.add(name.strip())
                City.objects.get_or_create(name=name.strip(), defaults={})
    except LookupError:
        pass

    # Training
    try:
        Training = apps.get_model("training", "Training")
        for name in Training.objects.values_list("city", flat=True).distinct():
            if name and name.strip() and name.strip() not in seen:
                seen.add(name.strip())
                City.objects.get_or_create(name=name.strip(), defaults={})
    except LookupError:
        pass

    # Tournament
    try:
        Tournament = apps.get_model("tournaments", "Tournament")
        for name in Tournament.objects.values_list("city", flat=True).distinct():
            if name and name.strip() and name.strip() not in seen:
                seen.add(name.strip())
                City.objects.get_or_create(name=name.strip(), defaults={})
    except LookupError:
        pass

    # Sparring (оба варианта модели — по необходимости)
    for app_label, model_name in [
        ("sparring", "SparringRequest"),
        ("sparring", "DoublesRequest"),
    ]:
        try:
            Model = apps.get_model(app_label, model_name)
            for name in Model.objects.values_list("city", flat=True).distinct():
                if name and name.strip() and name.strip() not in seen:
                    seen.add(name.strip())
                    City.objects.get_or_create(name=name.strip(), defaults={})
        except LookupError:
            pass


def noop_reverse(apps, schema_editor):
    """При откате миграции данные City не удаляем вручную — удалится модель."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_usertelegramlink_binding_token_nullable"),
    ]

    operations = [
        migrations.CreateModel(
            name="City",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        db_index=True,
                        max_length=100,
                        unique=True,
                        verbose_name="Название",
                    ),
                ),
            ],
            options={
                "verbose_name": "город",
                "verbose_name_plural": "города",
                "ordering": ["name"],
            },
        ),
        migrations.RunPython(enable_pg_trgm_and_index, drop_gin_index),
        migrations.RunPython(populate_cities, noop_reverse),
    ]
