# Generated manually

from django.conf import settings
from django.db import migrations


def migrate_to_footer_social_link(apps, schema_editor):
    FooterSocialLinks = apps.get_model("core", "FooterSocialLinks")
    FooterSocialLink = apps.get_model("core", "FooterSocialLink")
    old = FooterSocialLinks.objects.first()
    default_telegram = (
        getattr(settings, "TELEGRAM_PUBLIC_COMMUNITY_URL", None) or ""
    ).strip() or "https://t.me/TennisFanru"
    if old:
        telegram_url = (old.telegram_url or "").strip() or default_telegram
        vk_url = (old.vk_url or "").strip()
    else:
        telegram_url = default_telegram
        vk_url = "https://vk.ru/club235642769"
    FooterSocialLink.objects.create(
        name="Telegram",
        url=telegram_url,
        icon_path="images/Telegram_logo.svg",
        order=0,
    )
    if vk_url:
        FooterSocialLink.objects.create(
            name="ВКонтакте",
            url=vk_url,
            icon_path="images/VK.svg",
            order=1,
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_add_footer_social_link"),
    ]

    operations = [
        migrations.RunPython(migrate_to_footer_social_link, noop),
        migrations.DeleteModel(name="FooterSocialLinks"),
    ]
