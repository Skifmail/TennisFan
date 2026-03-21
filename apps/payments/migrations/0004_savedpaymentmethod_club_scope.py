import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0011_clubjoinrequest"),
        ("payments", "0003_savedpaymentmethod_club_plans"),
    ]

    operations = [
        migrations.AddField(
            model_name="savedpaymentmethod",
            name="club",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Для клубных автосписаний карта привязывается к конкретному "
                    "клубу и его ЮKassa-мерчанту. Для глобальной подписки поле пустое."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="saved_payment_methods",
                to="clubs.club",
                verbose_name="Клуб",
            ),
        ),
    ]
