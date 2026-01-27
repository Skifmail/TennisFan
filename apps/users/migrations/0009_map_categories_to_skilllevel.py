from django.db import migrations


def map_categories_to_skill_level(apps, schema_editor):
    mapping = {
        "futures": "novice",
        "base": "amateur",
        "tour": "experienced",
        "hard": "advanced",
        "challenger": "advanced",
        "masters": "professional",
    }

    models_and_fields = [
        ("users", "Player", "category"),
        ("tournaments", "Tournament", "category"),
        ("sparring", "SparringRequest", "desired_category"),
        ("training", "Training", "target_category"),
    ]

    for app_label, model_name, field_name in models_and_fields:
        Model = apps.get_model(app_label, model_name)
        for old, new in mapping.items():
            Model.objects.filter(**{field_name: old}).update(**{field_name: new})


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0008_alter_player_skill_level"),
        ("tournaments", "0009_tournament_entry_fee_tournament_is_one_day"),
        ("sparring", "0004_alter_sparringrequest_city"),
        ("training", "0006_alter_training_skill_level"),
    ]

    operations = [
        migrations.RunPython(map_categories_to_skill_level, migrations.RunPython.noop),
    ]