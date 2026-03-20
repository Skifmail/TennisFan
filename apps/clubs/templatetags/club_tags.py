from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def player_profile_url(context, player):
    """Возвращает клубный URL профиля игрока внутри клубной панели."""
    if not player or not getattr(player, "pk", None):
        return ""

    club = context.get("club")
    if context.get("is_club_panel") and club is not None:
        return reverse(
            "clubs:player_profile",
            kwargs={"slug": club.slug, "player_id": player.pk},
        )

    return reverse("profile", kwargs={"pk": player.pk})
