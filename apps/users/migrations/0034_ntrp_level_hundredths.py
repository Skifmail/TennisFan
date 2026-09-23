"""Сила хранится до сотых и пересчитывается из рейтинга без округления вверх."""

from decimal import ROUND_DOWN, Decimal

from django.db import migrations, models


def recompute_ntrp_levels(apps, schema_editor):
    """Записать в ntrp_level силу = рейтинг / 1000, обрезанную до сотых."""
    Player = apps.get_model("users", "Player")
    step = Decimal("0.01")
    lower = Decimal("1.5")
    upper = Decimal("7.0")
    for player in Player.objects.all().iterator():
        ntrp = Decimal(str(player.total_points or 0)) / Decimal("1000")
        ntrp = max(lower, min(upper, ntrp))
        ntrp = ntrp.quantize(step, rounding=ROUND_DOWN)
        if player.ntrp_level != ntrp:
            player.ntrp_level = ntrp
            player.save(update_fields=["ntrp_level"])


def noop(apps, schema_editor):
    """Откат схемы не восстанавливает прежние десятые."""
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0033_locality_settlement_types"),
    ]

    operations = [
        migrations.AlterField(
            model_name="player",
            name="ntrp_level",
            field=models.DecimalField(
                decimal_places=2,
                default=1.5,
                max_digits=4,
                verbose_name="Уровень силы",
            ),
        ),
        migrations.AlterField(
            model_name="ntrptestresult",
            name="level",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=4,
                null=True,
                verbose_name="Рассчитанный уровень силы (NTRP)",
            ),
        ),
        migrations.RunPython(recompute_ntrp_levels, noop),
    ]
