# Generated manually for RulesSection model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0007_remove_rules_page"),
    ]

    operations = [
        migrations.CreateModel(
            name="RulesSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(help_text="Уникальный код раздела", max_length=50, unique=True, verbose_name="Код раздела")),
                ("title", models.CharField(max_length=200, verbose_name="Название (для админки)")),
                (
                    "body",
                    models.TextField(
                        blank=True,
                        help_text="HTML-разметка. Для раздела «Правила тенниса» здесь только текст без заголовка и ссылок на PDF.",
                        verbose_name="Содержание (HTML)",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Раздел правил",
                "verbose_name_plural": "Разделы правил",
                "ordering": ["slug"],
            },
        ),
    ]
