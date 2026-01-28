"""
Content views - News, Gallery, Pages.
"""

import markdown
from django.shortcuts import get_object_or_404, render

from .models import Gallery, News, Page


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
