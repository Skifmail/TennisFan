"""Канонические покрытия корта: JSON-списки вместо свободного текста."""

from django.db import migrations, models

from apps.courts.surfaces import assign_surfaces_from_legacy, compose_surface_display


def forwards_convert_surfaces(apps, schema_editor):
    """Перенести свободный текст покрытий в канонические списки."""
    court_model = apps.get_model("courts", "Court")
    application_model = apps.get_model("courts", "CourtApplication")
    for model in (court_model, application_model):
        rows = list(model.objects.all())
        for row in rows:
            indoor, outdoor = assign_surfaces_from_legacy(
                is_indoor=bool(row.is_indoor),
                is_outdoor=bool(row.is_outdoor),
                indoor_text=str(getattr(row, "indoor_surface", "") or ""),
                outdoor_text=str(getattr(row, "outdoor_surface", "") or ""),
                aggregated_text=str(getattr(row, "surface", "") or ""),
            )
            row.indoor_surfaces = indoor
            row.outdoor_surfaces = outdoor
            row.surface = compose_surface_display(
                is_indoor=bool(row.is_indoor),
                indoor_surfaces=indoor,
                is_outdoor=bool(row.is_outdoor),
                outdoor_surfaces=outdoor,
            )
        if rows:
            model.objects.bulk_update(
                rows,
                ["indoor_surfaces", "outdoor_surfaces", "surface"],
            )


def backwards_restore_text(apps, schema_editor):
    """Вернуть текстовые поля из канонических списков."""
    court_model = apps.get_model("courts", "Court")
    application_model = apps.get_model("courts", "CourtApplication")
    for model in (court_model, application_model):
        rows = list(model.objects.all())
        for row in rows:
            indoor_text = compose_surface_display(
                is_indoor=True,
                indoor_surfaces=row.indoor_surfaces,
                is_outdoor=False,
                outdoor_surfaces=[],
            )
            outdoor_text = compose_surface_display(
                is_indoor=False,
                indoor_surfaces=[],
                is_outdoor=True,
                outdoor_surfaces=row.outdoor_surfaces,
            )
            row.indoor_surface = indoor_text
            row.outdoor_surface = outdoor_text
            row.surface = compose_surface_display(
                is_indoor=bool(row.is_indoor),
                indoor_surfaces=row.indoor_surfaces,
                is_outdoor=bool(row.is_outdoor),
                outdoor_surfaces=row.outdoor_surfaces,
            )
        if rows:
            model.objects.bulk_update(
                rows,
                ["indoor_surface", "outdoor_surface", "surface"],
            )


class Migration(migrations.Migration):

    dependencies = [
        ("courts", "0018_locality_settlement_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="court",
            name="indoor_surfaces",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Хард, грунт, терафлекс или другое. Можно выбрать несколько.",
                verbose_name="Покрытие крытых кортов",
            ),
        ),
        migrations.AddField(
            model_name="court",
            name="outdoor_surfaces",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Хард, грунт, терафлекс или другое. Можно выбрать несколько.",
                verbose_name="Покрытие открытых кортов",
            ),
        ),
        migrations.AddField(
            model_name="courtapplication",
            name="indoor_surfaces",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Хард, грунт, терафлекс или другое. Можно выбрать несколько.",
                verbose_name="Покрытие крытых кортов",
            ),
        ),
        migrations.AddField(
            model_name="courtapplication",
            name="outdoor_surfaces",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Хард, грунт, терафлекс или другое. Можно выбрать несколько.",
                verbose_name="Покрытие открытых кортов",
            ),
        ),
        migrations.AlterField(
            model_name="court",
            name="surface",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Собирается автоматически из выбранных покрытий.",
                max_length=200,
                verbose_name="Покрытие",
            ),
        ),
        migrations.AlterField(
            model_name="courtapplication",
            name="surface",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Собирается автоматически из выбранных покрытий.",
                max_length=200,
                verbose_name="Покрытие",
            ),
        ),
        migrations.RunPython(forwards_convert_surfaces, backwards_restore_text),
        migrations.RemoveField(
            model_name="court",
            name="indoor_surface",
        ),
        migrations.RemoveField(
            model_name="court",
            name="outdoor_surface",
        ),
        migrations.RemoveField(
            model_name="courtapplication",
            name="indoor_surface",
        ),
        migrations.RemoveField(
            model_name="courtapplication",
            name="outdoor_surface",
        ),
    ]
