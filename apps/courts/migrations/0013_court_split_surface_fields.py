from django.db import migrations, models


def copy_surface_to_indoor(apps, schema_editor):
    Court = apps.get_model("courts", "Court")
    for court in Court.objects.all():
        if court.surface and not court.indoor_surface:
            court.indoor_surface = court.surface
            court.save(update_fields=["indoor_surface"])


def reverse_copy_surface(apps, schema_editor):
    Court = apps.get_model("courts", "Court")
    for court in Court.objects.all():
        if court.indoor_surface and not court.surface:
            court.surface = court.indoor_surface
            court.save(update_fields=["surface"])


class Migration(migrations.Migration):
    dependencies = [
        ("courts", "0012_courtapplication_split_surface_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="court",
            name="indoor_surface",
            field=models.CharField(
                blank=True,
                help_text="Например: хард, грунт, трава.",
                max_length=100,
                verbose_name="Покрытие крытых кортов",
            ),
        ),
        migrations.AddField(
            model_name="court",
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
