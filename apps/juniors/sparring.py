"""Товарищеские спарринги детей и взрослых с проверкой представителя."""

from typing import Any, cast

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.contact_utils import build_telegram_url, build_whatsapp_url
from apps.notifications.services import create_notification
from apps.users.models import Player, User

from .models import (
    AuditEvent,
    ChildProfile,
    GuardianLink,
    JuniorSparringRequest,
    JuniorSparringResponse,
)
from .permissions import guardian_for


def _subscription_allows(user: User) -> bool:
    """Проверяет действующий тариф взрослого без выдачи Free в GET-запросах."""
    subscription = getattr(user, "subscription", None)
    return bool(
        subscription and subscription.is_valid() and subscription.tier.has_sparring
    )


def adult_can_sparring(user: User) -> bool:
    """Взрослый участвует в детских спаррингах по своему действующему тарифу."""
    return bool(user.is_authenticated and user.is_active and _subscription_allows(user))


def child_can_sparring(child: ChildProfile) -> bool:
    """Подписка любого проверенного представителя распространяется на ребёнка."""
    now = timezone.now()
    return bool(
        child.status == ChildProfile.Status.ACTIVE
        and child.guardians.filter(
            status=GuardianLink.Status.VERIFIED,
            user__is_active=True,
            user__subscription__is_active=True,
            user__subscription__tier__has_sparring=True,
        )
        .filter(
            Q(user__subscription__end_date__isnull=True)
            | Q(user__subscription__end_date__gt=now)
        )
        .exists()
    )


def playable_children(user: User) -> list[ChildProfile]:
    """Возвращает детей, за которых взрослый может размещать заявки и отклики."""
    if not adult_can_sparring(user):
        return []
    return list(
        ChildProfile.objects.filter(
            guardians__user=user,
            guardians__status=GuardianLink.Status.VERIFIED,
            status=ChildProfile.Status.ACTIVE,
        )
        .distinct()
        .order_by("first_name", "pk")
    )


def age_preference_label(request: JuniorSparringRequest) -> str:
    """Описывает пожелание по возрасту соперника как подсказку."""
    low, high = request.desired_age_min, request.desired_age_max
    if low is None and high is None:
        return "Без пожеланий по возрасту"
    if low is not None and high is not None:
        return f"{low}–{high} лет"
    if low is not None:
        return f"от {low} лет"
    return f"до {high} лет"


def age_warning(request: JuniorSparringRequest, age: int | None) -> str:
    """Предупреждает о возрасте вне пожеланий, но не запрещает отклик."""
    if age is None:
        return ""
    low, high = request.desired_age_min, request.desired_age_max
    if low is None and high is None:
        return ""
    outside = (low is not None and age < low) or (high is not None and age > high)
    if not outside:
        return ""
    return (
        f"Возраст соперника — {age} лет, пожелание заявки — "
        f"{age_preference_label(request)}. Это подсказка, а не ограничение: "
        "отклик отправлен, окончательное решение за представителем."
    )


def contact_details(user: User) -> dict[str, str]:
    """Отдаёт контакты взрослого только после согласования игры."""
    player = getattr(user, "player", None)
    return {
        "name": user.get_display_name(),
        "email": user.email or "",
        "phone": user.phone or "",
        "telegram": build_telegram_url(getattr(player, "telegram", "") or "") or "",
        "whatsapp": build_whatsapp_url(getattr(player, "whatsapp", "") or "") or "",
    }


def _notify_response(response: JuniorSparringResponse) -> None:
    """Сообщает представителю автора заявки о новом отклике."""
    request = response.request
    create_notification(
        event_key="junior.sparring_response",
        user=request.guardian.user,
        message=(
            f"Отклик на спарринг: {response.respondent_label}. "
            "Откройте заявку, чтобы согласовать игру."
        )[:255],
        url=reverse("juniors:sparring", args=[request.pk]),
    )


def _notify_accepted(response: JuniorSparringResponse) -> None:
    """Открывает контакты обеим сторонам после согласования игры."""
    request = response.request
    url = reverse("juniors:sparring", args=[request.pk])
    create_notification(
        event_key="junior.sparring_accepted",
        user=request.guardian.user,
        message=(
            f"Спарринг согласован: {request.child.first_name} — "
            f"{response.respondent_label}. Контакты открыты в заявке."
        )[:255],
        url=url,
    )
    respondent_user = response.respondent_user
    if respondent_user is not None and respondent_user.pk != request.guardian.user_id:
        create_notification(
            event_key="junior.sparring_accepted",
            user=respondent_user,
            message=(
                f"Ваш отклик принят: {request.child.public_label}, {request.city}. "
                "Контакты открыты в заявке."
            )[:255],
            url=url,
        )


