"""Страницы товарищеских спаррингов детей и взрослых."""

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .forms import JuniorSparringForm, JuniorSparringResponseForm
from .models import (
    ChildProfile,
    GuardianLink,
    JuniorSparringRequest,
    JuniorSparringResponse,
)
from .permissions import account
from .sparring import (
    accept_response,
    adult_can_sparring,
    age_preference_label,
    age_warning,
    cancel_response,
    child_can_sparring,
    close_request,
    contact_details,
    create_sparring_request,
    playable_children,
    reject_response,
    request_is_owned_by,
    respond_to_sparring,
)


def _guardian_children(user_id: int) -> dict[int, ChildProfile]:
    """Возвращает проверенных детей взрослого для подписей в списках."""
    return {
        child.pk: child
        for child in ChildProfile.objects.filter(
            guardians__user_id=user_id,
            guardians__status=GuardianLink.Status.VERIFIED,
        ).distinct()
    }


def _request_label(
    sparring: JuniorSparringRequest, own: dict[int, ChildProfile]
) -> str:
    """Показывает имя ребёнка своему взрослому и публичную метку остальным."""
    if sparring.child_id in own:
        return str(sparring.child)
    return str(sparring.child.public_label)


def _responded_by_user(*, user_id: int, request_ids: list[int]) -> set[int]:
    """Отмечает заявки, на которые взрослый уже откликнулся."""
    if not request_ids:
        return set()
    return set(
        JuniorSparringResponse.objects.filter(request_id__in=request_ids)
        .filter(Q(player__user_id=user_id) | Q(guardian__user_id=user_id))
        .values_list("request_id", flat=True)
    )


@never_cache
@login_required
def sparrings(request: HttpRequest) -> HttpResponse:
    """Показывает активные заявки детей с фильтром по городу и типу соперника."""
    user = account(request)
    qs = (
        JuniorSparringRequest.objects.filter(status=JuniorSparringRequest.Status.ACTIVE)
        .select_related("child", "guardian__user")
        .order_by("-created_at", "-pk")
    )
    city = (request.GET.get("city") or "").strip()
    partner_kind = request.GET.get("partner_kind") or ""
    if city:
        qs = qs.filter(city__icontains=city)
    if partner_kind in JuniorSparringRequest.PartnerKind.values:
        qs = qs.filter(partner_kind=partner_kind)
    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    own = _guardian_children(user.pk)
    responded = _responded_by_user(
        user_id=user.pk, request_ids=[obj.pk for obj in page.object_list]
    )
    rows = [
        {
            "request": obj,
            "label": _request_label(obj, own),
            "age_preference": age_preference_label(obj),
            "own": obj.child_id in own,
            "responded": obj.pk in responded,
        }
        for obj in page.object_list
    ]
    return render(
        request,
        "juniors/sparrings.html",
        {
            "rows": rows,
            "page": page,
            "city": city,
            "partner_kind": partner_kind,
            "partner_kinds": JuniorSparringRequest.PartnerKind.choices,
            "can_play": adult_can_sparring(user) or bool(playable_children(user)),
        },
    )


@never_cache
@login_required
def my_sparrings(request: HttpRequest) -> HttpResponse:
    """Собирает заявки взрослого, полученные отклики и его собственные отклики."""
    user = account(request)
    children = playable_children(user)
    own_requests = (
        JuniorSparringRequest.objects.filter(guardian__user=user)
        .select_related("child")
        .order_by("-created_at", "-pk")
    )
    received = (
        JuniorSparringResponse.objects.filter(request__guardian__user=user)
        .select_related("request__child", "player__user", "child", "guardian__user")
        .order_by("-created_at", "-pk")
    )
    sent = (
        JuniorSparringResponse.objects.filter(
            Q(player__user_id=user.pk) | Q(guardian__user_id=user.pk)
        )
        .select_related("request__child", "request__guardian__user")
        .order_by("-created_at", "-pk")
    )
    return render(
        request,
        "juniors/my_sparrings.html",
        {
            "own_requests": own_requests,
            "received": received,
            "sent": sent,
            "children": children,
            "can_create": bool(children),
        },
    )


