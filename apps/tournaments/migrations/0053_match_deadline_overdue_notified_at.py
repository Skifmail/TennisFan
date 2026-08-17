from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0052_tournamentphoto_uploaded_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="deadline_overdue_notified_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Когда администраторам отправили уведомление о просроченном дедлайне. "
                    "Сбрасывается при продлении дедлайна."
                ),
                null=True,
                verbose_name="Админы уведомлены о просрочке",
            ),
        ),
    ]
