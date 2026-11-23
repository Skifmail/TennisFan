"""Проверка одиночной связи матча с откликом без подавления предупреждений."""

from django.conf import settings
from django.db import models

from apps.tournaments.models import Match


def test_sparring_response_is_one_to_one_without_w342() -> None:
    """Django описывает существующую уникальность как связь один к одному."""
    field = Match._meta.get_field("sparring_response")

    assert isinstance(field, models.OneToOneField)
    assert field.unique and field.null and field.blank
    assert field.remote_field.on_delete is models.SET_NULL
    assert field.remote_field.related_name == "match"
    assert "fields.W342" not in settings.SILENCED_SYSTEM_CHECKS
    assert not [issue for issue in Match.check() if issue.id == "fields.W342"]
