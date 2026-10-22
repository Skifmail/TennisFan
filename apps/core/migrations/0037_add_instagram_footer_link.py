# Generated manually

from django.db import migrations

INSTAGRAM_URL = "https://www.instagram.com/tennisfan.ru/"
INSTAGRAM_ICON = "images/Instagram.png"


def add_instagram_link(apps, schema_editor):
    """Добавляет ссылку на Instagram с обязательной сноской."""
    FooterSocialLink = apps.get_model("core", "FooterSocialLink")
    link = (
        FooterSocialLink.objects.filter(name__iexact="Instagram").first()
        or FooterSocialLink.objects.filter(url__icontains="instagram.com").first()
    )
    if link is None:
        FooterSocialLink.objects.create(
            name="Instagram",
            url=INSTAGRAM_URL,
            icon_path=INSTAGRAM_ICON,
            requires_disclaimer=True,
            order=2,
        )
        return

    link.name = "Instagram"
    link.url = INSTAGRAM_URL
    if not link.icon and not link.icon_path:
        link.icon_path = INSTAGRAM_ICON
    link.requires_disclaimer = True
    if not link.order:
        link.order = 2
    link.save(
        update_fields=[
            "name",
            "url",
            "icon_path",
            "requires_disclaimer",
            "order",
        ]
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_footer_social_disclaimer"),
    ]

    operations = [
        migrations.RunPython(add_instagram_link, noop),
    ]
