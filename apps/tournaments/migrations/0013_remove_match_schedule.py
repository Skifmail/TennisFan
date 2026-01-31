# Migration: remove MatchScheduleItem and MatchScheduleSponsor models

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0012_add_match_schedule"),
    ]

    operations = [
        migrations.DeleteModel(name="MatchScheduleSponsor"),
        migrations.DeleteModel(name="MatchScheduleItem"),
    ]
