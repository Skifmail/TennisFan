"""Страница свободного ручного сценария обычного турнира."""

from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from .manual_pairing import (
    FINAL_STATUSES,
    ManualPairingError,
    load_entities,
    supports_manual_pairing,
)
from .manual_stages import (
    CUSTOM_KINDS,
    _stage_places,
    add_stage,
    combined_places,
    create_qualifiers,
    create_two_knockouts,
    finish_combined,
    finish_stages,
    placement_signature,
    stage_context,
)


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
    preview = None
    signature = ""
    omitted: list[str] = []
    sources = {
        key.removeprefix("place_from_"): value.strip()
        for key, value in request.POST.items()
        if key.startswith("place_from_") and value.strip()
    }
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "add_stage":
                selected = request.POST.getlist("members")
                kind = request.POST.get("kind", "")
                seeds = {}
                for index, token in enumerate(selected, 1):
                    if kind == "custom_round":
                        seeds[index] = token
                        continue
                    seed = request.POST.get(f"position_{token}", "")
                    if (
                        not seed.isascii()
                        or not seed.isdecimal()
                        or len(seed) > 6
                        or not 1 <= int(seed) <= len(entities)
                        or int(seed) in seeds
                    ):
                        raise ManualPairingError(
                            "Укажите разные позиции посева для выбранных участников."
                        )
                    seeds[int(seed)] = token
                add_stage(
                    tournament,
                    request.POST.get("name", ""),
                    kind,
                    [seeds[i] for i in sorted(seeds)],
                    tour_name=request.POST.get("tour_name", ""),
                )
                messages.success(request, "Этап создан. Матчи доступны участникам.")
            elif action == "preview_places":
                places, _ = combined_places(tournament, sources)
                signature = placement_signature(tournament, places)
                preview = sorted(
                    (places[e.token][0], e.name) for e in entities if e.token in places
                )
                omitted = [e.name for e in entities if e.token not in places]
            elif action == "publish_places":
                finish_combined(tournament, sources, request.POST.get("signature", ""))
                messages.success(
                    request,
                    "Турнир завершён. Итоговые места и сезонные очки опубликованы.",
                )
            elif action == "finish_stages":
                finish_stages(tournament, request.POST.get("final_stage", ""))
                messages.success(
                    request, "Турнир завершён, итоговые места опубликованы."
                )
            elif action == "qualifiers":
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
            if preview is None:
                return redirect("tournament_manage_stages", slug=slug)
        except ManualPairingError as exc:
            error = str(exc)
    context = stage_context(tournament)
    last_results: dict[str, str] = {}
    busy: dict[str, str] = {}
    for item in context["manual_stage_views"]:
        stage = item["stage"]
        item["place_from"] = sources.get(str(stage.pk), "")
        if stage.kind not in CUSTOM_KINDS:
            continue
        if any(match.status not in FINAL_STATUSES for match in item["matches"]):
            busy.update(dict.fromkeys(stage.member_tokens, str(stage)))
        else:
            for token, place in _stage_places(stage).items():
                last_results[token] = f"{stage} · место {place}"
    context.update(
        {
            "tournament": tournament,
            "can_manage_stages": True,
            "stage_next_url": request.path,
            "stage_error": error,
            "placement_preview": preview,
            "placement_signature": signature,
            "omitted": omitted,
            "sources": sources,
            "tour_options": list(
                dict.fromkeys(
                    [name for name, _ in context["manual_tours"] if name]
                    + ["Отборочный тур", "Основной тур", "Финальный тур"]
                )
            ),
            "tour_name": request.POST.get("tour_name", ""),
            "stage_kinds": CUSTOM_KINDS,
            "stage_name": request.POST.get("name", ""),
            "selected_kind": request.POST.get("kind", ""),
            "stage_rows": [
                {
                    "entity": entity,
                    "previous": last_results.get(entity.token, ""),
                    "busy": busy.get(entity.token, ""),
                    "selected": entity.token in request.POST.getlist("members"),
                    "position": request.POST.get(
                        f"position_{entity.token}", str(index)
                    ),
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
