from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0002_paymentrecord"),
    ]

    operations = [
        migrations.AddField(
            model_name="savedpaymentmethod",
            name="is_default_for_club_plans",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Если включено — этот способ оплаты используется для "
                    "автосписаний за клубные тарифы."
                ),
                verbose_name="Использовать для автопродления клубного тарифа",
            ),
        ),
    ]
