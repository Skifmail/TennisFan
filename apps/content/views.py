"""
Content views - News, Gallery, Pages, About Us.
"""

import logging

import markdown
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.comments.models import Comment
from apps.users.models import Player

from .forms import AboutUsCommentForm, NewsCommentForm
from .models import (
    AboutUs,
    ContactPage,
    Gallery,
    News,
    Page,
    StringerCompany,
    StringerCompanyRating,
    StringerPage,
    VideoPage,
)

logger = logging.getLogger(__name__)


def news_list(request):
    """News list page."""
    news = News.objects.filter(is_published=True).order_by("-created_at")
    return render(request, "content/news_list.html", {"news_list": news})


def news_detail(request, slug):
    """News detail page with gallery and comments."""
    news = get_object_or_404(
        News.objects.prefetch_related("photos"),
        slug=slug,
        is_published=True,
    )
    # Increment views (only for GET)
    if request.method == "GET":
        news.views_count += 1
        news.save(update_fields=["views_count"])

    # Comments
    ct = ContentType.objects.get_for_model(News)
    comments = (
        Comment.objects.filter(
            content_type=ct,
            object_id=news.pk,
        )
        .select_related("author__user")
        .order_by("-created_at")
    )

    form = NewsCommentForm()
    if request.method == "POST" and request.POST.get("action") == "comment":
        form = NewsCommentForm(request.POST)
        if form.is_valid():
            if not request.user.is_authenticated:
                messages.error(request, "Войдите, чтобы оставить комментарий.")
                return redirect("login")
            player = Player.objects.filter(user=request.user).first()
            if player is None:
                messages.error(
                    request,
                    "Создайте профиль игрока, чтобы оставлять комментарии.",
                )
                return redirect("profile_edit")
            comment = Comment.objects.create(
                content_type=ct,
                object_id=news.pk,
                author=player,
                text=form.cleaned_data["text"].strip(),
                is_approved=True,
            )
            try:
                from apps.core.telegram_notify import notify_news_comment

                notify_news_comment(comment, news)
            except Exception as e:
                logger.warning("Telegram notify for news comment failed: %s", e)
            messages.success(request, "Комментарий добавлен.")
            return redirect("news_detail", slug=news.slug)

    return render(
        request,
        "content/news_detail.html",
        {"news": news, "comments": comments, "comment_form": form},
    )


def gallery_list(request):
    """Gallery list page."""
    galleries = Gallery.objects.filter(is_published=True).prefetch_related("photos")
    return render(request, "content/gallery_list.html", {"galleries": galleries})


def gallery_detail(request, slug):
    """Gallery detail page."""
    gallery = get_object_or_404(
        Gallery.objects.prefetch_related("photos"), slug=slug, is_published=True
    )
    return render(request, "content/gallery_detail.html", {"gallery": gallery})


def page_detail(request, slug):
    """Static page detail. Содержимое (Page.content) поддерживает Markdown."""
    page = get_object_or_404(Page, slug=slug, is_published=True)
    content_html = markdown.markdown(page.content or "", extensions=["extra"])
    return render(
        request,
        "content/page_detail.html",
        {"page": page, "content_html": content_html},
    )


def about_us(request):
    """
    "О нас" page with editable content and comments.
    Заголовок "О НАС" фиксирован в шаблоне.
    """
    about = AboutUs.get_singleton()
    body_html = markdown.markdown(about.body or "", extensions=["extra"])

    # Comments
    ct = ContentType.objects.get_for_model(AboutUs)
    comments = (
        Comment.objects.filter(
            content_type=ct,
            object_id=about.pk,
        )
        .select_related("author__user")
        .order_by("-created_at")
    )

    # Comment form
    form = AboutUsCommentForm()
    if request.method == "POST":
        form = AboutUsCommentForm(request.POST)
        if form.is_valid():
            if not request.user.is_authenticated:
                messages.error(request, "Войдите, чтобы оставить комментарий.")
                return redirect("login")
            player = Player.objects.filter(user=request.user).first()
            if player is None:
                messages.error(
                    request,
                    "Создайте профиль игрока, чтобы оставлять комментарии.",
                )
                return redirect("profile_edit")
            comment = Comment.objects.create(
                content_type=ct,
                object_id=about.pk,
                author=player,
                text=form.cleaned_data["text"].strip(),
                is_approved=True,
            )
            try:
                from apps.core.telegram_notify import notify_about_us_comment

                notify_about_us_comment(comment)
            except Exception as e:
                logger.warning("Telegram notify for About Us comment failed: %s", e)
            messages.success(
                request,
                "Комментарий отправлен на модерацию. Он появится после одобрения.",
            )
            return redirect("about_us")

    context = {
        "about": about,
        "body_html": body_html,
        "comments": comments,
        "comment_form": form,
    }
    return render(request, "content/about_us.html", context)


