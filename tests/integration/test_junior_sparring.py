"""Товарищеские спарринги детей и взрослых через представителя."""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.juniors.models import (
    ChildProfile,
    GuardianLink,
    JuniorSparringRequest,
    JuniorSparringResponse,
)
from apps.juniors.sparring import (
    accept_response,
    adult_can_sparring,
    age_preference_label,
    age_warning,
    cancel_response,
    child_can_sparring,
    close_request,
    create_sparring_request,
    respond_to_sparring,
)
from apps.users.models import Notification
from tests.support.factories import make_player, make_subscription, make_user

pytestmark = pytest.mark.django_db


def subscriber(email: str = "adult@junior.test"):
    """Создаёт взрослого с действующим тарифом, включающим спарринги."""
    user = make_user(email=email)
    make_subscription(
        user,
        tier_name=f"sparring-{email.split('@')[0]}",
        tier_kwargs={"has_sparring": True},
    )
    return user


def make_child(
    guardian,
    *,
    first_name: str = "Анна",
    birth_date: date = date(2018, 5, 10),
    status: str = ChildProfile.Status.ACTIVE,
) -> ChildProfile:
    """Создаёт активного ребёнка с проверенным представителем."""
    child = ChildProfile.objects.create(
        first_name=first_name,
        last_name="Иванова",
        birth_date=birth_date,
        city="Москва",
        gender="female",
        ball="red",
        status=status,
    )
    GuardianLink.objects.create(
        child=child,
        user=guardian,
        relation=GuardianLink.Relation.PARENT,
        status=GuardianLink.Status.VERIFIED,
        verified_at=timezone.now(),
    )
    return child


def request_data(**overrides):
    """Возвращает данные заявки, которые проходят проверку модели."""
    data = {
        "city": "Москва",
        "description": "Игра на красном мяче, есть свободный корт в субботу.",
        "partner_kind": JuniorSparringRequest.PartnerKind.ANY,
        "gender_preference": JuniorSparringRequest.GenderPreference.OPEN,
        "desired_age_min": 7,
        "desired_age_max": 10,
        "preferred_days": "Суббота",
        "preferred_time": "Утро",
        "preferred_location": "Корт в Сокольниках",
    }
    data.update(overrides)
    return data


def create_request(guardian, child, **overrides) -> JuniorSparringRequest:
    """Размещает заявку через сервис тем же путём, что и представление."""
    return create_sparring_request(
        actor=guardian, child_id=child.pk, data=request_data(**overrides)
    )


def test_child_receives_guardian_subscription() -> None:
    """Подписка представителя автоматически открывает спарринги ребёнку."""
    parent = subscriber()
    child = make_child(parent)

    assert adult_can_sparring(parent)
    assert child_can_sparring(child)


def test_child_without_guardian_subscription_cannot_create() -> None:
    """Без оплаченного тарифа представителя заявка не создаётся."""
    parent = make_user(email="free-parent@junior.test")
    child = make_child(parent)

    assert not child_can_sparring(child)
    with pytest.raises(ValidationError, match="по подписке"):
        create_request(parent, child)
    assert not JuniorSparringRequest.objects.exists()


def test_draft_child_cannot_create_until_verified() -> None:
    """До проверки представительства и активации профиля игра недоступна."""
    parent = subscriber("draft-parent@junior.test")
    child = make_child(parent, status=ChildProfile.Status.DRAFT)

    with pytest.raises(ValidationError, match="после проверки"):
        create_request(parent, child)
    assert not JuniorSparringRequest.objects.exists()


def test_guardian_creates_request_through_view(client) -> None:
    """Представитель размещает заявку из личного кабинета."""
    parent = subscriber("view-parent@junior.test")
    child = make_child(parent)
    client.force_login(parent)

    response = client.post(
        reverse("juniors:sparring_new"),
        {**request_data(), "child": child.pk},
        secure=True,
    )

    assert response.status_code == 302
    sparring = JuniorSparringRequest.objects.get()
    assert sparring.child == child
    assert sparring.guardian.user == parent
    assert response.url == reverse("juniors:sparring", args=[sparring.pk])