def _ensure_request_owner(request: JuniorSparringRequest, actor: User) -> None:
    """Разрешает согласование только действующему представителю ребёнка."""
    if request_is_owned_by(request, actor):
        return
    raise PermissionDenied("Согласовать отклик может только представитель ребёнка.")


def request_is_owned_by(request: JuniorSparringRequest, user: User) -> bool:
    """Проверяет, действует ли взрослый от имени ребёнка этой заявки."""
    if request.guardian.user_id == user.pk:
        return True
    return bool(
        request.child.guardians.filter(
            user=user, status=GuardianLink.Status.VERIFIED
        ).exists()
    )


@transaction.atomic
def create_sparring_request(
    *, actor: User, child_id: int, data: dict[str, Any]
) -> JuniorSparringRequest:
    """Размещает заявку от имени проверенного представителя активного ребёнка."""
    child = ChildProfile.objects.select_for_update(of=("self",)).get(pk=child_id)
    link = guardian_for(actor, child)
    if child.status != ChildProfile.Status.ACTIVE:
        raise ValidationError(
            "Играть можно после проверки представителя и активации профиля."
        )
    if not child_can_sparring(child):
        raise ValidationError(
            "Спарринги доступны по подписке взрослого. Оформите Silver, Gold или Diamond."
        )
    allowed = {
        "city",
        "description",
        "partner_kind",
        "gender_preference",
        "desired_age_min",
        "desired_age_max",
        "preferred_days",
        "preferred_time",
        "preferred_location",
    }
    values = {key: value for key, value in data.items() if key in allowed}
    sparring = JuniorSparringRequest(child=child, guardian=link, **values)
    sparring.full_clean()
    sparring.save()
    AuditEvent.objects.create(
        actor=actor,
        child=child,
        action="sparring.created",
        details={"request_id": sparring.pk, "partner_kind": sparring.partner_kind},
    )
    return sparring


def _save_response(
    *,
    request: JuniorSparringRequest,
    message: str,
    player: Player | None = None,
    child: ChildProfile | None = None,
    guardian: GuardianLink | None = None,
) -> JuniorSparringResponse:
    """Сохраняет единственный отклик стороны и уведомляет только при первом."""
    lookup: dict[str, Any] = {"request": request}
    if player is not None:
        lookup["player"] = player
    else:
        lookup["child"] = child
    response = cast(
        JuniorSparringResponse | None,
        JuniorSparringResponse.objects.select_for_update().filter(**lookup).first(),
    )
    if response is not None:
        if response.status == JuniorSparringResponse.Status.ACCEPTED:
            raise ValidationError("Ваш отклик уже согласован.")
        response.message = message[:500]
        response.status = JuniorSparringResponse.Status.PENDING
        if guardian is not None:
            response.guardian = guardian
        response.full_clean()
        response.save(update_fields=["message", "status", "guardian", "updated_at"])
        return response
    response = JuniorSparringResponse(
        request=request,
        player=player,
        child=child,
        guardian=guardian,
        message=message[:500],
    )
    response.full_clean()
    response.save()
    _notify_response(response)
    return response


@transaction.atomic
def respond_to_sparring(
    *, actor: User, request_id: int, child_id: int | None = None, message: str = ""
) -> JuniorSparringResponse:
    """Откликается взрослый по подписке либо представитель другого ребёнка."""
    request = (
        JuniorSparringRequest.objects.select_for_update()
        .select_related("child", "guardian__user")
        .filter(pk=request_id)
        .first()
    )
    if request is None:
        raise ValidationError("Заявка не найдена.")
    if request.status != JuniorSparringRequest.Status.ACTIVE:
        raise ValidationError("Заявка уже закрыта.")
    if (
        request.child_id is not None
        and request.child.guardians.filter(user=actor).exists()
    ):
        raise ValidationError("Нельзя откликнуться на заявку своего ребёнка.")

    if child_id is None:
        if not request.allows_adult:
            raise ValidationError("Ребёнок ищет соперника-ребёнка.")
        if not adult_can_sparring(actor):
            raise ValidationError(
                "Спарринги доступны по подписке. Оформите Silver, Gold или Diamond."
            )
        try:
            player = actor.player
        except Player.DoesNotExist as exc:
            raise ValidationError("Заполните профиль игрока.") from exc
        return _save_response(request=request, message=message, player=player)

    if not request.allows_child:
        raise ValidationError("Ребёнок ищет взрослого соперника.")
    child = ChildProfile.objects.get(pk=child_id)
    link = guardian_for(actor, child)
    if child.pk == request.child_id:
        raise ValidationError("Нельзя откликнуться на заявку своего ребёнка.")
    if child.status != ChildProfile.Status.ACTIVE:
        raise ValidationError("Играть может только ребёнок с активным профилем.")
    if not child_can_sparring(child):
        raise ValidationError(
            "Спарринги доступны по подписке взрослого. Оформите Silver, Gold или Diamond."
        )
    return _save_response(request=request, message=message, child=child, guardian=link)


