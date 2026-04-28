from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_alter_supportthread_guest_email_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportmessage",
            name="edited_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Изменено",
            ),
        ),
    ]
