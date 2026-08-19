from django.db import migrations, models


def backfill_no_show_sides(apps, schema_editor):
    """Проставить флаги неявки у существующих Walkover без счёта."""
    Match = apps.get_model("tournaments", "Match")
    for match in Match.objects.filter(status="walkover").iterator():
        scores = (
            match.player1_set1,
            match.player2_set1,
            match.player1_set2,
            match.player2_set2,
            match.player1_set3,
            match.player2_set3,
        )
        if any(value is not None for value in scores):
            continue
        if match.rating_status == "na":
            continue
        if match.winner_id is None and match.winner_team_id is None:
            side1, side2 = True, True
        elif match.winner_team_id:
            side1 = match.winner_team_id == match.team2_id
            side2 = match.winner_team_id == match.team1_id
        else:
            side1 = match.winner_id == match.player2_id
            side2 = match.winner_id == match.player1_id
        Match.objects.filter(pk=match.pk).update(
            walkover_no_show_side1=side1,
            walkover_no_show_side2=side2,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0053_match_deadline_overdue_notified_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="walkover_no_show_side1",
            field=models.BooleanField(
                default=False,
                help_text="Walkover (неявка) игроку 1 / команде 1.",
                verbose_name="Неявка стороны 1",
            ),
        ),
        migrations.AddField(
            model_name="match",
            name="walkover_no_show_side2",
            field=models.BooleanField(
                default=False,
                help_text="Walkover (неявка) игроку 2 / команде 2.",
                verbose_name="Неявка стороны 2",
            ),
        ),
        migrations.RunPython(backfill_no_show_sides, migrations.RunPython.noop),
    ]
