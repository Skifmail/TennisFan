"""Страницы детской лиги и приватного семейного кабинета."""

from collections.abc import Callable
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.email_service import send_email_verification

from .competition import register, withdraw
from .forms import (
    ChildForm,
    ParentRegistrationForm,
    RegistrationForm,
    TournamentFilterForm,
)
from .models import (
    ChildProfile,
    GuardianLink,
    JuniorAppeal,
    JuniorAward,
    JuniorEntry,
    JuniorMatch,
    JuniorSeason,
    JuniorTournament,
    LegalRelease,
)
from .permissions import account, guardian_for
from .rules import BALL_GUIDE, Ball
from .services import (
    accept_consent,
    create_child,
    public_child_label,
    update_draft,
    withdraw_consent,
)


def posted_id(request: HttpRequest, key: str) -> int:
    value = request.POST.get(key, "")
    if not value.isdecimal() or len(value) > 19 or not 0 < int(value) < 2**63:
        raise ValidationError(
            "Некорректный идентификатор. Обновите страницу и повторите действие."
        )
    return int(value)


def league_available(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Закрывает маршруты лиги до явного включения после миграций."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not settings.JUNIOR_LEAGUE_ENABLED and not (
            request.user.is_authenticated and request.user.is_staff
        ):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapped


@league_available
@never_cache
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "juniors/home.html", {"ball_guide": BALL_GUIDE})


@league_available
@never_cache
def rules(request: HttpRequest) -> HttpResponse:
    return render(request, "juniors/rules.html", {"ball_guide": BALL_GUIDE})


@league_available
@never_cache
@require_http_methods(["GET", "POST"])
def parent_join(request: HttpRequest) -> HttpResponse:
    """Регистрирует взрослого для семейного кабинета без создания взрослого игрока."""
    from apps.users.views import (
        _log_registration_legal_acceptances,
        _registration_allowed,
    )

    if request.user.is_authenticated:
        return redirect("juniors:family")
    form = ParentRegistrationForm(request.POST or None)
    if request.method == "POST":
        if not _registration_allowed(request):
            form.is_valid()
            form.add_error(None, "Слишком много попыток. Повторите регистрацию позже.")
        elif form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    _log_registration_legal_acceptances(request, user)
                    from .models import AuditEvent

                    AuditEvent.objects.create(actor=user, action="parent.registered")
            except IntegrityError:
                form.add_error(
                    "email", "Аккаунт уже существует. Войдите или восстановите пароль."
                )
            else:
                login(
                    request, user, backend="django.contrib.auth.backends.ModelBackend"
                )
                send_email_verification(user)
                messages.success(
                    request,
                    "Аккаунт создан. Подтвердите email, затем добавьте ребёнка.",
                )
                return redirect("juniors:family")
    return render(request, "juniors/parent_join.html", {"form": form})


@league_available
@never_cache
@login_required
def family(request: HttpRequest) -> HttpResponse:
    links = (
        GuardianLink.objects.filter(user=account(request))
        .exclude(status=GuardianLink.Status.REVOKED)
        .select_related("child")
    )
    history = ChildProfile.objects.filter(adult_user_id=request.user.pk).first()
    return render(
        request, "juniors/family.html", {"links": links, "adult_history": history}
    )


@never_cache
@login_required
def adult_history(request: HttpRequest) -> HttpResponse:
    from .statistics import child_statistics

    child = get_object_or_404(ChildProfile, adult_user_id=request.user.pk)
    return render(
        request,
        "juniors/adult_history.html",
        {"child": child, "statistics": child_statistics(child)},
    )


