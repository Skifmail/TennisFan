"""
Main URL configuration for TennisFan project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps import views as sitemap_views
from django.urls import include, path

from apps.core.views import robots_txt
from config.sitemaps import StaticViewSitemap

urlpatterns = [
    path(f"{settings.ADMIN_URL}/", admin.site.urls),
    path(
        "sitemap.xml",
        sitemap_views.sitemap,
        {"sitemaps": {"static": StaticViewSitemap}},
        name="sitemap",
    ),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("", include("apps.core.urls")),
    path("users/", include("apps.users.urls")),
    path("ratings/", include("apps.player_ratings.urls")),
    path("tournaments/", include("apps.tournaments.urls")),
    path("courts/", include("apps.courts.urls")),
    path("sparring/", include("apps.sparring.urls")),
    path("training/", include("apps.training.urls")),
    path("news/", include("apps.content.urls_news")),
    path("gallery/", include("apps.content.urls_gallery")),
    path("pages/", include("apps.content.urls_pages")),
    path("videos/", include("apps.content.urls_videos")),
    path("stringers/", include("apps.content.urls_stringers")),
    path("subscriptions/", include("apps.subscriptions.urls")),
    path("payments/", include("apps.payments.urls")),
    path("legal/", include("apps.legal.urls")),
    path("about/", include("apps.content.urls_about")),
    path("contacts/", include("apps.content.urls_contacts")),
    path("shop/", include("apps.shop.urls")),
    path("telegram/", include("apps.telegram_bot.urls")),
    path("club/", include("apps.clubs.urls")),
]

if settings.DEBUG and getattr(settings, "PROFILING", False):
    urlpatterns += [
        path("__debug__/", include("debug_toolbar.urls")),
        path("silk/", include("silk.urls", namespace="silk")),
    ]

# Serve media files (only if using local filesystem storage, not Cloudinary)
if settings.MEDIA_ROOT is not None:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Serve static files via Django only in DEBUG (prod uses WhiteNoise)
if settings.DEBUG:
    urlpatterns += static(
        settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0]
    )

# Admin site customization
admin.site.site_header = "TennisFan - Админ-панель"
admin.site.site_title = "TennisFan Admin"
admin.site.index_title = "Управление сайтом"
