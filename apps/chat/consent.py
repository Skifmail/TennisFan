"""Личные согласия на чат: подписанная форма, снимки документов и отзыв."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Mapping
from typing import Any, Final, TypedDict, cast
from uuid import UUID, uuid4

from django import forms
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone
from loguru import logger

from apps.core.models import City, LegalAcceptanceLog
from apps.legal.utils import LEGAL_DOCUMENT_PATHS, LEGAL_VERSION_LENGTH
from apps.users.models import User

from .domain import (
    ChatConsentDocumentError,
    ChatConsentRequiredError,
    ChatConsentVersionError,
    ChatNotFoundError,
    ChatValidationError,
)
from .models import ChatEvent, ChatMembership, ChatMessage, ChatRoom
from .realtime import publish_room_change

CONSENT_SOURCE: Final = "chat_join"
CONSENT_SALT: Final = "city-chat-consent-v1"
CONSENT_MAX_AGE: Final = 1800
MAX_CONDITIONS_LENGTH: Final = 1000
MAX_USER_AGENT_LENGTH: Final = 2000
MAX_SIGNED_FORM_LENGTH: Final = 4096
DISCLOSURE_BATCH_SIZE: Final = 200
CONSENT_DOCUMENTS: Final = {
    "personal_data": "chat-personal-data",
    "rules": "chat-rules",
    "disclosure": "chat-disclosure",
}
CONSENT_LABELS: Final = {
    "personal_data": "Даю согласие на обработку персональных данных для работы городского чата, включая показ моих сообщений его участникам.",
    "rules": "Я прочитал(а) правила городских чатов и обязуюсь их соблюдать.",
    "disclosure": "Даю дополнительное согласие на показ выбранных ниже имени и фамилии и/или фотографии участникам этого городского чата.",
}
BASE_DISCLOSURE_CONDITIONS: Final = (
    "Доступ только участникам выбранного чата. Запрещены дальнейшая передача "
    "и обработка другими лицами, кроме получения доступа, если иное не предусмотрено законом."
)


class DocumentSnapshot(TypedDict):
    """Снимок конкретной редакции документа.

    Attributes:
        document_text: Полный текст, предъявленный пользователю.
        document_sha256: Контрольная сумма полного текста.
        version: Короткий идентификатор редакции, совместимый с журналом платформы.
    """

    document_text: str
    document_sha256: str
    version: str


class ChatConsentForm(forms.Form):
    """Собирает независимые волеизъявления без предварительных отметок.

    Attributes:
        token: Подписанный контекст аккаунта, города и редакций.
        conditions: Дополнительные условия для рассмотрения оператором.
    """

    token = forms.CharField(widget=forms.HiddenInput, max_length=MAX_SIGNED_FORM_LENGTH)
    personal_data = forms.BooleanField(label=CONSENT_LABELS["personal_data"])
    rules = forms.BooleanField(label=CONSENT_LABELS["rules"])
    disclosure = forms.BooleanField(
        required=False,
        label=CONSENT_LABELS["disclosure"],
        widget=forms.CheckboxInput(attrs={"aria-controls": "chat-disclosure-options"}),
    )
    show_name = forms.BooleanField(
        required=False, label="Показывать имя и фамилию из моего профиля"
    )
    show_avatar = forms.BooleanField(
        required=False, label="Показывать фотографию из моего профиля"
    )
    conditions = forms.CharField(
        required=False,
        max_length=MAX_CONDITIONS_LENGTH,
        label="Мои дополнительные условия и запреты",
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": MAX_CONDITIONS_LENGTH}),
    )

    def clean(self) -> dict[str, Any]:
        """Принимает только явные значения независимых чекбоксов.

        Returns:
            Проверенные поля формы.
        """
        cleaned = super().clean() or {}
        for name in ("personal_data", "rules"):
            if self.data.get(name) != "on":
                self.add_error(name, "Необходимо отдельное подтверждение.")
        for name in ("disclosure", "show_name", "show_avatar"):
            if self.data.get(name) not in (None, "", "on"):
                self.add_error(name, "Некорректное значение выбора данных.")
        disclosure = cleaned.get("disclosure", False)
        selected = cleaned.get("show_name", False) or cleaned.get("show_avatar", False)
        if disclosure and not selected:
            self.add_error("disclosure", "Выберите имя и фамилию и/или фотографию.")
        if not disclosure and (selected or cleaned.get("conditions")):
            self.add_error(
                "disclosure", "Для показа данных необходимо дополнительное согласие."
            )
        return cleaned


def load_consent_documents() -> dict[str, DocumentSnapshot]:
    """Читает обязательные документы и политику без подстановки заглушек.

    Returns:
        Полные снимки актуальных текстов.

    Raises:
        ChatConsentDocumentError: Хотя бы один текст отсутствует или повреждён.
    """
    snapshots: dict[str, DocumentSnapshot] = {}
    for slug in (*CONSENT_DOCUMENTS.values(), "privacy"):
        try:
            content = LEGAL_DOCUMENT_PATHS[slug].read_text(encoding="utf-8")
        except (KeyError, OSError, UnicodeError) as exc:
            logger.error("Документ чата недоступен", document_slug=slug)
            raise ChatConsentDocumentError(
                "Документы временно недоступны. Попробуйте позже."
            ) from exc
        if not content.strip():
            raise ChatConsentDocumentError("Обязательный документ пуст.")
        digest = hashlib.sha256(content.encode()).hexdigest()
        snapshots[slug] = {
            "document_text": content,
            "document_sha256": digest,
            "version": digest[:LEGAL_VERSION_LENGTH],
        }
    return snapshots


def _bundle_version(documents: dict[str, DocumentSnapshot]) -> str:
    # Повторно вычисляем сумму из текста: снимок и версия не могут разойтись.
    return hashlib.sha256(
        json.dumps(
            {key: value["document_text"] for key, value in documents.items()},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _subject(user: User) -> dict[str, str | int]:
    return {"id": user.pk, "name": user.get_full_name(), "email": user.email}


def build_consent_form(*, user: User, city: City) -> ChatConsentForm:
    """Готовит новую незаполненную форму для конкретного игрока и города.

    Args:
        user: Авторизованный игрок.
        city: Выбранный населённый пункт.

    Returns:
        Форма с подписью редакций и одноразовым идентификатором подтверждения.

    Raises:
        ChatConsentDocumentError: Не удалось загрузить обязательные документы.
    """
    token = signing.dumps(
        {
            "subject": _subject(user),
            "city_id": city.pk,
            "version": _bundle_version(load_consent_documents()),
            "nonce": str(uuid4()),
        },
        salt=CONSENT_SALT,
        compress=True,
    )
    return ChatConsentForm(initial={"token": token})


def ensure_chat_consent(member: ChatMembership) -> None:
    """Проверяет личное согласие, его редакцию и исполнение особых условий.

    Args:
        member: Участие, загруженное сервисом доступа.

    Raises:
        ChatConsentRequiredError: Требуются подтверждения или проверка условий.
        ChatConsentDocumentError: Документы временно недоступны.
    """
    if (
        not member.consented_at
        or member.consent_withdrawn_at
        or member.consent_version != _bundle_version(load_consent_documents())
    ):
        raise ChatConsentRequiredError(
            "Подтвердите действующие документы городского чата."
        )
    if member.disclosure_conditions and not member.disclosure_reviewed:
        raise ChatConsentRequiredError(
            "Ваши особые условия ожидают рассмотрения оператором. Связь: tennis@tennisfan.ru."
        )


def consent_request_ip(request: HttpRequest) -> str | None:
    """Фиксирует адрес соединения, не доверяя произвольному X-Forwarded-For.

    Args:
        request: Запрос согласия.

    Returns:
        Нормализованный адрес узла соединения либо None. За прокси это адрес прокси.
    """
    raw = request.META.get("REMOTE_ADDR", "")
    if "%" in raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


@transaction.atomic
def accept_chat_consent(
    *,
    user: User,
    city: City,
    data: Mapping[str, str],
    ip_address: str | None,
    user_agent: str,
) -> ChatMembership:
    """Атомарно фиксирует выданные согласия и участие без дублей повторного запроса.

    Args:
        user: Игрок, самостоятельно подтверждающий документы.
        city: Город, связанный с подписанной формой.
        data: Поля отправленной формы.
        ip_address: Проверенный адрес соединения.
        user_agent: Ограниченное по размеру описание браузера.

    Returns:
        Участие с выбранными условиями распространения.

    Raises:
        ChatValidationError: Отсутствуют независимые подтверждения.
        ChatConsentVersionError: Подпись, аккаунт, город или редакции не совпадают.
        ChatConsentDocumentError: Не удалось загрузить документы.
        ChatAccessError: Аккаунт неактивен.
        ChatNotFoundError: Город или комната недоступны.
    """
    from .services import join_city, require_active_user

    require_active_user(user)
    form = ChatConsentForm(data=data)
    if not form.is_valid():
        raise ChatValidationError(
            "Подтвердите обработку данных и правила. При дополнительном согласии выберите имя и фамилию и/или фото."
        )
    documents = load_consent_documents()
    version = _bundle_version(documents)
    try:
        payload = signing.loads(
            form.cleaned_data["token"], salt=CONSENT_SALT, max_age=CONSENT_MAX_AGE
        )
        nonce = UUID(payload["nonce"])
        matches = (
            payload["subject"] == _subject(user)
            and payload["city_id"] == city.pk
            and payload["version"] == version
        )
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ChatConsentVersionError(
            "Форма устарела. Откройте её заново и ознакомьтесь с документами."
        ) from exc
    if not matches:
        raise ChatConsentVersionError(
            "Документы или данные изменились. Подтвердите актуальную форму."
        )
    choices = {
        "show_name": form.cleaned_data["show_name"],
        "show_avatar": form.cleaned_data["show_avatar"],
        "conditions": form.cleaned_data["conditions"],
    }
    # Общий с отправкой порядок блокировок: сначала комната, затем аккаунт.
    room, _ = ChatRoom.objects.get_or_create(city=city)
    room = ChatRoom.objects.select_for_update().get(pk=room.pk)
    if not room.is_active:
        raise ChatNotFoundError("Чат закрыт.")
    user = User.objects.select_for_update(no_key=True).get(pk=user.pk)
    require_active_user(user)
    if payload["subject"] != _subject(user):
        raise ChatConsentVersionError(
            "Данные аккаунта изменились. Откройте форму заново."
        )
    previous = LegalAcceptanceLog.objects.filter(
        user=user, source=CONSENT_SOURCE, metadata__acceptance_id=str(nonce)
    ).first()
    if previous is not None:
        member = ChatMembership.objects.filter(
            user=user,
            room__city=city,
            is_active=True,
            consent_id=nonce,
            consent_withdrawn_at__isnull=True,
        ).first()
        if member is None or previous.metadata["disclosure"] != choices:
            raise ChatConsentVersionError(
                "Это подтверждение уже использовано. Откройте новую форму."
            )
        return cast(ChatMembership, member)
    join_city(user=user, city_id=city.pk)
    member = ChatMembership.objects.select_for_update().get(user=user, room=room)
    for field, slug in CONSENT_DOCUMENTS.items():
        if not form.cleaned_data[field]:
            continue
        snapshot = documents[slug]
        LegalAcceptanceLog.objects.create(
            user=user,
            document_slug=slug,
            document_version=snapshot["version"],
            source=CONSENT_SOURCE,
            ip_address=ip_address,
            user_agent=user_agent[:MAX_USER_AGENT_LENGTH],
            metadata={
                **snapshot,
                "acceptance_id": str(nonce),
                "bundle_version": version,
                "room_id": room.pk,
                "city_id": city.pk,
                "city_label": str(city),
                "subject": _subject(user),
                "checkbox_text": CONSENT_LABELS[field],
                "disclosure": choices,
                "base_conditions": BASE_DISCLOSURE_CONDITIONS,
                "privacy": documents["privacy"],
                "method": "authenticated_explicit_checkbox",
                "ip_kind": "connection_peer",
                "operator_review_required": bool(choices["conditions"]),
            },
        )
    member.consent_version = version
    member.consent_id = nonce
    member.consented_at = timezone.now()
    member.consent_withdrawn_at = None
    member.disclosure_name = choices["show_name"]
    member.disclosure_avatar = choices["show_avatar"]
    member.disclosure_conditions = choices["conditions"]
    member.disclosure_reviewed = not bool(choices["conditions"])
    member.save(
        update_fields=[
            "consent_version",
            "consent_id",
            "consented_at",
            "consent_withdrawn_at",
            "disclosure_name",
            "disclosure_avatar",
            "disclosure_conditions",
            "disclosure_reviewed",
        ]
    )
    publish_disclosure_change(room_id=room.pk, user_id=user.pk)
    logger.info(
        "Согласия городского чата получены",
        user_id=user.pk,
        room_id=room.pk,
        acceptance_id=str(nonce),
    )
    return cast(ChatMembership, member)


@transaction.atomic
def withdraw_chat_consent(
    *, user: User, room_id: int, ip_address: str | None, user_agent: str
) -> None:
    """Отзывает согласия на чат и прекращает дальнейшую публикацию его сообщений.

    Args:
        user: Автор отзыва.
        room_id: Городской чат.
        ip_address: Проверенный адрес соединения.
        user_agent: Описание браузера.

    Raises:
        ChatConsentRequiredError: Участие отсутствует.
    """
    ChatRoom.objects.select_for_update().get(pk=room_id)
    User.objects.select_for_update(no_key=True).get(pk=user.pk)
    member = (
        ChatMembership.objects.select_for_update()
        .filter(user=user, room_id=room_id)
        .first()
    )
    if member is None:
        raise ChatConsentRequiredError("Участие не найдено.")
    if member.consent_withdrawn_at:
        return
    withdrawn_documents = ["chat-personal-data", "chat-rules"]
    if LegalAcceptanceLog.objects.filter(
        user=user,
        source=CONSENT_SOURCE,
        document_slug="chat-disclosure",
        metadata__acceptance_id=str(member.consent_id),
    ).exists():
        withdrawn_documents.append("chat-disclosure")
    LegalAcceptanceLog.objects.create(
        user=user,
        document_slug=withdrawn_documents[0],
        document_version=member.consent_version or "unknown",
        source="chat_withdrawal",
        ip_address=ip_address,
        user_agent=user_agent[:MAX_USER_AGENT_LENGTH],
        metadata={
            "subject": _subject(user),
            "room_id": room_id,
            "city_id": member.room.city_id,
            "acceptance_id": str(member.consent_id),
            "withdrawn_documents": withdrawn_documents,
            "method": "authenticated_explicit_withdrawal",
        },
    )
    member.is_active = False
    member.consent_version = ""
    member.consent_withdrawn_at = timezone.now()
    member.disclosure_name = member.disclosure_avatar = False
    member.save(
        update_fields=[
            "is_active",
            "consent_version",
            "consent_withdrawn_at",
            "disclosure_name",
            "disclosure_avatar",
        ]
    )
    publish_disclosure_change(room_id=room_id, user_id=user.pk)
    logger.info("Согласия городского чата отозваны", user_id=user.pk, room_id=room_id)


@transaction.atomic
def publish_disclosure_change(*, room_id: int, user_id: int) -> None:
    """Инвалидирует показ уже загруженных сообщений и цитат после смены согласий.

    Args:
        room_id: Комната с изменёнными условиями.
        user_id: Автор, изменивший условия распространения.
    """
    from itertools import islice

    ChatRoom.objects.select_for_update().get(pk=room_id)
    rows = (
        ChatMessage.objects.filter(room_id=room_id)
        .filter(Q(author_id=user_id) | Q(reply_to__author_id=user_id))
        .order_by("pk")
        .iterator(chunk_size=DISCLOSURE_BATCH_SIZE)
    )
    while batch := list(islice(rows, DISCLOSURE_BATCH_SIZE)):
        events = ChatEvent.objects.bulk_create(
            [ChatEvent(room_id=room_id, message=message) for message in batch]
        )
        for message, event in zip(batch, events, strict=True):
            message.revision = event.pk
        ChatMessage.objects.bulk_update(
            batch, ["revision"], batch_size=DISCLOSURE_BATCH_SIZE
        )
    transaction.on_commit(lambda: publish_room_change(room_id))
