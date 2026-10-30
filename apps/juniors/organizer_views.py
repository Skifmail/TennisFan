"""Рабочий кабинет организатора детских соревнований."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from .appeals import resolve_appeal
from .competition import promote_waitlist, publish_tournament
from .forms import RescheduleForm, ResultForm, ScheduleForm
from .lifecycle import cancel_tournament, reassign_entry, reschedule_tournament
from .matches import record_result, start_tournament
from .models import JuniorAppeal, JuniorEntry, JuniorMatch, JuniorTournament
from .permissions import account, require_organizer
from .ranking import finalize
from .views import league_available, posted_id


@league_available
@never_cache
@login_required
def manage(request: HttpRequest, pk: int) -> HttpResponse:
    tournament = get_object_or_404(JuniorTournament, pk=pk)
    require_organizer(account(request), tournament)
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "publish":
                publish_tournament(actor=account(request), tournament_id=pk)
            elif action == "start":
                start_tournament(actor=account(request), tournament_id=pk)
            elif action == "finalize":
                finalize(actor=account(request), tournament_id=pk)
            elif action == "resolve_appeal":
                appeal = get_object_or_404(
                    JuniorAppeal,
                    pk=posted_id(request, "appeal"),
                    tournament=tournament,
                )
                resolve_appeal(
                    actor=account(request),
                    appeal_id=appeal.pk,
                    response=request.POST.get("response", ""),
                )
            elif action == "cancel":
                cancel_tournament(
                    actor=account(request),
                    tournament_id=pk,
                    reason=request.POST.get("reason", ""),
                )
            elif action == "reassign":
                entry = get_object_or_404(
                    JuniorEntry, pk=posted_id(request, "entry"), tournament=tournament
                )
                from .models import GuardianLink

                guardian = get_object_or_404(
                    GuardianLink,
                    pk=posted_id(request, "guardian"),
                    child_id=entry.child_id,
                    status="verified",
                )
                reassign_entry(
                    actor=account(request),
                    entry_id=entry.pk,
                    guardian_id=guardian.pk,
                    reason=request.POST.get("reason", ""),
                )
            elif action == "reschedule":
                schedule = RescheduleForm(request.POST)
                if not schedule.is_valid():
                    raise ValidationError(
                        "Укажите корректную новую дату и причину переноса."
                    )
                reschedule_tournament(
                    actor=account(request), tournament_id=pk, **schedule.cleaned_data
                )
            elif action == "promote":
                entry = get_object_or_404(
                    JuniorEntry,
                    pk=posted_id(request, "entry"),
                    tournament=tournament,
                )
                promote_waitlist(actor=account(request), entry_id=entry.pk)
            else:
                raise ValidationError("Неизвестное действие.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Действие выполнено.")
        return redirect("juniors:manage", pk=pk)
    return render(
        request,
        "juniors/manage.html",
        {
            "tournament": tournament,
            "entries": tournament.entries.select_related("child", "guardian__user"),
            "matches": tournament.matches.select_related(
                "entry1__child", "entry2__child", "winner__child"
            ),
            "appeals": tournament.appeals.select_related("guardian__child"),
        },
    )


@league_available
@never_cache
@login_required
def result(request: HttpRequest, pk: int) -> HttpResponse:
    match = get_object_or_404(
        JuniorMatch.objects.select_related(
            "tournament", "entry1__child", "entry2__child"
        ),
        pk=pk,
    )
    require_organizer(account(request), match.tournament)
    schedule_form = ScheduleForm(
        request.POST if request.POST.get("action") == "schedule" else None
    )
    if (
        request.method == "POST"
        and request.POST.get("action") == "schedule"
        and schedule_form.is_valid()
    ):
        from .matches import schedule_match

        try:
            schedule_match(
                actor=account(request),
                match_id=pk,
                scheduled_at=schedule_form.cleaned_data["scheduled_at"],
            )
        except ValidationError as exc:
            schedule_form.add_error(None, exc)
        else:
            messages.success(request, "Расписание матча сохранено.")
            return redirect("juniors:manage", pk=match.tournament_id)
    form = ResultForm(
        request.POST
        if request.method == "POST" and request.POST.get("action") != "schedule"
        else None
    )
    if (
        request.method == "POST"
        and request.POST.get("action") != "schedule"
        and form.is_valid()
    ):
        try:
            record_result(
                actor=account(request),
                match_id=pk,
                scores=form.cleaned_data["scores"],
                walkover_side=form.cleaned_data["walkover"],
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Результат сохранён.")
            return redirect("juniors:manage", pk=match.tournament_id)
    return render(
        request,
        "juniors/result.html",
        {"form": form, "match": match, "schedule_form": schedule_form},
    )
