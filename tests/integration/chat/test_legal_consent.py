"""Согласия городского чата: доказательства, версии и запрет обхода формы."""

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse
from pytest_mock import MockerFixture

from apps.chat import services
from apps.chat.consent import accept_chat_consent, build_consent_form
from apps.chat.domain import ChatError
from apps.chat.models import ChatMembership
from apps.core.models import City, LegalAcceptanceLog
from apps.legal.utils import LEGAL_DOCUMENT_PATHS
from apps.users.models import Player, User
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def _submission(user: User, city: City, **changes: str) -> dict[str, str]:
    form = build_consent_form(user=user, city=city)
    return {
        "city_id": str(city.pk),
        "token": str(form["token"].value()),
        "personal_data": "on",
        "rules": "on",
        "disclosure": "on",
        **changes,
    }


def _accept(user: User, city: City, data: dict[str, str]) -> ChatMembership:
    return accept_chat_consent(
        user=user,
        city=city,
        data=data,
        ip_address="127.0.0.1",
        user_agent="Test browser",
    )


@pytest.mark.parametrize(
    "slug", ["chat-rules", "chat-personal-data", "chat-disclosure"]
)
def test_document_when_opened_should_be_public_and_versioned(
    client: Client, slug: str
) -> None:
    """Все три текста доступны гостям и не являются заглушками."""
    response = client.get(f"/legal/{slug}/")
    assert response.status_code == 200
    assert "Ермолаев Леонид" in response.content.decode()
    assert len(LEGAL_DOCUMENT_PATHS[slug].read_text()) > 1000


def test_join_form_when_opened_should_have_three_unchecked_required_consents(
    client: Client,
) -> None:
    """Открытие формы не вступает в чат и не создаёт фиктивных согласий."""
    user = make_user(email="legal-form@test.local")
    city = City.objects.create(name="Город формы согласий")
    client.force_login(user)
    response = client.get(reverse("chat:join"), {"city_id": city.pk})
    assert response.status_code == 200
    form = response.context["consent_form"]
    for name in ("personal_data", "rules", "disclosure"):
        assert form.fields[name].required
        assert not form[name].value()
    assert "мои сообщения" in form.fields["disclosure"].label
    assert "выбранных ниже" not in form.fields["disclosure"].label
    for name in ("show_name", "show_avatar", "conditions"):
        assert not form.fields[name].required
    assert "Имя и фото — необязательно" in response.content.decode()
    assert not ChatMembership.objects.filter(user=user).exists()
    assert not LegalAcceptanceLog.objects.filter(user=user).exists()


@pytest.mark.parametrize("field", ["personal_data", "rules", "disclosure", "token"])
@pytest.mark.parametrize("value", ["", "false", "0"])
def test_join_when_required_confirmation_missing_should_not_create_membership(
    field: str, value: str
) -> None:
    """Сервер не доверяет состоянию кнопки и HTML-валидации браузера."""
    user = make_user(email="legal-missing@test.local")
    city = City.objects.create(name="Город обязательных согласий")
    with pytest.raises(ChatError):
        _accept(user, city, _submission(user, city, **{field: value}))
    assert not ChatMembership.objects.filter(user=user).exists()
    assert not LegalAcceptanceLog.objects.filter(user=user).exists()


def test_join_when_confirmed_should_atomically_store_three_document_snapshots() -> None:
    """Доказательства содержат содержание, версии, субъекта и контекст города."""
    user = make_user(email="legal-proof@test.local", first_name="Анна")
    city = City.objects.create(name="Город доказательств")
    data = _submission(user, city, show_name="on")
    member = _accept(user, city, data)
    logs = list(LegalAcceptanceLog.objects.filter(user=user, source="chat_join"))
    assert {log.document_slug for log in logs} == {
        "chat-rules",
        "chat-personal-data",
        "chat-disclosure",
    }
    assert len(logs) == 3
    assert member.consented_at and member.consent_version
    for log in logs:
        assert log.ip_address == "127.0.0.1"
        assert log.user_agent == "Test browser"
        assert log.metadata["room_id"] == member.room_id
        assert log.metadata["city_id"] == city.pk
        assert log.metadata["subject"]["email"] == user.email
        assert log.metadata["checkbox_text"]
        assert (
            log.metadata["document_sha256"]
            == sha256(log.metadata["document_text"].encode()).hexdigest()
        )
        assert log.metadata["privacy"]["document_text"]
    _accept(user, city, data)
    assert LegalAcceptanceLog.objects.filter(user=user).count() == 3


