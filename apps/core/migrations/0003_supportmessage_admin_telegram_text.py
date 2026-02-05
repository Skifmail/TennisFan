# Add admin_telegram_text to SupportMessage (for editing message after admin reply)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_support_telegram_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportmessage",
            name="admin_telegram_text",
            field=models.TextField(blank=True, verbose_name="Текст сообщения админу"),
        ),
    ]