@league_available
@never_cache
@login_required
def child_form(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    child = get_object_or_404(ChildProfile, pk=pk) if pk else None
    if child:
        link = guardian_for(account(request), child, verified=False)
    else:
        link = None
    form = ChildForm(
        request.POST or None,
        instance=child,
        initial={"relation": link.relation if link else "parent"},
    )
    if request.method == "POST" and form.is_valid():
        try:
            if child:
                saved = update_draft(
                    actor=account(request), child_id=child.pk, data=form.cleaned_data
                )
            else:
                saved = create_child(
                    actor=account(request),
                    data=form.cleaned_data,
                    relation=form.cleaned_data["relation"],
                )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(
                request,
                "Профиль сохранён. Для участия подтвердите полномочия представителя.",
            )
            return redirect("juniors:child", pk=saved.pk)
    return render(request, "juniors/child_form.html", {"form": form, "child": child})


@league_available
@never_cache
@login_required
def child_detail(request: HttpRequest, pk: int) -> HttpResponse:
    from .statistics import child_statistics

    child = get_object_or_404(ChildProfile, pk=pk)
    link = guardian_for(account(request), child, verified=False)
    release = LegalRelease.objects.filter(reviewed=True, is_current=True).first()
    consents = link.consents.filter(withdrawn_at__isnull=True, release=release)
    return render(
        request,
        "juniors/child.html",
        {
            "child": child,
            "link": link,
            "release": release,
            "processing_accepted": consents.filter(kind="processing").exists(),
            "publication_accepted": consents.filter(kind="publication").exists(),
            "entries": child.entries.select_related("tournament"),
            "statistics": child_statistics(child),
            "appeals": JuniorAppeal.objects.filter(
                guardian__child=child
            ).select_related("match"),
        },
    )


@league_available
@never_cache
@login_required
@require_POST
def consent_action(request: HttpRequest, pk: int) -> HttpResponse:
    get_object_or_404(ChildProfile, pk=pk)
    kind = request.POST.get("kind", "")
    try:
        if request.POST.get("action") == "withdraw":
            withdraw_consent(actor=account(request), child_id=pk, kind=kind)
        else:
            if request.POST.get("accepted") != "yes":
                raise ValidationError(
                    "Подтвердите ознакомление с конкретным согласием."
                )
            release = get_object_or_404(LegalRelease, pk=posted_id(request, "release"))
            accept_consent(
                actor=account(request), child_id=pk, release_id=release.pk, kind=kind
            )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Настройки согласия сохранены.")
    return redirect("juniors:child", pk=pk)


@league_available
@never_cache
def tournaments(request: HttpRequest) -> HttpResponse:
    qs = JuniorTournament.objects.exclude(status="draft").select_related("season")
    filter_form = TournamentFilterForm(request.GET if request.GET else {})
    if filter_form.is_valid():
        data = filter_form.cleaned_data
        for key in ("ball", "city", "status", "gender"):
            if data.get(key):
                qs = qs.filter(**{key: data[key]})
        if data.get("age") is not None:
            qs = qs.filter(min_age__lte=data["age"], max_age__gte=data["age"])
        if data.get("date_from"):
            qs = qs.filter(starts_at__date__gte=data["date_from"])
        if data.get("date_to"):
            qs = qs.filter(starts_at__date__lte=data["date_to"])
        if data.get("available"):
            qs = qs.annotate(
                occupied=Count(
                    "entries",
                    filter=Q(entries__status="confirmed")
                    | Q(
                        entries__status="payment",
                        entries__reserved_until__gt=timezone.now(),
                    ),
                )
            ).filter(
                occupied__lt=F("capacity"),
                status="open",
                registration_ends_at__gt=timezone.now(),
            )
    else:
        qs = qs.none()
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "juniors/tournaments.html",
        {
            "page": Paginator(qs.order_by("starts_at", "pk"), 24).get_page(
                request.GET.get("page")
            ),
            "filter_form": filter_form,
            "query": query.urlencode(),
        },
    )


