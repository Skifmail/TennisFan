from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("training", "0017_type_prices_json"),
    ]

    operations = [
        migrations.AddField(
            model_name="training",
            name="court_has_extra_fee",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Отметьте, если аренда корта оплачивается отдельно, но точная "
                    "стоимость не указана. На странице тренировки будет показано «+ корт»."
                ),
                verbose_name="Корт оплачивается отдельно",
            ),
        ),
    ]