def test_join_when_document_changed_should_reject_stale_confirmation(
    mocker: MockerFixture,
) -> None:
    """Нельзя подтвердить иную редакцию, чем была показана игроку."""
    from apps.chat import consent

    user = make_user(email="legal-stale@test.local")
    city = City.objects.create(name="Город редакций")
    data = _submission(user, city)
    original = consent.load_consent_documents
    snapshots = original()
    snapshots["chat-rules"] = {
        **snapshots["chat-rules"],
        "document_text": "Изменённая редакция",
    }
    mocker.patch.object(consent, "load_consent_documents", return_value=snapshots)
    with pytest.raises(ChatError):
        _accept(user, city, data)
    assert not LegalAcceptanceLog.objects.filter(user=user).exists()


def test_join_when_token_belongs_to_other_subject_or_city_should_reject() -> None:
    """Подпись связана с аккаунтом и конкретным населённым пунктом."""
    user = make_user(email="legal-owner@test.local")
    other = make_user(email="legal-other@test.local")
    city = City.objects.create(name="Первый город согласий")
    second = City.objects.create(name="Второй город согласий")
    data = _submission(user, city)
    for subject, locality in ((other, city), (user, second)):
        with pytest.raises(ChatError):
            _accept(subject, locality, data)


def test_join_when_audit_write_fails_should_roll_back_membership(
    mocker: MockerFixture,
) -> None:
    """Ошибка журнала не оставляет участника без доказательств согласия."""
    user = make_user(email="legal-atomic@test.local")
    city = City.objects.create(name="Город транзакций согласий")
    data = _submission(user, city)
    mocker.patch.object(
        LegalAcceptanceLog.objects, "create", side_effect=IntegrityError("test failure")
    )
    with pytest.raises(IntegrityError):
        _accept(user, city, data)
    assert not ChatMembership.objects.filter(user=user).exists()


def test_access_when_admin_added_without_consent_should_require_confirmation(
    client: Client,
) -> None:
    """Ручное участие не означает согласия игрока; API также закрыто."""
    user = make_user(email="legal-admin-added@test.local")
    city = City.objects.create(name="Город ручного участия")
    room = services.join_city(user=user, city_id=city.pk)
    client.force_login(user)
    response = client.get(reverse("chat:room", args=[room.pk]))
    assert response.status_code == 302
    assert "join/" in response.headers["Location"]
    for route in ("history", "sync"):
        assert client.get(reverse(f"chat:{route}", args=[room.pk])).status_code == 403
    with pytest.raises(ChatError):
        services.send_message(
            user=user, room_id=room.pk, text="Обход", client_id=uuid4()
        )


def test_join_when_custom_conditions_requested_should_hold_access_for_operator_review(
    client: Client,
) -> None:
    """Особые условия сохраняются, но данные не публикуются до их рассмотрения."""
    user = make_user(email="legal-conditions@test.local")
    city = City.objects.create(name="Город условий")
    member = _accept(
        user, city, _submission(user, city, conditions="Запрет обработки для рекламы")
    )
    assert not member.disclosure_reviewed
    with pytest.raises(ChatError):
        services.require_member(user=user, room_id=member.room_id)
    public = client.get(reverse("chat:data_conditions", args=[member.room_id]))
    assert public.status_code == 200
    assert "Запрет обработки для рекламы" in public.content.decode()
    assert user.email not in public.content.decode()
    member.disclosure_reviewed = True
    member.save(update_fields=["disclosure_reviewed"])
    assert services.require_member(user=user, room_id=member.room_id).pk == member.pk


def test_disclosure_when_fields_not_selected_should_hide_name_photo_and_profile_link() -> (
    None
):
    """Отказ от показа имени и фото не мешает общению под обозначением участника."""
    user = make_user(email="legal-minimal@test.local", first_name="Секретное имя")
    city = City.objects.create(name="Город минимизации")
    member = _accept(user, city, _submission(user, city))
    message = services.send_message(
        user=user, room_id=member.room_id, text="Привет", client_id=uuid4()
    )
    payload = services.serialize_message(message)
    assert payload["author"] == f"Участник #{user.pk}"
    assert payload["avatar"] == payload["profile_url"] == ""
    assert payload["conditions_url"]


