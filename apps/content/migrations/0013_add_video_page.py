# Generated migration for VideoPage, LiveStream, and Video models

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0012_newsphoto"),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("live_streams_title", models.CharField(default="Прямые трансляции", max_length=200, verbose_name="Заголовок блока «Прямые трансляции»")),
                ("playlist_title", models.CharField(default="Плейлист", max_length=200, verbose_name="Заголовок блока «Плейлист»")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Страница «Видео»",
                "verbose_name_plural": "Страница «Видео»",
            },
        ),
        migrations.CreateModel(
            name="LiveStream",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200, verbose_name="Название трансляции")),
                ("url", models.URLField(help_text="Ссылка на прямую трансляцию YouTube, VK или RuTube", verbose_name="Ссылка на трансляцию")),
                ("platform", models.CharField(choices=[("youtube", "YouTube"), ("vk", "VK"), ("rutube", "RuTube")], default="youtube", max_length=20, verbose_name="Платформа")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активна (показывать)")),
                ("order", models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                (
                    "video_page",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="live_streams",
                        to="content.videopage",
                        verbose_name="Страница видео",
                    ),
                ),
            ],
            options={
                "verbose_name": "Прямая трансляция",
                "verbose_name_plural": "Прямые трансляции",
                "ordering": ["order", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="Video",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200, verbose_name="Название видео")),
                ("description", models.TextField(blank=True, verbose_name="Описание")),
                ("url", models.URLField(help_text="Ссылка на видео YouTube, VK или RuTube", verbose_name="Ссылка на видео")),
                ("platform", models.CharField(choices=[("youtube", "YouTube"), ("vk", "VK"), ("rutube", "RuTube")], default="youtube", max_length=20, verbose_name="Платформа")),
                ("thumbnail_url", models.URLField(blank=True, help_text="Ссылка на изображение превью. Если не указано, будет использовано превью с платформы.", verbose_name="URL превью (опционально)")),
                ("is_published", models.BooleanField(default=True, verbose_name="Опубликовано")),
                ("order", models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")),
                ("views_count", models.PositiveIntegerField(default=0, verbose_name="Просмотры")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                (
                    "video_page",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="videos",
                        to="content.videopage",
                        verbose_name="Страница видео",
                    ),
                ),
            ],
            options={
                "verbose_name": "Видео",
                "verbose_name_plural": "Видео",
                "ordering": ["order", "-created_at"],
            },
        ),
    ]
