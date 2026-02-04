# Copy Tournament.category into TournamentAllowedCategory

from django.db import migrations


def populate_allowed_categories(apps, schema_editor):
    Tournament = apps.get_model("tournaments", "Tournament")
    TournamentAllowedCategory = apps.get_model("tournaments", "TournamentAllowedCategory")
    for t in Tournament.objects.all():
        if t.category:
            TournamentAllowedCategory.objects.get_or_create(tournament=t, defaults={"category": t.category})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0019_add_tournament_allowed_category"),
    ]

    operations = [
        migrations.RunPython(populate_allowed_categories, noop),
    ]