def test_withdrawal_when_requested_should_close_access_hide_messages_and_keep_audit(
    client: Client,
) -> None:
    """Отзыв не подменяется обычным выходом и прекращает дальнейший показ."""
    user = make_user(email="legal-withdraw@test.local", first_name="Анна")
    city = City.objects.create(name="Город отзыва")
    data = _submission(user, city, show_name="on")
    member = _accept(user, city, data)
    message = services.send_message(
        user=user, room_id=member.room_id, text="Скрыть после отзыва", client_id=uuid4()
    )
    client.force_login(user)
    response = client.post(reverse("chat:withdraw_consent", args=[member.room_id]))
    assert response.status_code == 302
    assert (
        LegalAcceptanceLog.objects.filter(user=user, source="chat_withdrawal").count()
        == 1
    )
    assert services.serialize_message(message)["text"] == ""
    with pytest.raises(ChatError):
        _accept(user, city, data)
    assert LegalAcceptanceLog.objects.filter(user=user, source="chat_join").count() == 3


def test_audit_when_account_deleted_should_preserve_subject_and_document_evidence() -> (
    None
):
    """Удаление аккаунта не стирает факт ранее данного согласия."""
    user = make_user(email="legal-delete@test.local")
    city = City.objects.create(name="Город журнала")
    _accept(user, city, _submission(user, city))
    ids = list(
        LegalAcceptanceLog.objects.filter(user=user).values_list("pk", flat=True)
    )
    email = user.email
    user.delete()
    records = LegalAcceptanceLog.objects.filter(pk__in=ids)
    assert records.count() == 3
    assert all(
        log.user_id is None and log.metadata["subject"]["email"] == email
        for log in records
    )


def test_links_when_directory_and_footer_opened_should_include_rules(
    client: Client,
) -> None:
    """Правила доступны в основном подвале и в каталоге чатов."""
    user = make_user(email="legal-links@test.local")
    client.force_login(user)
    for url in ("/legal/", reverse("chat:index")):
        response = client.get(url)
        assert 'href="/legal/chat-rules/"' in response.content.decode()


def test_join_when_csrf_missing_should_reject_even_with_valid_signed_token() -> None:
    """Подписанная форма не заменяет CSRF-защиту."""
    user = make_user(email="legal-csrf@test.local")
    city = City.objects.create(name="Город CSRF согласий")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    assert client.post(reverse("chat:join"), _submission(user, city)).status_code == 403


def test_join_when_signed_form_expired_should_require_new_confirmation(
    mocker: MockerFixture,
) -> None:
    """Истёкшее подтверждение нельзя превратить в новый акцепт."""
    from time import time

    from apps.chat.consent import CONSENT_MAX_AGE

    user = make_user(email="legal-expired@test.local")
    city = City.objects.create(name="Город срока формы")
    data = _submission(user, city)
    mocker.patch(
        "django.core.signing.time.time", return_value=time() + CONSENT_MAX_AGE + 1
    )
    with pytest.raises(ChatError):
        _accept(user, city, data)


