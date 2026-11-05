"""Матчи детей в кабинете представителя и самостоятельный ввод результатов."""

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .appeals import submit_appeal
from .forms import ResultForm
from .matches import record_parent_result
from .models import JuniorMatch
from .permissions import account, can_manage_competition, match_guardians
from .services import public_child_label
from .views import posted_id


def match_row(match: JuniorMatch, child_ids: set[int]) -> dict[str, Any]:
    """Собирает подписи без раскрытия имени чужого ребёнка без согласия."""
    return {
        "match": match,
        "first": (
            (
                str(match.entry1.child)
                if match.entry1.child_id in child_ids
                else public_child_label(match.entry1.child)
            )
            if match.entry1
            else "Ожидается"
        ),
        "second": (
            (
                str(match.entry2.child)
                if match.entry2.child_id in child_ids
                else public_child_label(match.entry2.child)
            )
            if match.entry2
            else "Ожидается"
        ),
        "ready": bool(match.entry1_id and match.entry2_id),
    }


@never_cache
@login_required
@require_http_methods(["GET", "HEAD"])
def my_matches(request: HttpRequest) -> HttpResponse:
    """Показывает матчи всех детей только их действующему проверенному представителю."""
    actor = account(request)
    child_ids = set(
        actor.junior_guardianships.filter(status="verified").values_list(
            "child_id", flat=True
        )
    )
    matches = (
        JuniorMatch.objects.filter(
            Q(entry1__child_id__in=child_ids) | Q(entry2__child_id__in=child_ids)
        )
        .exclude(tournament__status="cancelled")
        .select_related("tournament", "entry1__child", "entry2__child")
        .order_by("tournament__starts_at", "round_number", "position")
    )
    state = request.GET.get("state", "upcoming")
    if state == "completed":
        matches = matches.filter(status__in=["completed", "walkover", "bye"])
    else:
        state = "upcoming"
        matches = matches.filter(status="pending")
    page = Paginator(matches, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "juniors/my_matches.html",
        {
            "rows": [match_row(match, child_ids) for match in page],
            "page": page,
            "state": state,
        },
    )


@never_cache
@login_required
@require_http_methods(["GET", "POST", "HEAD"])
def match_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Даёт семье страницу матча, ввод первого результата и обращение по ошибке."""
    actor = account(request)
    match = get_object_or_404(
        JuniorMatch.objects.select_related(
            "tournament", "entry1__child", "entry2__child"
        ),
        pk=pk,
    )
    links = match_guardians(actor, match)
    manager = can_manage_competition(actor, match.tournament)
    if not links and not manager:
        raise PermissionDenied(
            "Матч доступен представителям его участников и организатору."
        )
    can_submit = bool(
        links
        and match.status == "pending"
        and match.entry1_id
        and match.entry2_id
        and match.tournament.status == "active"
    )
    form = ResultForm(
        request.POST
        if request.method == "POST" and request.POST.get("action") != "appeal"
        else None
    )
    if request.method == "POST":
        if request.POST.get("action") == "appeal":
            try:
                if not links:
                    raise PermissionDenied
                submit_appeal(
                    actor=actor,
                    child_id=links[0].child_id,
                    match_id=pk,
                    message=request.POST.get("message", ""),
                )
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(request, "Возражение передано организатору.")
            return redirect("juniors:match", pk=pk)
        if not links:
            raise PermissionDenied(
                "Для ввода организатором откройте управление турниром."
            )
        if form.is_valid():
            try:
                record_parent_result(
                    actor=actor,
                    match_id=pk,
                    scores=form.cleaned_data["scores"],
                    walkover_side=form.cleaned_data["walkover"],
                    reason=form.cleaned_data["reason"],
                    expected_version=posted_id(request, "version") - 1,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Результат сохранён. Матч завершён.")
                return redirect("juniors:match", pk=pk)
    own_ids = {link.child_id for link in links}
    return render(
        request,
        "juniors/match.html",
        {
            **match_row(match, own_ids),
            "form": form,
            "can_submit_result": can_submit,
            "can_manage_tournament": manager,
            "can_appeal": bool(
                links
                and match.status in ("completed", "walkover")
                and match.tournament.status != "cancelled"
            ),
            "appeals": match.appeals.filter(guardian__user=actor),
            "posted_version": request.POST.get("version", match.result_version + 1),
        },
    )
