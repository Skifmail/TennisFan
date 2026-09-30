"""Скопировать текущий теннисный рейтинг игроков в профиль вида спорта."""

from django.db import migrations


def backfill_tennis_profiles(apps, schema_editor):
    """Создать теннисный профиль для игроков, у которых его ещё нет."""
    Player = apps.get_model("users", "Player")
    Profile = apps.get_model("users", "PlayerSportProfile")
    existing = set(
        Profile.objects.filter(sport="tennis").values_list("player_id", flat=True)
    )
    batch = []
    for player in Player.objects.iterator():
        if player.pk in existing:
            continue
        batch.append(
            Profile(
                player_id=player.pk,
                sport="tennis",
                total_points=player.total_points or 0,
                hidden_rating=player.hidden_rating or 0,
                ntrp_level=player.ntrp_level,
                skill_level=player.skill_level,
                matches_played=player.matches_played or 0,
                matches_won=player.matches_won or 0,
            )
        )
        if len(batch) >= 500:
            Profile.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        Profile.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0035_sport_padel"),
    ]

    operations = [
        migrations.RunPython(backfill_tennis_profiles, migrations.RunPython.noop),
    ]
