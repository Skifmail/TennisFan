from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tournaments", "0079_tournament_court_has_extra_fee")]
    operations = [
        migrations.AlterField(
            model_name="manualtournamentstage",
            name="kind",
            field=models.CharField(
                "Тип этапа",
                max_length=16,
                choices=[
                    ("custom_round", "Круговой"),
                    ("custom_knockout", "Олимпийский на выбывание"),
                    ("custom_places", "Олимпийский за все места"),
                    ("qualifier", "Круговой отбор"),
                    ("main", "Основная олимпийская сетка"),
                    ("consolation", "Утешительная олимпийская сетка"),
                ],
            ),
        )
    ]
