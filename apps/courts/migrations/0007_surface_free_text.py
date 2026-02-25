# Generated manually: surface as free text instead of fixed choices

from django.db import migrations, models

SURFACE_MAP = {
    "hard": "Хард",
    "clay": "Грунт",
    "grass": "Трава",
    "indoor": "Закрытый хард",
}


def surface_to_display(apps, schema_editor):
    """Convert old choice values to display labels for existing rows."""
    Court = apps.get_model("courts", "Court")
    CourtApplication = apps.get_model("courts", "CourtApplication")
    for court in Court.objects.all():
        if court.surface in SURFACE_MAP:
            court.surface = SURFACE_MAP[court.surface]
            court.save(update_fields=["surface"])
    for app in CourtApplication.objects.all():
        if app.surface in SURFACE_MAP:
            app.surface = SURFACE_MAP[app.surface]
            app.save(update_fields=["surface"])


def surface_to_choice_reverse(apps, schema_editor):
    """Reverse: display label back to choice value (for rollback)."""
    rev = {v: k for k, v in SURFACE_MAP.items()}
    Court = apps.get_model("courts", "Court")
    CourtApplication = apps.get_model("courts", "CourtApplication")
    for court in Court.objects.all():
        if court.surface in rev:
            court.surface = rev[court.surface]
            court.save(update_fields=["surface"])
    for app in CourtApplication.objects.all():
        if app.surface in rev:
            app.surface = rev[app.surface]
            app.save(update_fields=["surface"])


class Migration(migrations.Migration):

    dependencies = [
        ("courts", "0006_alter_court_image_alter_courtapplication_image"),
    ]

    operations = [
        migrations.RunPython(surface_to_display, surface_to_choice_reverse),
        migrations.AlterField(
            model_name="court",
            name="surface",
            field=models.CharField(
                help_text="Например: хард, грунт, трава. Заполняется заявителем при подаче заявки.",
                max_length=100,
                verbose_name="Покрытие",
            ),
        ),
        migrations.AlterField(
            model_name="courtapplication",
            name="surface",
            field=models.CharField(
                help_text="Например: хард, грунт, трава. Укажите вручную.",
                max_length=100,
                verbose_name="Покрытие",
            ),
        ),
    ]