def test_join_when_document_missing_should_fail_closed(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Недоступный документ не заменяется неизвестной редакцией."""
    user = make_user(email="legal-unavailable@test.local")
    city = City.objects.create(name="Город недоступного документа")
    data = _submission(user, city)
    mocker.patch.dict(
        LEGAL_DOCUMENT_PATHS, {"chat-rules": Path(str(tmp_path)) / "missing.txt"}
    )
    with pytest.raises(ChatError):
        _accept(user, city, data)
    assert not LegalAcceptanceLog.objects.filter(user=user).exists()


def test_join_when_replayed_after_leave_should_not_reactivate_old_confirmation() -> (
    None
):
    """Повторная доставка старого POST после выхода не означает новое вступление."""
    user = make_user(email="legal-left-replay@test.local")
    city = City.objects.create(name="Город старой формы")
    data = _submission(user, city)
    member = _accept(user, city, data)
    services.leave_room(user=user, room_id=member.room_id)
    with pytest.raises(ChatError):
        _accept(user, city, data)
    assert not ChatMembership.objects.get(pk=member.pk).is_active


def test_withdrawal_when_old_messages_and_quotes_loaded_should_emit_redacted_events() -> (
    None
):
    """Отзыв обновляет уже загруженные сообщения и чужие цитаты, а не только историю."""
    from apps.chat.consent import withdraw_chat_consent

    author = make_user(email="legal-live-author@test.local")
    reader = make_user(email="legal-live-reader@test.local")
    city = City.objects.create(name="Город отзыва цитат")
    member = _accept(author, city, _submission(author, city, show_name="on"))
    _accept(reader, city, _submission(reader, city))
    original = services.send_message(
        user=author, room_id=member.room_id, text="Личное сообщение", client_id=uuid4()
    )
    reply = services.send_message(
        user=reader,
        room_id=member.room_id,
        text="Ответ",
        reply_to_id=original.pk,
        client_id=uuid4(),
    )
    cursor = services.history(user=reader, room_id=member.room_id)["cursor"]
    withdraw_chat_consent(
        user=author, room_id=member.room_id, ip_address=None, user_agent="Test"
    )
    events = services.sync_events(user=reader, room_id=member.room_id, after_id=cursor)[
        "events"
    ]
    updates = {event["message"]["id"]: event["message"] for event in events}
    assert updates[original.pk]["text"] == ""
    assert updates[reply.pk]["reply_to"]["text"] == "Сообщение недоступно"


def test_admin_when_audit_opened_after_account_deletion_should_display_and_export_evidence(
    client: Client,
) -> None:
    """Журнал в Ядре доступен после удаления аккаунта, включая полный экспорт."""
    user = make_user(email="legal-admin-proof@test.local", first_name="=FORMULA()")
    city = City.objects.create(name="Город проверки журнала")
    _accept(user, city, _submission(user, city))
    record = LegalAcceptanceLog.objects.filter(user=user).first()
    assert record is not None
    user.delete()
    client.force_login(
        make_user(email="legal-operator@test.local", is_staff=True, is_superuser=True)
    )
    detail = client.get(
        reverse("admin:core_legalacceptancelog_change", args=[record.pk])
    )
    assert detail.status_code == 200
    assert "Сохранённый текст документа" in detail.content.decode()
    exported = client.post(
        reverse("admin:core_legalacceptancelog_changelist"),
        {"action": "export_as_csv", "_selected_action": [record.pk]},
    )
    assert exported.status_code == 200
    assert "legal-admin-proof@test.local" in exported.content.decode()
    assert "'=FORMULA()" in exported.content.decode()


def test_admin_when_membership_user_forged_should_not_transfer_personal_consent(
    client: Client,
) -> None:
    """Нельзя перенести принятые документы на другого игрока через редактирование участия."""
    user = make_user(email="legal-owner-admin@test.local")
    other = make_user(email="legal-victim-admin@test.local")
    city = City.objects.create(name="Город неизменного субъекта")
    member = _accept(user, city, _submission(user, city))
    client.force_login(
        make_user(email="legal-root-admin@test.local", is_staff=True, is_superuser=True)
    )
    url = (
        reverse("admin:chat_chatmembership_change", args=[member.pk])
        + f"?room_id={member.room_id}"
    )
    response = client.post(
        url,
        {
            "user": other.pk,
            "room": member.room_id,
            "is_active": "on",
            "notifications_enabled": "on",
            "_save": "1",
        },
    )
    assert response.status_code == 302
    member.refresh_from_db()
    assert member.user_id == user.pk
    assert not LegalAcceptanceLog.objects.filter(user=other).exists()


def test_join_when_another_city_selected_should_require_independent_confirmations() -> (
    None
):
    """Согласие на одну комнату не переносится в другую."""
    user = make_user(email="legal-two-cities@test.local")
    first = City.objects.create(name="Первая независимая комната")
    second = City.objects.create(name="Вторая независимая комната")
    _accept(user, first, _submission(user, first))
    room = services.join_city(user=user, city_id=second.pk)
    with pytest.raises(ChatError):
        services.require_member(user=user, room_id=room.pk)
    _accept(user, second, _submission(user, second))
    assert LegalAcceptanceLog.objects.filter(user=user, source="chat_join").count() == 6


@pytest.mark.django_db(transaction=True)
def test_join_when_identical_confirmations_concurrent_should_write_only_three_logs() -> (
    None
):
    """Параллельная доставка одной формы не дублирует доказательства."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection

    if connection.vendor != "postgresql":
        pytest.skip("Нужны блокировки PostgreSQL")
    user = make_user(email="legal-concurrent@test.local")
    city = City.objects.create(name="Город параллельных подтверждений")
    data = _submission(user, city)
    barrier = Barrier(2)

    def submit() -> int:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return int(_accept(user, city, data).pk)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1]
    assert LegalAcceptanceLog.objects.filter(user=user, source="chat_join").count() == 3


