# Generated manually for FAN bye matches.

from django.contrib.auth.hashers import make_password
from django.db import migrations


BYE_EMAIL = "bye@tennisfan.local"


def create_bye(apps, schema_editor):
    User = apps.get_model("users", "User")
    Player = apps.get_model("users", "Player")
    if User.objects.filter(email=BYE_EMAIL).exists():
        return
    user = User.objects.create(
        email=BYE_EMAIL,
        first_name="",
        last_name="",
        is_active=False,
        password=make_password(None),
    )
    Player.objects.create(user=user, is_bye=True)


def remove_bye(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(email=BYE_EMAIL).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0015_player_is_bye"),
    ]

    operations = [
        migrations.RunPython(create_bye, remove_bye),
    ]
