"""Приём заявок, приватность, вместимость и изоляция рейтинга кемпа."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.camps.models import (
    CampApplication,
    CampGame,
    CampParticipant,
    CampPayment,
    CampSession,
    CampTournament,
)
from apps.camps.services import record_payment, submit_application, update_application
from apps.notifications.models import Delivery
from apps.users.models import User
from tests.support.factories import make_player, make_tournament

pytestmark = pytest.mark.django_db


def _submit(
    session: CampSession,
    *,
    count: int = 1,
    user: User | None = None,
    key: uuid.UUID | None = None,
) -> CampApplication:
    """Подаёт заявку через бизнес-операцию с синтетическими данными."""
    return submit_application(
        session_id=session.pk,
        submission_key=key or uuid.uuid4(),
        contact={
            "package": session.packages.first(),
            "contact_name": "Алекс",
            "email": "camp-guest@example.test",
            "phone": "+79990000000",
        },
        participants=[
            {"name": f"Игрок {index}", "role": "adult", "age": 30, "level": "amateur"}
            for index in range(count)
        ],
        user=user,
    )


def _values(status: str = "confirmed", **kwargs: Any) -> dict[str, Any]:
    """Формирует согласованные координатором условия."""
    return {
        "status": status,
        "quoted_total": Decimal("45000"),
        "payment_due": None,
        "accommodation_notes": "Внутреннее размещение",
        "staff_notes": "Секретная заметка",
        **kwargs,
    }


def _payload(client: Client, session: CampSession) -> dict[str, str]:
    """Получает привязанный к браузеру ключ и собирает форму."""
    client.get(reverse("camps:apply", args=[session.pk]))
    return {
        "submission_key": client.session["camp_submission_keys"][str(session.pk)],
        "package": str(session.packages.first().pk),
        "contact_name": "Алекс",
        "email": "guest@example.test",
        "phone": "+7 (999) 000-00-00",
        "consent": "on",
        "people-TOTAL_FORMS": "1",
        "people-INITIAL_FORMS": "0",
        "people-MIN_NUM_FORMS": "1",
        "people-MAX_NUM_FORMS": "12",
        "people-0-name": "Игрок Тест",
        "people-0-role": "adult",
        "people-0-age": "30",
        "people-0-level": "amateur",
    }


def test_camps_public_pages_hide_drafts(
    client: Client, camp_session: CampSession
) -> None:
    """Неопубликованные программы не раскрываются по прямой ссылке."""
    for url in (reverse("camps:list"), camp_session.camp.get_absolute_url()):
        assert client.get(url).status_code == 200
    camp_session.camp.is_published = False
    camp_session.camp.save()
    assert client.get(camp_session.camp.get_absolute_url()).status_code == 404
    assert "Наш кемп" not in client.get(reverse("camps:list")).content.decode()


def test_camps_hero_assets_exist_in_static() -> None:
    """Видео и постер hero лежат в статике, иначе collectstatic упадёт на манифесте."""
    video = Path(settings.BASE_DIR) / "static/video/tennisfan-camps-web.mp4"
    poster = Path(settings.BASE_DIR) / "static/images/camps-hero-poster.webp"

    assert video.is_file(), f"Нет файла {video}"
    assert poster.is_file(), f"Нет файла {poster}"


def test_camps_public_list_uses_video_hero(client: Client) -> None:
    """Каталог показывает видео с быстрой webp-заглушкой вместо иллюстрации."""
    body = client.get(reverse("camps:list")).content.decode()

    assert "tennisfan-camps-web.mp4" in body
    assert "camps-hero-poster.webp" in body
    assert "data-camp-hero-video" in body
    assert "autoplay muted loop playsinline" in body
    assert 'fetchpriority="high"' in body
    assert "camp-court-art" not in body
    assert "Больше тенниса. Ближе друг к другу." in body


def test_camps_guest_submission_is_private_and_idempotent(
    client: Client, camp_session: CampSession
) -> None:
    """Двойной POST даёт одну заявку, доступную только подавшему браузеру."""
    payload = _payload(client, camp_session)
    url = reverse("camps:apply", args=[camp_session.pk])
    first = client.post(url, payload)
    second = client.post(url, payload)
    assert first.status_code == second.status_code == 302
    assert first.url == second.url
    assert CampApplication.objects.count() == CampParticipant.objects.count() == 1
    app = CampApplication.objects.get()
    assert app.user_id is None
    assert app.phone == "+79990000000"
    assert client.get(first.url).status_code == 200
    assert Client().get(first.url).status_code == 404
    assert camp_session.confirmed_players == 0


def test_camps_email_does_not_claim_existing_account(
    client: Client, camp_session: CampSession
) -> None:
    """Совпадение email не привязывает гостевую заявку к чужому аккаунту."""
    user = User.objects.create_user(email="guest@example.test", password="test-only")
    client.post(
        reverse("camps:apply", args=[camp_session.pk]), _payload(client, camp_session)
    )
    other = Client()
    other.force_login(user)
    app = CampApplication.objects.get()
    assert other.get(reverse("camps:application", args=[app.pk])).status_code == 404


def test_camps_invalid_phone_and_participant_show_errors(
    client: Client, camp_session: CampSession
) -> None:
    """Ошибки полей видны, частичная заявка не сохраняется."""
    payload = _payload(client, camp_session)
    payload.update(phone="+", **{"people-0-age": "10"})
    response = client.post(reverse("camps:apply", args=[camp_session.pk]), payload)
    assert response.status_code == 200
    assert "от 7 до 15 цифр" in response.content.decode()
    assert "младше 18" in response.content.decode()
    assert not CampApplication.objects.exists()


def test_camps_requires_consent(client: Client, camp_session: CampSession) -> None:
    """Без согласия форма не создаёт персональные записи."""
    data = _payload(client, camp_session)
    del data["consent"]
    assert (
        client.post(reverse("camps:apply", args=[camp_session.pk]), data).status_code
        == 200
    )
    assert not CampApplication.objects.exists()


def test_camps_reject_package_from_other_session(camp_session: CampSession) -> None:
    """Нельзя подменить пакет и его цену чужим заездом."""
    package = camp_session.packages.first()
    package.is_active = False
    package.save()
    with pytest.raises(ValidationError):
        _submit(camp_session)
    assert not CampApplication.objects.exists()


def test_camps_closing_registration_rejects_open_form(
    client: Client, camp_session: CampSession
) -> None:
    """Закрытие набора между GET и POST предотвращает создание заявки."""
    data = _payload(client, camp_session)
    camp_session.status = "closed"
    camp_session.save()
    response = client.post(reverse("camps:apply", args=[camp_session.pk]), data)
    assert response.status_code == 302
    assert not CampApplication.objects.exists()


def test_camps_confirmation_reserves_whole_group(
    camp_session: CampSession, manager: User
) -> None:
    """Последнее место нельзя продать двум игрокам, лист ожидания мест не занимает."""
    first = _submit(camp_session)
    update_application(application_id=first.pk, actor=manager, values=_values())
    second = _submit(camp_session, count=2)
    with pytest.raises(ValidationError, match="недостаточно"):
        update_application(application_id=second.pk, actor=manager, values=_values())
    second.refresh_from_db()
    assert second.status == "waitlist"
    update_application(
        application_id=second.pk, actor=manager, values=_values("waitlist")
    )
    assert camp_session.confirmed_players == 1


def test_camps_companion_does_not_consume_sports_place(
    camp_session: CampSession, manager: User
) -> None:
    """Сопровождающий хранится в составе, но не уменьшает спортивную вместимость."""
    app = _submit(camp_session)
    CampParticipant.objects.create(
        application=app, name="Сопровождающий", role="companion"
    )
    update_application(application_id=app.pk, actor=manager, values=_values())
    assert camp_session.confirmed_players == 1
    assert app.participants.count() == 2


def test_camps_confirmation_requires_quote_and_permission(
    camp_session: CampSession, manager: User
) -> None:
    """Подтверждение требует согласованной суммы и разрешения сотрудника."""
    app = _submit(camp_session)
    with pytest.raises(ValidationError, match="стоимость"):
        update_application(
            application_id=app.pk, actor=manager, values=_values(quoted_total=None)
        )
    player = User.objects.create_user(
        email="ordinary@example.test", password="test-only"
    )
    with pytest.raises(PermissionDenied):
        update_application(application_id=app.pk, actor=player, values=_values())


def test_camps_cancel_releases_places_without_refund(
    camp_session: CampSession, manager: User
) -> None:
    """Отмена освобождает спортивное место и не выдумывает возврат денег."""
    app = _submit(camp_session)
    update_application(application_id=app.pk, actor=manager, values=_values())
    record_payment(
        application_id=app.pk,
        actor=manager,
        amount=Decimal("5000"),
        reference="Предоплата",
        operation_key=uuid.uuid4(),
    )
    update_application(
        application_id=app.pk, actor=manager, values=_values("cancelled")
    )
    assert camp_session.confirmed_players == 0
    assert app.paid_amount == Decimal("5000")
    with pytest.raises(ValidationError):
        update_application(application_id=app.pk, actor=manager, values=_values())


def test_camps_manual_payment_is_idempotent_and_refund_limited(
    camp_session: CampSession, manager: User
) -> None:
    """Повтор проводки не удваивает оплату, возврат не превышает поступления."""
    app = _submit(camp_session)
    key = uuid.uuid4()
    for _ in range(2):
        record_payment(
            application_id=app.pk,
            actor=manager,
            amount=Decimal("10000"),
            reference="Перевод",
            operation_key=key,
        )
    assert CampPayment.objects.count() == 1
    with pytest.raises(ValidationError):
        record_payment(
            application_id=app.pk,
            actor=manager,
            amount=Decimal("-10001"),
            reference="Возврат",
            operation_key=uuid.uuid4(),
        )
    record_payment(
        application_id=app.pk,
        actor=manager,
        amount=Decimal("-10000"),
        reference="Возврат",
        operation_key=uuid.uuid4(),
    )
    assert app.paid_amount == 0


def test_camps_offer_snapshot_survives_package_change(
    camp_session: CampSession,
) -> None:
    """Редактирование пакета не переписывает условия поданной заявки."""
    app = _submit(camp_session)
    package = app.package
    package.price = Decimal("99999")
    package.name = "Новый пакет"
    package.save()
    app.refresh_from_db()
    assert app.offer_snapshot["package"] == "Спорт"
    assert Decimal(app.offer_snapshot["unit_price"]) == Decimal("45000")


def test_camps_staff_pages_and_private_notes(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Служебные экраны закрыты, внутренние заметки не попадают участнику."""
    guest = Client()
    guest.post(
        reverse("camps:apply", args=[camp_session.pk]), _payload(guest, camp_session)
    )
    app = CampApplication.objects.get()
    update_application(
        application_id=app.pk, actor=manager, values=_values("contacted")
    )
    assert (
        "Секретная заметка"
        not in guest.get(reverse("camps:application", args=[app.pk])).content.decode()
    )
    client.force_login(manager)
    for url in (
        reverse("camps:dashboard"),
        reverse("camps:manage_session", args=[camp_session.pk]),
        reverse("camps:manage_application", args=[app.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 200
    client.force_login(
        User.objects.create_user(email="outsider@example.test", password="test-only")
    )
    assert (
        client.get(reverse("camps:manage_session", args=[camp_session.pk])).status_code
        == 403
    )
    assert (
        client.get(reverse("camps:export", args=[camp_session.pk])).status_code == 403
    )


def test_camps_csv_escapes_formulas(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Выгрузка не исполняет имена как формулы электронной таблицы."""
    app = _submit(camp_session)
    app.participants.update(name="=1+1")
    client.force_login(manager)
    content = client.get(
        reverse("camps:export", args=[camp_session.pk])
    ).content.decode("utf-8-sig")
    assert "'=1+1" in content


def test_camps_friendly_game_does_not_touch_rating(
    camp_session: CampSession, manager: User
) -> None:
    """Запись товарищеской игры сохраняет очки и официальные счётчики."""
    first = make_player(email_suffix="camp-one")
    second = make_player(email_suffix="camp-two")
    app = _submit(camp_session, count=2)
    update_application(application_id=app.pk, actor=manager, values=_values())
    people = list(app.participants.all())
    for person, player in zip(people, (first, second), strict=True):
        person.player = player
        person.save()
    before = (first.total_points, first.matches_played, first.matches_won)
    game = CampGame(
        session=camp_session,
        player1=people[0],
        player2=people[1],
        played_at=timezone.now(),
        score="6:4, 6:3",
    )
    game.full_clean()
    game.save()
    first.refresh_from_db()
    assert (first.total_points, first.matches_played, first.matches_won) == before
    from apps.tournaments.models import Match

    assert not Match.objects.filter(player1=first).exists()


def test_camps_game_rejects_companion_and_self(
    camp_session: CampSession, manager: User
) -> None:
    """Играть могут разные подтверждённые игроки одного заезда."""
    app = _submit(camp_session)
    person = app.participants.get()
    game = CampGame(
        session=camp_session, player1=person, player2=person, played_at=timezone.now()
    )
    with pytest.raises(ValidationError):
        game.full_clean()


def test_camps_notifications_queue_after_commit(
    client: Client,
    camp_session: CampSession,
    manager: User,
    django_capture_on_commit_callbacks: Any,
) -> None:
    """Авторизованный участник получает управляемое событие и очередь email."""
    client.force_login(manager)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("camps:apply", args=[camp_session.pk]),
            _payload(client, camp_session),
        )
    assert response.status_code == 302
    assert Delivery.objects.filter(
        event_key="camp.application_submitted", channel="email", user=manager
    ).exists()


def test_camps_official_tournament_keeps_existing_identity(
    camp_session: CampSession,
) -> None:
    """Кемп ссылается на существующий турнир без копии матчей."""
    tournament = make_tournament(slug="camp-official")
    link = CampTournament.objects.create(session=camp_session, tournament=tournament)
    assert link.tournament_id == tournament.pk
    assert tournament.camp_link.session_id == camp_session.pk


def test_camps_zero_and_unknown_prices_are_different(camp_session: CampSession) -> None:
    """Бесплатный пакет и цена по запросу не смешиваются."""
    package = camp_session.packages.first()
    package.price = Decimal("0")
    assert package.price_label == "0 ₽"
    package.price = None
    assert package.price_label == "Стоимость по запросу"


def test_camps_guest_can_read_games_only_after_confirmation(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Гостевая сессия видит журнал только своего подтверждённого заезда."""
    client.post(
        reverse("camps:apply", args=[camp_session.pk]), _payload(client, camp_session)
    )
    app = CampApplication.objects.get()
    url = reverse("camps:games", args=[camp_session.pk])
    assert client.get(url).status_code == 403
    update_application(application_id=app.pk, actor=manager, values=_values())
    assert client.get(url).status_code == 200
    assert Client().get(url).status_code == 403


def test_camps_child_age_and_companion_only_are_rejected(
    client: Client, camp_session: CampSession
) -> None:
    """Детская роль не обходит взрослый допуск, сопровождающий не едет без игрока."""
    data = _payload(client, camp_session)
    data.update({"people-0-role": "child", "people-0-age": "12"})
    assert (
        client.post(reverse("camps:apply", args=[camp_session.pk]), data).status_code
        == 200
    )
    assert not CampApplication.objects.exists()
    data.update({"people-0-role": "companion", "people-0-age": "30"})
    response = client.post(reverse("camps:apply", args=[camp_session.pk]), data)
    assert "хотя бы одного игрока" in response.content.decode()
    assert not CampApplication.objects.exists()


def test_camps_staff_post_records_quote_and_payment(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Рабочие формы подтверждают заявку и принимают ручную проводку."""
    app = _submit(camp_session)
    client.force_login(manager)
    url = reverse("camps:manage_application", args=[app.pk])
    response = client.post(
        url,
        {
            "action": "update",
            "status": "confirmed",
            "quoted_total": "90000",
            "staff_notes": "Только команда",
        },
    )
    assert response.status_code == 302
    app.refresh_from_db()
    assert app.status == "confirmed"
    client.get(url)
    key = client.session[f"camp_payment_key_{app.pk}"]
    response = client.post(
        url,
        {
            "action": "payment",
            "amount": "10000",
            "reference": "Синтетическое поступление",
            "operation_key": key,
        },
    )
    assert response.status_code == 302
    assert app.paid_amount == Decimal("10000")
    assert app.balance == Decimal("80000")


def test_camps_guest_email_respects_event_policy(
    client: Client, camp_session: CampSession, django_capture_on_commit_callbacks: Any
) -> None:
    """Гостевое письмо отправляется после транзакции через политику события."""
    from django.core import mail

    from apps.notifications.models import EventPolicy

    data = _payload(client, camp_session)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("camps:apply", args=[camp_session.pk]), data)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["guest@example.test"]
    assert "Место забронировано" not in mail.outbox[0].body
    EventPolicy.objects.filter(key="camp.application_updated").update(
        email_enabled=False
    )


def test_camps_participant_admin_keeps_capacity_immutable(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Обычная админка не позволяет поменять роль или удалить спортивное место."""
    app = _submit(camp_session)
    client.force_login(manager)
    person = app.participants.get()
    response = client.get(
        reverse("admin:camps_campparticipant_change", args=[person.pk])
    )
    assert response.status_code == 200
    assert 'name="role"' not in response.content.decode()
    response = client.get(reverse("admin:camps_campapplication_change", args=[app.pk]))
    assert 'name="status"' not in response.content.decode()


def test_camps_session_capacity_cannot_drop_below_confirmed(
    camp_session: CampSession, manager: User
) -> None:
    """Редактирование заезда не уменьшает лимит ниже занятого состава."""
    app = _submit(camp_session, count=2)
    update_application(application_id=app.pk, actor=manager, values=_values())
    camp_session.capacity = 1
    with pytest.raises(ValidationError):
        camp_session.full_clean()


def test_camps_price_preserves_fractional_amount(camp_session: CampSession) -> None:
    """Цена пакета не округляется с потерей денежных единиц."""
    package = camp_session.packages.first()
    package.price = Decimal("45000.50")
    assert package.price_label == "45 000,50 ₽"


def test_camps_no_application_without_active_package(
    client: Client, camp_session: CampSession
) -> None:
    """Заезд без доступных пакетов не предлагает неработающую форму."""
    camp_session.packages.update(is_active=False)
    assert not camp_session.accepts_applications
    assert client.get(reverse("camps:apply", args=[camp_session.pk])).status_code == 302
