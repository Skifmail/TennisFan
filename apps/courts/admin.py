"""
Courts admin configuration.
"""

import logging

from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html

from .geocoder import geocode_address, _normalize_address_for_geocode
from .models import Court, CourtApplication, CourtApplicationStatus, CourtRating

logger = logging.getLogger(__name__)


def _get_geocoder_api_key() -> str:
    """API-ключ для Geocoder: отдельный или общий с картами."""
    return getattr(settings, "YANDEX_GEOCODER_API_KEY", None) or getattr(
        settings, "YANDEX_MAPS_API_KEY", ""
    )


def _get_geocoder_referer() -> str:
    """Referer для запросов к Yandex Geocoder (если у ключа ограничение по Referer)."""
    return getattr(settings, "YANDEX_GEOCODER_REFERER", "") or ""


def _geocode_court(court: Court) -> bool:
    """Получить координаты корта по адресу через Yandex Geocoder. Возвращает True, если координаты установлены."""
    full_address = _normalize_address_for_geocode(court.city or "", court.address or "")
    if not full_address.replace(",", "").strip():
        return False
    api_key = _get_geocoder_api_key()
    referer = _get_geocoder_referer()
    lat, lon = geocode_address(
        full_address,
        api_key=api_key or "",
        referer=referer or None,
        hint_city=(court.city or "").strip() or None,
    )
    if lat is None or lon is None:
        return False
    court.latitude = lat
    court.longitude = lon
    return True


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    """Admin for Court model."""

    list_display = (
        "name",
        "city",
        "surface",
        "courts_count",
        "is_indoor",
        "price_per_hour",
        "is_active",
    )
    list_filter = ("city", "surface", "is_indoor", "has_lighting", "is_active")
    search_fields = ("name", "address")
    list_editable = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}
    actions = ["geocode_selected_courts"]

    fieldsets = (
        (
            None,
            {
                "fields": ("name", "slug", "city", "address", "district", "description"),
                "description": "Для точной метки на карте: в «Город» — только название города (например: Сочи). В «Адрес» — улица и номер дома (например: Курортный проспект, 45). Не дублируйте город в поле «Адрес».",
            },
        ),
        ("Характеристики", {"fields": ("surface", "courts_count", "has_lighting", "is_indoor")}),
        ("Особенности", {"fields": ("sells_balls", "sells_water", "multiple_payment_methods")}),
        ("Контакты", {"fields": ("phone", "whatsapp", "website")}),
        ("Карта", {
            "fields": ("latitude", "longitude"),
            "description": "Координаты подставляются по адресу при сохранении (Yandex Geocoder). Либо укажите вручную или действие «Получить координаты по адресу» в списке кортов.",
        }),
        ("Цена и фото", {"fields": ("price_per_hour", "image")}),
        ("Статус", {"fields": ("is_active",)}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Автогеокодирование: если адрес есть, а координат нет — запросить по API
        if obj.address and (obj.latitude is None or obj.longitude is None):
            if _geocode_court(obj):
                obj.save(update_fields=["latitude", "longitude"])
                self.message_user(
                    request,
                    "Координаты получены по адресу и сохранены.",
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    "Не удалось получить координаты по адресу. Проверьте адрес или укажите координаты вручную.",
                    messages.WARNING,
                )

    @admin.action(description="Получить координаты по адресу")
    def geocode_selected_courts(self, request, queryset):
        ok = 0
        for court in queryset:
            if court.address and _geocode_court(court):
                court.save(update_fields=["latitude", "longitude"])
                ok += 1
        if ok:
            messages.success(request, f"Координаты получены для {ok} кортов.")
        else:
            messages.warning(request, "Не удалось получить координаты (проверьте адреса или ключ API).")


@admin.action(description="Одобрить и добавить корт на сайт")
def approve_court_applications(modeladmin, request, queryset):
    pending = queryset.filter(status=CourtApplicationStatus.PENDING)
    ok = 0
    err = 0
    for app in pending:
        try:
            app.approve_and_create_court()
            ok += 1
        except Exception as e:
            logger.exception("Ошибка одобрения заявки %s: %s", app.pk, e)
            err += 1
    if ok:
        messages.success(request, f"Одобрено заявок: {ok}. Корты добавлены на сайт.")
    if err:
        messages.error(request, f"Не удалось одобрить заявок: {err}. См. лог.")


@admin.action(description="Отклонить заявки")
def reject_court_applications(modeladmin, request, queryset):
    updated = queryset.filter(status=CourtApplicationStatus.PENDING).update(
        status=CourtApplicationStatus.REJECTED
    )
    if updated:
        messages.success(request, f"Отклонено заявок: {updated}.")


@admin.register(CourtApplication)
class CourtApplicationAdmin(admin.ModelAdmin):
    """Заявки на добавление корта. Одобренные превращаются в Court."""

    list_display = (
        "name",
        "city",
        "applicant_name",
        "applicant_email",
        "status_badge",
        "court_link",
        "created_at",
    )
    list_filter = ("status", "city", "surface")
    search_fields = ("name", "city", "address", "applicant_name", "applicant_email")
    list_display_links = ("name",)
    actions = [approve_court_applications, reject_court_applications]
    readonly_fields = ("status", "court", "created_at", "updated_at")

    fieldsets = (
        (
            "Заявитель",
            {"fields": ("applicant_name", "applicant_email", "applicant_phone")},
        ),
        (None, {"fields": ("name", "city", "address", "description")}),
        ("Характеристики", {"fields": ("surface", "courts_count", "has_lighting", "is_indoor")}),
        ("Контакты корта", {"fields": ("phone", "whatsapp", "website")}),
        ("Карта", {"fields": ("latitude", "longitude")}),
        ("Цена и фото", {"fields": ("price_per_hour", "image")}),
        ("Статус", {"fields": ("status", "court", "created_at", "updated_at")}),
    )

    def status_badge(self, obj):
        colors = {
            CourtApplicationStatus.PENDING: "#f0ad4e",
            CourtApplicationStatus.APPROVED: "#5cb85c",
            CourtApplicationStatus.REJECTED: "#d9534f",
        }
        c = colors.get(obj.status, "#999")
        return format_html(
            '<span style="background: {}; color: #fff; padding: 2px 8px; border-radius: 4px;">{}</span>',
            c,
            obj.get_status_display(),
        )

    status_badge.short_description = "Статус"

    def court_link(self, obj):
        if not obj.court_id:
            return "—"
        return format_html(
            '<a href="{}">{}</a>',
            f"/admin/courts/court/{obj.court_id}/change/",
            obj.court.name,
        )

    court_link.short_description = "Корт на сайте"


@admin.register(CourtRating)
class CourtRatingAdmin(admin.ModelAdmin):
    list_display = ("court", "user", "score", "updated_at")
    list_filter = ("score",)
    search_fields = ("court__name", "user__email")
    raw_id_fields = ("court", "user")
