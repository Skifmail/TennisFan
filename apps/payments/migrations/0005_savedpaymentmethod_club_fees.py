from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0004_savedpaymentmethod_club_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="savedpaymentmethod",
            name="is_default_for_club_fees",
            field=models.BooleanField(
                default=False,
                help_text="Если включено — этот способ оплаты используется для автосписаний за членские взносы клуба.",
                verbose_name="Использовать для автосписания членского взноса клуба",
            ),
        ),
    ]
