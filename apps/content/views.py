"""
Content views - News, Gallery, Pages, About Us.
"""

import logging

import markdown
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, redirect, render

from apps.comments.models import Comment
from apps.users.models import Player

from .forms import AboutUsCommentForm
from .models import AboutUs, ContactItem, ContactPage, Gallery, News, Page

logger = logging.getLogger(__name__)


def news_list(request):
    """News list page."""
    news = News.objects.filter(is_published=True).order_by('-created_at')
    return render(request, 'content/news_list.html', {'news_list': news})


def news_detail(request, slug):
    """News detail page."""
    news = get_object_or_404(News, slug=slug, is_published=True)
    # Increment views
    news.views_count += 1
    news.save(update_fields=['views_count'])
    return render(request, 'content/news_detail.html', {'news': news})


def gallery_list(request):
    """Gallery list page."""
    galleries = Gallery.objects.filter(is_published=True).prefetch_related('photos')
    return render(request, 'content/gallery_list.html', {'galleries': galleries})


def gallery_detail(request, slug):
    """Gallery detail page."""
    gallery = get_object_or_404(
        Gallery.objects.prefetch_related('photos'),
        slug=slug,
        is_published=True
    )
    return render(request, 'content/gallery_detail.html', {'gallery': gallery})


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

    # Comments: only approved
    ct = ContentType.objects.get_for_model(AboutUs)
    comments = (
        Comment.objects.filter(
            content_type=ct,
            object_id=about.pk,
            is_approved=True,
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
                is_approved=False,  # Модерация в админке
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
