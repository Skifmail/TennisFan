"""
Main URL configuration for TennisFan project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
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
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

# Admin site customization
admin.site.site_header = "TennisFan - Админ-панель"
admin.site.site_title = "TennisFan Admin"
admin.site.index_title = "Управление сайтом"