@never_cache
@login_required
def sparring_new(request: HttpRequest) -> HttpResponse:
    """Размещает заявку от имени ребёнка проверенного представителя."""
    user = account(request)
    children = playable_children(user)
    if not children:
        messages.error(
            request,
            "Разместить заявку можно после проверки представительства и при "
            "действующей подписке взрослого с доступом к спаррингам.",
        )
        return redirect("juniors:my_sparrings")
    form = JuniorSparringForm(
        request.POST or None, children=children, initial={"city": children[0].city}
    )
    if request.method == "POST" and form.is_valid():
        try:
            sparring = create_sparring_request(
                actor=user,
                child_id=form.cleaned_data["child"].pk,
                data=form.cleaned_data,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Заявка опубликована. Отклики придут в кабинет.")
            return redirect("juniors:sparring", pk=sparring.pk)
    return render(
        request,
        "juniors/sparring_form.html",
        {"form": form, "children": children},
    )


def _detail_context(
    *,
    sparring: JuniorSparringRequest,
    user: Any,
    form: JuniorSparringResponseForm | None = None,
) -> dict[str, Any]:
    """Собирает данные заявки без раскрытия чужих персональных данных."""
    own = _guardian_children(user.pk)
    is_owner = request_is_owned_by(sparring, user)
    accepted = sparring.accepted_response
    own_response = sparring.responses.filter(
        Q(player__user_id=user.pk) | Q(guardian__user_id=user.pk)
    ).first()
    is_participant = bool(
        accepted is not None
        and accepted.respondent_user is not None
        and accepted.respondent_user.pk == user.pk
    )
    children = [
        child for child in playable_children(user) if child.pk != sparring.child_id
    ]
    allow_adult = sparring.allows_adult and adult_can_sparring(user)
    can_respond = bool(
        sparring.status == JuniorSparringRequest.Status.ACTIVE
        and not is_owner
        and own_response is None
        and (allow_adult or (sparring.allows_child and children))
    )
    if form is None and can_respond:
        form = JuniorSparringResponseForm(children=children, allow_adult=allow_adult)
    responses = [
        {
            "response": response,
            "warning": age_warning(sparring, response.respondent_age),
        }
        for response in sparring.responses.select_related(
            "player__user", "child", "guardian__user"
        )
    ]
    contacts: list[dict[str, str]] = []
    if accepted is not None and (is_owner or is_participant):
        contacts.append(contact_details(sparring.guardian.user))
        respondent_user = accepted.respondent_user
        if (
            respondent_user is not None
            and respondent_user.pk != sparring.guardian.user_id
        ):
            contacts.append(contact_details(respondent_user))
    return {
        "sparring": sparring,
        "label": _request_label(sparring, own),
        "age_preference": age_preference_label(sparring),
        "is_owner": is_owner,
        "is_participant": is_participant,
        "accepted": accepted,
        "own_response": own_response,
        "responses": responses,
        "form": form,
        "can_respond": can_respond,
        "allow_adult": allow_adult,
        "children": children,
        "contacts": contacts,
        "subscription_ok": child_can_sparring(sparring.child),
    }


@never_cache
@login_required
def sparring_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Показывает заявку, отклики и контакты после согласования игры."""
    sparring = get_object_or_404(
        JuniorSparringRequest.objects.select_related("child", "guardian__user"),
        pk=pk,
    )
    return render(
        request,
        "juniors/sparring.html",
        _detail_context(sparring=sparring, user=account(request)),
    )


@never_cache
@login_required
@require_POST
def sparring_respond(request: HttpRequest, pk: int) -> HttpResponse:
    """Принимает отклик взрослого или представителя другого ребёнка."""
    user = account(request)
    sparring = get_object_or_404(
        JuniorSparringRequest.objects.select_related("child", "guardian__user"), pk=pk
    )
    is_owner = request_is_owned_by(sparring, user)
    children = [
        child for child in playable_children(user) if child.pk != sparring.child_id
    ]
    allow_adult = sparring.allows_adult and adult_can_sparring(user)
    form = JuniorSparringResponseForm(
        request.POST, children=children, allow_adult=allow_adult
    )
    if is_owner or not form.is_valid():
        messages.error(request, "Проверьте выбранного ребёнка и текст сообщения.")
        return render(
            request,
            "juniors/sparring.html",
            _detail_context(sparring=sparring, user=user, form=form),
        )
    child = form.cleaned_data.get("child")
    try:
        respond_to_sparring(
            actor=user,
            request_id=sparring.pk,
            child_id=child.pk if child else None,
            message=form.cleaned_data.get("message", ""),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Отклик отправлен. Представитель ребёнка увидит его.")
    return redirect("juniors:sparring", pk=sparring.pk)


@never_cache
@login_required
@require_POST
def sparring_accept(request: HttpRequest, pk: int) -> HttpResponse:
    """Согласует отклик и открывает контакты обеим сторонам."""
    response = get_object_or_404(
        JuniorSparringResponse.objects.select_related("request"), pk=pk
    )
    try:
        accept_response(actor=account(request), response_id=response.pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Спарринг согласован. Контакты открыты в заявке.")
    return redirect("juniors:sparring", pk=response.request_id)


@never_cache
@login_required
@require_POST
def sparring_reject(request: HttpRequest, pk: int) -> HttpResponse:
    """Отклоняет отклик, оставляя заявку открытой до нового отклика."""
    response = get_object_or_404(
        JuniorSparringResponse.objects.select_related("request"), pk=pk
    )
    try:
        reject_response(actor=account(request), response_id=response.pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, "Отклик отклонён.")
    return redirect("juniors:sparring", pk=response.request_id)


@never_cache
@login_required
@require_POST
def sparring_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    """Отменяет собственный ожидающий отклик."""
    response = get_object_or_404(
        JuniorSparringResponse.objects.select_related("request"), pk=pk
    )
    try:
        cancel_response(actor=account(request), response_id=response.pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, "Отклик отменён.")
    return redirect("juniors:sparring", pk=response.request_id)


@never_cache
@login_required
@require_POST
def sparring_close(request: HttpRequest, pk: int) -> HttpResponse:
    """Закрывает заявку представителя, пока игра ещё не согласована."""
    sparring = get_object_or_404(JuniorSparringRequest, pk=pk)
    try:
        close_request(actor=account(request), request_id=sparring.pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, "Заявка закрыта.")
    return redirect("juniors:sparring", pk=sparring.pk)