def test_conditions_when_untrusted_markup_supplied_should_escape_public_text(
    client: Client,
) -> None:
    """Публичные условия не исполняют присланный пользователем HTML."""
    user = make_user(email="legal-condition-xss@test.local")
    city = City.objects.create(name="Город безопасных условий")
    member = _accept(
        user, city, _submission(user, city, conditions='<script>alert("x")</script>')
    )
    html = client.get(
        reverse("chat:data_conditions", args=[member.room_id])
    ).content.decode()
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html


def test_admin_when_conditions_reviewed_should_record_operator_action_separately(
    client: Client,
) -> None:
    """Решение оператора открывает доступ, но не считается новым согласием игрока."""
    user = make_user(email="legal-pending-admin@test.local")
    operator = make_user(
        email="legal-conditions-operator@test.local", is_staff=True, is_superuser=True
    )
    city = City.objects.create(name="Город решения оператора")
    member = _accept(
        user,
        city,
        _submission(user, city, conditions="Запрет на использование для рекламы"),
    )
    client.force_login(operator)
    url = (
        reverse("admin:chat_chatmembership_change", args=[member.pk])
        + f"?room_id={member.room_id}"
    )
    response = client.post(
        url,
        {
            "user": user.pk,
            "room": member.room_id,
            "is_active": "on",
            "notifications_enabled": "on",
            "disclosure_reviewed": "on",
            "_save": "1",
        },
    )
    assert response.status_code == 302
    review = LegalAcceptanceLog.objects.get(user=user, source="chat_conditions_review")
    assert review.metadata["operator_user_id"] == operator.pk
    assert review.metadata["conditions"] == "Запрет на использование для рекламы"
    assert LegalAcceptanceLog.objects.filter(user=user, source="chat_join").count() == 3
    assert services.require_member(user=user, room_id=member.room_id).pk == member.pk


def test_audit_when_subject_changed_email_should_export_original_identity() -> None:
    """Отчёт показывает субъекта на момент согласия, а не новый профиль."""
    import csv
    from io import StringIO

    from django.contrib import admin
    from django.test import RequestFactory

    from apps.core.legal_admin import LegalAcceptanceLogAdmin

    user = make_user(email="legal-original@test.local", first_name="Исходное имя")
    city = City.objects.create(name="Город исходного субъекта")
    _accept(user, city, _submission(user, city))
    User.objects.filter(pk=user.pk).update(
        email="legal-new@test.local", first_name="Новое имя"
    )
    model_admin = LegalAcceptanceLogAdmin(LegalAcceptanceLog, admin.site)
    response = model_admin.export_as_csv(
        RequestFactory().get("/"), LegalAcceptanceLog.objects.filter(user=user)
    )
    rows = list(csv.DictReader(StringIO(response.content.decode())))
    assert len(rows) == 3
    assert all(row["user_email"] == "legal-original@test.local" for row in rows)
    assert all(row["user_name"] == "Исходное имя" for row in rows)


@pytest.mark.parametrize("show_avatar", [False, True])
@pytest.mark.parametrize("has_avatar", [False, True])
def test_photo_when_choice_recorded_should_publish_only_permitted_profile_photo(
    show_avatar: bool, has_avatar: bool
) -> None:
    """Фото публикуется только по выбору игрока, независимо от раскрытия имени.

    Args:
        show_avatar: Отдельное разрешение на показ фотографии.
        has_avatar: Наличие фотографии в профиле игрока.
    """
    user = make_user(email="chat-photo@test.local", first_name="Анна")
    player = Player.objects.create(user=user)
    # Проверяем сериализацию пути, не загрузку и преобразование файла.
    Player.objects.filter(pk=player.pk).update(
        avatar="avatars/chat-photo.jpg" if has_avatar else ""
    )
    player.refresh_from_db()
    city = City.objects.create(name="Город фотографии")
    member = _accept(
        user, city, _submission(user, city, show_avatar="on" if show_avatar else "")
    )
    message = services.send_message(
        user=user, room_id=member.room_id, text="Фото по выбору", client_id=uuid4()
    )
    payload = services.serialize_message(message)
    assert payload["avatar"] == (
        player.avatar.url if show_avatar and has_avatar else ""
    )
    assert payload["author"] == f"Участник #{user.pk}"
    assert payload["profile_url"] == ""
    assert member.disclosure_reviewed
