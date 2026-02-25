# binding_token: allow NULL so multiple links can have "no token" without unique violation

from django.db import migrations, models


def empty_binding_token_to_null(apps, schema_editor):
    """Replace empty string with NULL so unique constraint allows multiple rows."""
    UserTelegramLink = apps.get_model("core", "UserTelegramLink")
    UserTelegramLink.objects.filter(binding_token="").update(binding_token=None)


def null_to_empty_reverse(apps, schema_editor):
    """Reverse: set NULL back to '' (for rollback)."""
    UserTelegramLink = apps.get_model("core", "UserTelegramLink")
    UserTelegramLink.objects.filter(binding_token__isnull=True).update(binding_token="")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_migrate_to_footer_social_link"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usertelegramlink",
            name="binding_token",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Пусто/NULL = токен не выдан или уже использован. Уникален, чтобы один токен не привязал двух пользователей.",
                max_length=64,
                null=True,
                unique=True,
                verbose_name="Токен привязки (для t.me/bot?start=TOKEN)",
            ),
        ),
        migrations.RunPython(empty_binding_token_to_null, null_to_empty_reverse),
    ]
