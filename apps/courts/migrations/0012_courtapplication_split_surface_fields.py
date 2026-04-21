from django.db import migrations, models


def copy_surface_to_indoor(apps, schema_editor):
    CourtApplication = apps.get_model("courts", "CourtApplication")
    for app in CourtApplication.objects.all():
        if app.surface and not app.indoor_surface:
            app.indoor_surface = app.surface
            app.save(update_fields=["indoor_surface"])


def reverse_copy_surface(apps, schema_editor):
    CourtApplication = apps.get_model("courts", "CourtApplication")
    for app in CourtApplication.objects.all():
        if app.indoor_surface and not app.surface:
            app.surface = app.indoor_surface
            app.save(update_fields=["surface"])


class Migration(migrations.Migration):
    dependencies = [
        ("courts", "0011_courtapplication_is_outdoor"),
    ]

    operations = [
        migrations.AddField(
            model_name="courtapplication",
            name="indoor_surface",
            field=models.CharField(
                blank=True,
                help_text="Например: хард, грунт, трава.",
                max_length=100,
                verbose_name="Покрытие крытых кортов",
            ),
        ),
        migrations.AddField(
            model_name="courtapplication",
            name="outdoor_surface",
            field=models.CharField(
                blank=True,
                help_text="Например: хард, грунт, трава.",
                max_length=100,
                verbose_name="Покрытие открытых кортов",
            ),
        ),
        migrations.RunPython(copy_surface_to_indoor, reverse_copy_surface),
    ]
