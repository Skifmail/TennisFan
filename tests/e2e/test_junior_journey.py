"""Локальный пилот: родитель, проверка, запись, матч, итоги и приватный рейтинг."""

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.juniors.models import ChildProfile, GuardianLink, JuniorTournament
from tests.integration.test_junior_competition import setup_tournament
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@override_settings(JUNIOR_LEAGUE_ENABLED=True, SECURE_SSL_REDIRECT=False)
def test_parent_to_tournament_results_through_real_views(client) -> None:
    organizer, tournament, release = setup_tournament()
    parents = []
    for index in range(2):
        parent = make_user(email=f"journey-{index}@junior.test")
        parents.append(parent)
        client.force_login(parent)
        response = client.post(
            reverse("juniors:add_child"),
            {
                "first_name": f"Ребёнок{index}",
                "last_name": "СкрытаяФамилия",
                "birth_date": f"{timezone.localdate().year - 7}-01-10",
                "city": "Москва",
                "gender": "female",
                "ball": "red",
                "relation": "parent",
                "representative": "on",
            },
        )
        assert response.status_code == 302
        link = GuardianLink.objects.get(user=parent)
        client.force_login(organizer)
        review = client.post(
            reverse("admin:juniors_verify", args=[link.pk]),
            {
                "approved": "yes",
                "evidence": "Тест: оригиналы, совершеннолетие и родство проверены",
                "checked": "on",
            },
        )
        assert review.status_code == 302
        client.force_login(parent)
        assert (
            client.post(
                reverse("juniors:consent", args=[link.child_id]),
                {"kind": "processing", "release": release.pk, "accepted": "yes"},
            ).status_code
            == 302
        )
        kid = ChildProfile.objects.get(pk=link.child_id)
        assert kid.status == "active"
        response = client.post(
            reverse("juniors:tournament", args=[tournament.pk]),
            {"child": kid.pk, "regulation_version": 1, "accepted": "on"},
        )
        assert response.status_code == 302
    # Симулируем окончание регистрации, не ожидая календарного дедлайна.
    JuniorTournament.objects.filter(pk=tournament.pk).update(
        registration_ends_at=timezone.now() - timedelta(minutes=1)
    )
    client.force_login(organizer)
    assert (
        client.post(
            reverse("juniors:manage", args=[tournament.pk]), {"action": "start"}
        ).status_code
        == 302
    )
    match = tournament.matches.get()
    assert (
        client.post(
            reverse("juniors:result", args=[match.pk]), {"score": "4:2", "walkover": ""}
        ).status_code
        == 302
    )
    assert (
        client.post(
            reverse("juniors:manage", args=[tournament.pk]), {"action": "finalize"}
        ).status_code
        == 302
    )
    tournament.refresh_from_db()
    assert tournament.status == "completed"
    assert tournament.awards.count() == 2
    public = client.get(reverse("juniors:ratings"))
    assert public.status_code == 200
    assert "СкрытаяФамилия" not in public.content.decode()
    client.force_login(parents[0])
    child_id = GuardianLink.objects.get(user=parents[0]).child_id
    private = client.get(reverse("juniors:child", args=[child_id]))
    assert private.status_code == 200
    assert "СкрытаяФамилия" in private.content.decode()
    assert "Спортивная статистика" in private.content.decode()
