"""
Main URL configuration for TennisFan project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.telegram_bot.views import admin_broadcast

urlpatterns = [
    path("admin/telegram-broadcast/", admin_broadcast, name="admin_telegram_broadcast"),
    path('admin/', admin.site.urls),
    path('', include('apps.core.urls')),
    path('users/', include('apps.users.urls')),
    path('tournaments/', include('apps.tournaments.urls')),
    path('courts/', include('apps.courts.urls')),
    path('sparring/', include('apps.sparring.urls')),
    path('training/', include('apps.training.urls')),
    path('news/', include('apps.content.urls_news')),
    path('gallery/', include('apps.content.urls_gallery')),
    path('pages/', include('apps.content.urls_pages')),
    path('subscriptions/', include('apps.subscriptions.urls')),
    path('payments/', include('apps.payments.urls')),
    path('legal/', include('apps.legal.urls')),
    path('about/', include('apps.content.urls_about')),
    path('contacts/', include('apps.content.urls_contacts')),
    path('shop/', include('apps.shop.urls')),
    path('telegram/', include('apps.telegram_bot.urls')),
]

# Serve media files (only if using local filesystem storage, not Cloudinary)
if settings.MEDIA_ROOT is not None:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Serve static files via Django only in DEBUG (prod uses WhiteNoise)
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

# Admin site customization
admin.site.site_header = "TennisFan - Админ-панель"
admin.site.site_title = "TennisFan Admin"
admin.site.index_title = "Управление сайтом"
