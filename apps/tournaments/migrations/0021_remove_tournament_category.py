# Remove legacy single category from Tournament

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0020_populate_allowed_categories"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="tournament",
            name="category",
        ),
    ]
