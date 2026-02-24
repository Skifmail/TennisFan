# Generated manually

from django.db import migrations


def create_footer_social_links(apps, schema_editor):
    FooterSocialLinks = apps.get_model("core", "FooterSocialLinks")
    if not FooterSocialLinks.objects.exists():
        FooterSocialLinks.objects.create(
            telegram_url="",
            vk_url="https://vk.ru/club235642769",
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_footer_social_links"),
    ]

    operations = [
        migrations.RunPython(create_footer_social_links, noop),
    ]
