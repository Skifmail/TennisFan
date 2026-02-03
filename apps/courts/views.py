"""
Courts views.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, redirect, render

from apps.comments.models import Comment
from apps.users.models import Player

from .forms import CourtApplicationForm
from .models import Court, CourtRating


def court_list(request):
    """List of courts with average rating."""
    city = request.GET.get("city", "")
    surface = request.GET.get("surface", "")

    courts = (
        Court.objects.filter(is_active=True)
        .annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings"),
        )
    )

    if city:
        courts = courts.filter(city__icontains=city)
    if surface:
        courts = courts.filter(surface=surface)

    courts = list(courts)
    for c in courts:
        c.rating_percent = round((float(getattr(c, "average_rating") or 0) / 5) * 100, 1)

    context = {
        "courts": courts,
        "current_city": city,
        "current_surface": surface,
    }
    return render(request, "courts/list.html", context)


def court_detail(request, slug):
    """Court detail page with rating, comments, expanded info."""
    court = get_object_or_404(Court, slug=slug, is_active=True)

    # Rating: average and count; current user's rating if any
    rating_agg = court.ratings.aggregate(
        average_rating=Avg("score"),
        rating_count=Count("id"),
    )
    court.average_rating = rating_agg["average_rating"]
    court.rating_count = rating_agg["rating_count"] or 0
    court.rating_percent = round((float(court.average_rating or 0) / 5) * 100, 1)
    user_rating = None
    if request.user.is_authenticated:
        user_rating = CourtRating.objects.filter(court=court, user=request.user).first()
        if user_rating:
            court.user_rating = user_rating.score

    # Comments (generic Comment model for Court) + оценка автора по court.ratings
    ct = ContentType.objects.get_for_model(Court)
    comments = list(
        Comment.objects.filter(
            content_type=ct,
            object_id=court.pk,
            is_approved=True,
        )
        .select_related("author__user")
        .order_by("-created_at")
    )
    ratings_by_user = dict(court.ratings.values_list("user_id", "score"))
    for c in comments:
        c.author_score = ratings_by_user.get(c.author.user_id)
        c.author_rating_percent = (c.author_score or 0) * 20

    # POST: submit comment + rating (оценка ставится только вместе с комментарием)
    if request.method == "POST" and request.POST.get("action") == "comment" and request.user.is_authenticated:
        text = (request.POST.get("text") or "").strip()
        score_raw = request.POST.get("score")
        try:
            score = int(score_raw) if score_raw else None
        except (TypeError, ValueError):
            score = None
        if not (1 <= score <= 5):
            messages.error(request, "Выберите оценку от 1 до 5 звёзд.")
        elif not text or len(text) > 2000:
            messages.error(request, "Введите текст комментария (до 2000 символов).")
        else:
            try:
                player = request.user.player
            except Player.DoesNotExist:
                messages.error(request, "Чтобы оставить комментарий, нужен профиль игрока.")
            else:
                comment = Comment.objects.create(
                    content_type=ct,
                    object_id=court.pk,
                    author=player,
                    text=text,
                    is_approved=True,
                )
                CourtRating.objects.update_or_create(
                    court=court,
                    user=request.user,
                    defaults={"score": score},
                )
                try:
                    from apps.core.telegram_notify import notify_court_comment
                    notify_court_comment(comment=comment, court=court, score=score)
                except Exception:
                    pass
                messages.success(request, "Комментарий и оценка сохранены.")
                return redirect("court_detail", slug=court.slug)

    recent_matches = court.matches.select_related(
        "player1__user", "player2__user"
    ).order_by("-scheduled_datetime")[:10]

    context = {
        "court": court,
        "recent_matches": recent_matches,
        "comments": comments,
        "can_comment": request.user.is_authenticated,
        "yandex_maps_api_key": getattr(settings, "YANDEX_MAPS_API_KEY", "") or "",
        "court_has_coords": court.latitude is not None and court.longitude is not None,
    }
    return render(request, "courts/detail.html", context)


def court_application_create(request):
    """Подача заявки на добавление корта. Поля как в админке."""
    if request.method == "POST":
        form = CourtApplicationForm(request.POST, request.FILES)
        if form.is_valid():
            app = form.save()
            from apps.core.telegram_notify import notify_court_application

            notify_court_application(app)
            messages.success(
                request,
                "Заявка отправлена. Мы рассмотрим её и свяжемся с вами. "
                "После одобрения корт появится на сайте.",
            )
            return redirect("court_application_success")
    else:
        form = CourtApplicationForm()
    return render(
        request,
        "courts/application_form.html",
        {"form": form},
    )


def court_application_success(request):
    """Страница после успешной отправки заявки."""
    return render(request, "courts/application_success.html")