def contacts(request):
    """Страница «Контакты» с редактируемыми способами связи."""
    contact_page = ContactPage.get_singleton()
    intro_html = markdown.markdown(contact_page.intro_text or "", extensions=["extra"])
    items = contact_page.contact_items.order_by("order", "id")
    context = {
        "contact_page": contact_page,
        "intro_html": intro_html,
        "contact_items": items,
    }
    return render(request, "content/contacts.html", context)


def videos(request):
    """Страница «Видео» с прямыми трансляциями и плейлистом."""
    video_page = VideoPage.get_singleton()
    live_streams = video_page.live_streams.filter(is_active=True).order_by(
        "order", "-created_at"
    )
    videos = video_page.videos.filter(is_published=True).order_by(
        "order", "-created_at"
    )

    # Увеличиваем счетчик просмотров при просмотре видео (через AJAX или при клике)

    context = {
        "video_page": video_page,
        "live_streams": live_streams,
        "videos": videos,
    }
    return render(request, "content/videos.html", context)


def stringers(request):
    """Страница «Стрингеры» со списком компаний по натяжке струн."""
    stringer_page = StringerPage.get_singleton()

    # Если страница отключена, возвращаем 404
    if not stringer_page.is_enabled:
        from django.http import Http404

        raise Http404("Страница отключена")

    companies = (
        stringer_page.companies.filter(is_active=True)
        .prefetch_related("photos", "ratings", "ratings__user")
        .order_by("order", "name")
    )

    # Добавляем рейтинг и информацию о наличии оценки пользователя для каждой компании
    user_rated_company_ids = set()
    if request.user.is_authenticated:
        user_rated_company_ids = set(
            StringerCompanyRating.objects.filter(
                user=request.user, company__in=companies
            ).values_list("company_id", flat=True)
        )

    for company in companies:
        company.avg_rating = company.get_average_rating()
        company.rating_count = company.get_rating_count()
        company.user_has_rated = company.id in user_rated_company_ids

    context = {
        "stringer_page": stringer_page,
        "companies": companies,
    }
    return render(request, "content/stringers.html", context)


def stringer_detail(request, pk):
    """Детальная страница компании стрингеров."""
    company = get_object_or_404(
        StringerCompany.objects.prefetch_related("photos", "ratings", "ratings__user"),
        pk=pk,
        is_active=True,
    )

    # Рейтинг компании
    company.avg_rating = company.get_average_rating()
    company.rating_count = company.get_rating_count()

    # Проверяем, есть ли оценка от текущего пользователя
    user_has_rated = False
    user_rating = None
    if request.user.is_authenticated:
        user_rating = StringerCompanyRating.objects.filter(
            company=company, user=request.user
        ).first()
        user_has_rated = user_rating is not None

    # Получаем все оценки с комментариями
    ratings = company.ratings.select_related("user").order_by("-created_at")

    context = {
        "company": company,
        "user_has_rated": user_has_rated,
        "user_rating": user_rating,
        "ratings": ratings,
    }
    return render(request, "content/stringer_detail.html", context)


@login_required
@require_POST
def stringer_rate(request):
    """Оценка компании стрингеров."""
    company_id = request.POST.get("company_id")
    score = request.POST.get("score")
    comment = request.POST.get("comment", "").strip()

    if not company_id or not score:
        messages.error(request, "Не указаны обязательные поля.")
        return redirect("stringers")

    try:
        company = get_object_or_404(StringerCompany, pk=company_id, is_active=True)
        score = int(score)
        if not (1 <= score <= 5):
            messages.error(request, "Оценка должна быть от 1 до 5.")
            return redirect("stringer_detail", pk=company_id)
    except (ValueError, TypeError):
        messages.error(request, "Неверная оценка.")
        return redirect("stringers")

    # Создаем или обновляем оценку
    rating, created = StringerCompanyRating.objects.update_or_create(
        company=company,
        user=request.user,
        defaults={"score": score, "comment": comment},
    )

    try:
        from apps.core.telegram_notify import notify_stringer_rating

        notify_stringer_rating(rating, created=created)
    except Exception:
        pass

    if created:
        messages.success(request, f"Спасибо! Ваша оценка для {company.name} сохранена.")
    else:
        messages.success(request, f"Ваша оценка для {company.name} обновлена.")

    return redirect("stringer_detail", pk=company_id)
