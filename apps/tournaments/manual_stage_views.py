"""Страница свободного ручного сценария обычного турнира."""

from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from .manual_pairing import ManualPairingError, load_entities, supports_manual_pairing
from .manual_stages import create_qualifiers, create_two_knockouts, stage_context


@login_required
def manage_stages(request: HttpRequest, slug: str) -> HttpResponse:
    """Настроить круговой отбор и ручной посев в обе олимпийские сетки."""
    from .views import _tournament_manage_get_any_tournament

    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    if (
        not supports_manual_pairing(tournament)
        or not tournament.manual_pairing_has_started()
    ):
        messages.error(
            request,
            "Сначала запустите круговой или олимпийский турнир в ручном режиме.",
        )
        return redirect("tournament_manage", slug=slug)
    entities = load_entities(tournament)
    error = ""
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "qualifiers":
                groups: dict[str, list[str]] = defaultdict(list)
                for entity in entities:
                    name = request.POST.get(f"qualifier_{entity.token}", "").strip()
                    if not name:
                        raise ManualPairingError(
                            "Выберите отбор для каждого участника."
                        )
                    groups[name].append(entity.token)
                create_qualifiers(tournament, dict(groups))
                messages.success(
                    request, "Круговые отборы созданы. Внесите результаты матчей."
                )
            elif action == "knockouts":
                seeds: dict[str, dict[int, str]] = {"main": {}, "consolation": {}}
                for entity in entities:
                    branch = request.POST.get(f"branch_{entity.token}", "")
                    position = request.POST.get(f"seed_{entity.token}", "")
                    if (
                        branch not in seeds
                        or not position.isdecimal()
                        or not 1 <= int(position) <= len(entities)
                    ):
                        raise ManualPairingError(
                            "Выберите сетку и позицию посева для каждого участника."
                        )
                    seed = int(position)
                    if seed in seeds[branch]:
                        raise ManualPairingError(
                            "Позиции посева внутри каждой сетки должны быть разными."
                        )
                    seeds[branch][seed] = entity.token
                create_two_knockouts(
                    tournament,
                    *[
                        [token for _, token in sorted(seeds[branch].items())]
                        for branch in ("main", "consolation")
                    ],
                )
                messages.success(
                    request,
                    "Обе олимпийские сетки созданы. Победители проходят дальше автоматически.",
                )
            else:
                raise ManualPairingError("Неизвестное действие.")
            return redirect("tournament_manage_stages", slug=slug)
        except ManualPairingError as exc:
            error = str(exc)
    context = stage_context(tournament)
    context.update(
        {
            "tournament": tournament,
            "can_manage_stages": True,
            "stage_next_url": request.path,
            "stage_error": error,
            "stage_rows": [
                {
                    "entity": entity,
                    "group": request.POST.get(f"qualifier_{entity.token}", "1"),
                    "branch": request.POST.get(f"branch_{entity.token}", ""),
                    "seed": request.POST.get(f"seed_{entity.token}", str(index)),
                }
                for index, entity in enumerate(entities, 1)
            ],
            "group_numbers": [str(index) for index in range(1, 9)],
            "can_create_stages": not tournament.matches.exists()
            and not context["stages_closed"],
        }
    )
    return render(
        request, "tournaments/manage_stages.html", context, status=400 if error else 200
    )