@transaction.atomic
def accept_response(*, actor: User, response_id: int) -> JuniorSparringResponse:
    """Согласует один отклик, закрывает заявку и открывает контакты обеим сторонам."""
    request_id = (
        JuniorSparringResponse.objects.filter(pk=response_id)
        .values_list("request_id", flat=True)
        .first()
    )
    if request_id is None:
        raise ValidationError("Отклик не найден.")
    request = (
        JuniorSparringRequest.objects.select_for_update()
        .select_related("child", "guardian__user")
        .get(pk=request_id)
    )
    response = cast(
        JuniorSparringResponse | None,
        JuniorSparringResponse.objects.select_for_update()
        .select_related("player__user", "child", "guardian__user")
        .filter(pk=response_id, request=request)
        .first(),
    )
    if response is None:
        raise ValidationError("Отклик не найден.")
    _ensure_request_owner(request, actor)
    if request.status != JuniorSparringRequest.Status.ACTIVE:
        raise ValidationError("Заявка уже закрыта.")
    if response.status != JuniorSparringResponse.Status.PENDING:
        raise ValidationError("Этот отклик уже обработан.")
    if not child_can_sparring(request.child):
        raise ValidationError("Подписка представителя не активна. Продлите тариф.")
    response.status = JuniorSparringResponse.Status.ACCEPTED
    response.save(update_fields=["status", "updated_at"])
    request.status = JuniorSparringRequest.Status.CLOSED
    request.save(update_fields=["status", "updated_at"])
    request.responses.filter(status=JuniorSparringResponse.Status.PENDING).exclude(
        pk=response.pk
    ).update(status=JuniorSparringResponse.Status.REJECTED)
    AuditEvent.objects.create(
        actor=actor,
        child=request.child,
        action="sparring.accepted",
        details={"request_id": request.pk, "response_id": response.pk},
    )
    _notify_accepted(response)
    return response


@transaction.atomic
def reject_response(*, actor: User, response_id: int) -> JuniorSparringResponse:
    """Отклоняет один отклик, оставляя заявку открытой для других."""
    response = cast(
        JuniorSparringResponse | None,
        JuniorSparringResponse.objects.select_for_update()
        .select_related("request__child", "request__guardian__user", "player__user")
        .filter(pk=response_id)
        .first(),
    )
    if response is None:
        raise ValidationError("Отклик не найден.")
    _ensure_request_owner(response.request, actor)
    if response.status != JuniorSparringResponse.Status.PENDING:
        raise ValidationError("Этот отклик уже обработан.")
    response.status = JuniorSparringResponse.Status.REJECTED
    response.save(update_fields=["status", "updated_at"])
    return response


@transaction.atomic
def cancel_response(*, actor: User, response_id: int) -> None:
    """Отменяет собственный ожидающий отклик без доступа к чужим решениям."""
    response = (
        JuniorSparringResponse.objects.select_for_update()
        .select_related("player__user", "guardian__user")
        .filter(pk=response_id)
        .first()
    )
    if response is None:
        raise ValidationError("Отклик не найден.")
    if response.respondent_user is None or response.respondent_user.pk != actor.pk:
        raise PermissionDenied("Отменить можно только свой отклик.")
    if response.status != JuniorSparringResponse.Status.PENDING:
        raise ValidationError("Отклик уже обработан.")
    response.status = JuniorSparringResponse.Status.REJECTED
    response.save(update_fields=["status", "updated_at"])


@transaction.atomic
def close_request(*, actor: User, request_id: int) -> JuniorSparringRequest:
    """Закрывает заявку представителя, если игра ещё не согласована."""
    request = cast(
        JuniorSparringRequest | None,
        JuniorSparringRequest.objects.select_for_update()
        .select_related("child")
        .filter(pk=request_id)
        .first(),
    )
    if request is None:
        raise ValidationError("Заявка не найдена.")
    _ensure_request_owner(request, actor)
    if request.responses.filter(status=JuniorSparringResponse.Status.ACCEPTED).exists():
        raise ValidationError("Игра уже согласована. Заявку нельзя закрыть.")
    if request.status == JuniorSparringRequest.Status.CLOSED:
        return request
    request.status = JuniorSparringRequest.Status.CLOSED
    request.save(update_fields=["status", "updated_at"])
    request.responses.filter(status=JuniorSparringResponse.Status.PENDING).update(
        status=JuniorSparringResponse.Status.REJECTED
    )
    return request