def test_stranger_cannot_create_request_for_someone_elses_child() -> None:
    """Создать заявку от имени чужого ребёнка нельзя даже при подписке."""
    parent = subscriber("owner-parent@junior.test")
    child = make_child(parent)
    stranger = subscriber("stranger@junior.test")

    with pytest.raises(PermissionDenied):
        create_request(stranger, child)


def test_adult_responds_and_acceptance_opens_contacts(
    django_capture_on_commit_callbacks,
) -> None:
    """Взрослый по подписке откликается, согласование закрывает заявку и шлёт события."""
    parent = subscriber("notify-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child)
    adult = make_player(email_suffix="adult-respondent")
    make_subscription(
        adult.user,
        tier_name="sparring-adult-respondent",
        tier_kwargs={"has_sparring": True},
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = respond_to_sparring(
            actor=adult.user, request_id=sparring.pk, message="Готов сыграть в субботу."
        )

    assert response.player_id == adult.pk
    assert response.status == JuniorSparringResponse.Status.PENDING
    assert Notification.objects.filter(
        user=parent, event_key="junior.sparring_response"
    ).exists()

    with django_capture_on_commit_callbacks(execute=True):
        accept_response(actor=parent, response_id=response.pk)

    sparring.refresh_from_db()
    response.refresh_from_db()
    assert sparring.status == JuniorSparringRequest.Status.CLOSED
    assert response.status == JuniorSparringResponse.Status.ACCEPTED
    assert Notification.objects.filter(
        user=parent, event_key="junior.sparring_accepted"
    ).exists()
    assert Notification.objects.filter(
        user=adult.user, event_key="junior.sparring_accepted"
    ).exists()


def test_guardian_of_other_child_can_respond_for_their_child(
    django_capture_on_commit_callbacks,
) -> None:
    """Представитель другого ребёнка откликается от имени своего ребёнка."""
    parent = subscriber("first-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child)
    other_parent = subscriber("second-parent@junior.test")
    other_child = make_child(other_parent, first_name="Борис")

    with django_capture_on_commit_callbacks(execute=True):
        response = respond_to_sparring(
            actor=other_parent, request_id=sparring.pk, child_id=other_child.pk
        )

    assert response.child == other_child
    assert response.guardian.user == other_parent
    assert response.respondent_label == other_child.public_label
    assert Notification.objects.filter(
        user=parent, event_key="junior.sparring_response"
    ).exists()


def test_partner_kind_limits_who_can_respond() -> None:
    """Тип соперника закрывает неподходящий отклик, не трогая заявку."""
    parent = subscriber("kind-parent@junior.test")
    child = make_child(parent)
    child_only = create_request(
        parent, child, partner_kind=JuniorSparringRequest.PartnerKind.CHILD
    )
    adult = make_player(email_suffix="kind-adult")
    make_subscription(
        adult.user,
        tier_name="sparring-kind-adult",
        tier_kwargs={"has_sparring": True},
    )

    with pytest.raises(ValidationError, match="ребёнка"):
        respond_to_sparring(actor=adult.user, request_id=child_only.pk)

    adult_only = create_request(
        parent, child, partner_kind=JuniorSparringRequest.PartnerKind.ADULT
    )
    other_parent = subscriber("kind-other-parent@junior.test")
    other_child = make_child(other_parent, first_name="Вера")
    with pytest.raises(ValidationError, match="взрослого"):
        respond_to_sparring(
            actor=other_parent,
            request_id=adult_only.pk,
            child_id=other_child.pk,
        )


def test_age_preference_is_only_a_warning() -> None:
    """Возраст вне пожелания не блокирует отклик, а сопровождается подсказкой."""
    parent = subscriber("age-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child, desired_age_min=12, desired_age_max=14)

    assert age_preference_label(sparring) == "12–14 лет"
    assert age_warning(sparring, 13) == ""
    warning = age_warning(sparring, 8)
    assert "8 лет" in warning
    assert "подсказка" in warning
    assert "не ограничение" in warning


def test_cannot_respond_to_own_child_request() -> None:
    """Своя же заявка не принимает отклик от того же представителя."""
    parent = subscriber("self-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child)

    with pytest.raises(ValidationError, match="своего ребёнка"):
        respond_to_sparring(actor=parent, request_id=sparring.pk, child_id=child.pk)


def test_stranger_cannot_close_or_accept() -> None:
    """Закрывать заявку и принимать отклики может только представитель."""
    parent = subscriber("close-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child)
    adult = make_player(email_suffix="close-adult")
    make_subscription(
        adult.user,
        tier_name="sparring-close-adult",
        tier_kwargs={"has_sparring": True},
    )
    response = respond_to_sparring(actor=adult.user, request_id=sparring.pk)
    stranger = subscriber("close-stranger@junior.test")

    with pytest.raises(PermissionDenied):
        close_request(actor=stranger, request_id=sparring.pk)
    with pytest.raises(PermissionDenied):
        accept_response(actor=stranger, response_id=response.pk)

    sparring.refresh_from_db()
    response.refresh_from_db()
    assert sparring.status == JuniorSparringRequest.Status.ACTIVE
    assert response.status == JuniorSparringResponse.Status.PENDING


def test_acceptance_rejects_other_pending_responses(
    django_capture_on_commit_callbacks,
) -> None:
    """Принятый отклик закрывает заявку, остальные ожидающие получают отказ."""
    parent = subscriber("many-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child)
    first_adult = make_player(email_suffix="many-first")
    second_adult = make_player(email_suffix="many-second")
    for index, adult in enumerate((first_adult, second_adult)):
        make_subscription(
            adult.user,
            tier_name=f"sparring-many-{index}",
            tier_kwargs={"has_sparring": True},
        )
    first = respond_to_sparring(actor=first_adult.user, request_id=sparring.pk)
    second = respond_to_sparring(actor=second_adult.user, request_id=sparring.pk)

    with django_capture_on_commit_callbacks(execute=True):
        accept_response(actor=parent, response_id=first.pk)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == JuniorSparringResponse.Status.ACCEPTED
    assert second.status == JuniorSparringResponse.Status.REJECTED


def test_respondent_can_cancel_own_pending_response() -> None:
    """Взрослый отменяет свой отклик, но не чужой."""
    parent = subscriber("cancel-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child)
    adult = make_player(email_suffix="cancel-adult")
    make_subscription(
        adult.user,
        tier_name="sparring-cancel-adult",
        tier_kwargs={"has_sparring": True},
    )
    response = respond_to_sparring(actor=adult.user, request_id=sparring.pk)

    with pytest.raises(PermissionDenied):
        cancel_response(actor=parent, response_id=response.pk)

    cancel_response(actor=adult.user, response_id=response.pk)
    response.refresh_from_db()
    assert response.status == JuniorSparringResponse.Status.REJECTED


def test_detail_page_hides_child_name_and_contacts_before_acceptance(client) -> None:
    """Чужая заявка показывает метку ребёнка и не раскрывает контакты."""
    parent = subscriber("detail-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child)
    stranger = subscriber("detail-stranger@junior.test")
    client.force_login(stranger)

    response = client.get(reverse("juniors:sparring", args=[sparring.pk]), secure=True)

    assert response.status_code == 200
    body = response.content.decode()
    assert child.public_label in body
    assert "Анна" not in body
    assert "detail-parent@junior.test" not in body


def test_sparring_pages_render_for_guardian(client) -> None:
    """Список заявок и кабинет спаррингов открываются без ошибок шаблонов."""
    parent = subscriber("pages-parent@junior.test")
    child = make_child(parent)
    create_request(parent, child)
    client.force_login(parent)

    for name in ("juniors:sparrings", "juniors:my_sparrings"):
        response = client.get(reverse(name), secure=True)
        assert response.status_code == 200


def test_adult_responds_through_view(client) -> None:
    """Отклик взрослого игрока сохраняется через форму заявки."""
    parent = subscriber("form-parent@junior.test")
    child = make_child(parent)
    sparring = create_request(parent, child)
    adult = make_player(email_suffix="form-adult")
    make_subscription(
        adult.user,
        tier_name="sparring-form-adult",
        tier_kwargs={"has_sparring": True},
    )
    client.force_login(adult.user)

    response = client.post(
        reverse("juniors:sparring_respond", args=[sparring.pk]),
        {"message": "Могу в субботу утром."},
        secure=True,
    )

    assert response.status_code == 302
    saved = JuniorSparringResponse.objects.get(request=sparring)
    assert saved.player_id == adult.pk
    assert saved.message == "Могу в субботу утром."
