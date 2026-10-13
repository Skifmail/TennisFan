"""Переименовать заголовок страницы стрингеров."""

from django.db import migrations, models

NEW_TITLE = "Натяжка струн - услуги стрингеров"


def rename_stringer_page_title(apps, schema_editor):
    """Заменить прежний заголовок «Стрингеры» на новый."""
    StringerPage = apps.get_model("content", "StringerPage")
    StringerPage.objects.filter(title="Стрингеры").update(title=NEW_TITLE)


def restore_stringer_page_title(apps, schema_editor):
    """Вернуть заголовок «Стрингеры»."""
    StringerPage = apps.get_model("content", "StringerPage")
    StringerPage.objects.filter(title=NEW_TITLE).update(title="Стрингеры")


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0028_refresh_padel_rules_self_contained"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stringerpage",
            name="title",
            field=models.CharField(
                default=NEW_TITLE,
                max_length=200,
                verbose_name="Заголовок страницы",
            ),
        ),
        migrations.RunPython(rename_stringer_page_title, restore_stringer_page_title),
    ]
