from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0017_club_legal_and_user_consent"),
    ]

    operations = [
        migrations.AddField(
            model_name="clubplayerplan",
            name="registration_limit_period",
            field=models.CharField(
                choices=[
                    ("monthly", "В месяц"),
                    ("plan_period", "За весь срок тарифа"),
                ],
                default="monthly",
                help_text=(
                    "В месяц — лимит обновляется каждый календарный месяц. "
                    "За весь срок тарифа — общий лимит на весь оплаченный период."
                ),
                max_length=20,
                verbose_name="Период лимита регистраций",
            ),
        ),
    ]
