from django.db import migrations, models

import config.validators


class Migration(migrations.Migration):

    dependencies = [
        ("clubs", "0008_add_platform_plan_max_members"),
    ]

    operations = [
        migrations.AddField(
            model_name="club",
            name="hero_image",
            field=models.ImageField(
                blank=True,
                upload_to="clubs/banners/",
                validators=[config.validators.validate_image_max_2mb],
                verbose_name="Баннер публичной страницы",
                help_text="Рекомендуется широкое изображение 1920×600 px, без текста, JPG/PNG/WebP до 2 МБ.",
            ),
        ),
    ]