@league_available
@never_cache
def tournament_detail(request: HttpRequest, pk: int) -> HttpResponse:
    tournament = get_object_or_404(
        JuniorTournament.objects.exclude(status="draft").select_related("season"), pk=pk
    )
    entries = tournament.entries.filter(status="confirmed").select_related("child")
    participants = [{"label": public_child_label(entry.child)} for entry in entries]
    matches = [
        {
            "round": match.round_number,
            "first": (
                public_child_label(match.entry1.child)
                if match.entry1 is not None
                else "Ожидается"
            ),
            "second": (
                public_child_label(match.entry2.child)
                if match.entry2 is not None
                else "Свободный круг" if match.status == "bye" else "Ожидается"
            ),
            "status": match.get_status_display(),
            "scores": match.scores,
            "scheduled_at": match.scheduled_at,
        }
        for match in tournament.matches.select_related("entry1__child", "entry2__child")
    ]
    form = (
        RegistrationForm(request.POST or None, user=request.user, tournament=tournament)
        if request.user.is_authenticated
        else None
    )
    if request.method == "POST":
        if form is None:
            return redirect("login")
        if form.is_valid():
            try:
                entry = register(
                    actor=account(request),
                    child_id=form.cleaned_data["child"].pk,
                    tournament_id=tournament.pk,
                    regulation_version=form.cleaned_data["regulation_version"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Заявка: {entry.get_status_display()}.")
                return redirect("juniors:child", pk=entry.child_id)
    return render(
        request,
        "juniors/tournament.html",
        {
            "tournament": tournament,
            "participants": participants,
            "form": form,
            "matches": matches,
        },
    )


@league_available
@never_cache
def ratings(request: HttpRequest) -> HttpResponse:
    from .ranking import leaderboard

    seasons = JuniorSeason.objects.all()
    season_id = request.GET.get("season", "")
    season = (
        seasons.filter(pk=season_id).first() if season_id.isdigit() else seasons.first()
    )
    ball = request.GET.get("ball", Ball.RED)
    if ball not in Ball.values:
        ball = Ball.RED
    categories = (
        list(
            JuniorAward.objects.filter(
                season=season, ball=ball, tournament__status="completed"
            )
            .values_list("category", flat=True)
            .distinct()
        )
        if season
        else []
    )
    category = request.GET.get("category", "")
    if category not in categories:
        category = categories[0] if categories else ""
    city = request.GET.get("city", "")[:100]
    rows = (
        leaderboard(season=season, ball=ball, category=category, city=city)
        if season and category
        else []
    )
    labels = []
    gender_labels = {"open": "Все", "male": "Мальчики", "female": "Девочки"}
    for item in categories:
        ages, rule, gender, _ = item.split(":")
        labels.append(
            (
                item,
                f"{ages} лет · {'на конец года' if rule == 'year_end' else 'на дату начала'} · {gender_labels[gender]}",
            )
        )
    return render(
        request,
        "juniors/ratings.html",
        {
            "rows": rows,
            "seasons": seasons,
            "season": season,
            "balls": Ball.choices,
            "ball": ball,
            "categories": labels,
            "category": category,
            "city": city,
        },
    )


@league_available
@never_cache
@login_required
@require_POST
def withdraw_entry(request: HttpRequest, pk: int) -> HttpResponse:
    get_object_or_404(JuniorEntry, pk=pk)
    try:
        entry = withdraw(actor=account(request), entry_id=pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("juniors:family")
    messages.success(
        request, "Заявка снята. Если был оплачен взнос, подайте запрос на возврат."
    )
    return redirect("juniors:child", pk=entry.child_id)


@league_available
@never_cache
@login_required
@require_POST
def appeal(request: HttpRequest, pk: int, match_id: int) -> HttpResponse:
    from .appeals import submit_appeal

    get_object_or_404(ChildProfile, pk=pk)
    get_object_or_404(JuniorMatch, pk=match_id)
    try:
        submit_appeal(
            actor=account(request),
            child_id=pk,
            match_id=match_id,
            message=request.POST.get("message", ""),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Возражение передано организатору.")
    return redirect("juniors:child", pk=pk)
