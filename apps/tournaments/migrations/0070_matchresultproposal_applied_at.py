"""Добавить сохраняемую отметку однократного применения результата."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tournaments", "0069_groups_playoff_labels")]

    operations = [
        migrations.AddField(
            model_name="matchresultproposal",
            name="applied_at",
            field=models.DateTimeField(
                "Результат применён", blank=True, editable=False, null=True
            ),
        ),
    ]
