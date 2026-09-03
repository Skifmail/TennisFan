"""
Courts admin configuration.
"""

import logging

from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html

from .forms import CourtAdminForm, CourtApplicationAdminForm
from .geocoder import _normalize_address_for_geocode, geocode_address
from .models import (
    Court,
    CourtApplication,
    CourtApplicationStatus,
    CourtPhoto,
    CourtRating,
)
from .surfaces import CourtSurface

logger = logging.getLogger(__name__)


def _get_geocoder_api_key() -> str:
    """API-ключ для Geocoder: отдельный или общий с картами."""
    return (
        getattr(settings, "YANDEX_GEOCODER_API_KEY", None)
        or getattr(settings, "YANDEX_MAPS_API_KEY", "")
        or ""
    )


def _get_geocoder_referer() -> str:
    """Referer для запросов к Yandex Geocoder (если у ключа ограничение по Referer)."""
    return str(getattr(settings, "YANDEX_GEOCODER_REFERER", "") or "")


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


class CourtPhotoInline(admin.TabularInline):
    """Дополнительные фото корта (до 4 штук, вместе с основным фото — до 5)."""

    model = CourtPhoto
    extra = 0
    max_num = 4


class CourtSurfaceListFilter(admin.SimpleListFilter):
    """Фильтр списка кортов по каноническому покрытию."""

    title = "Покрытие"
    parameter_name = "surface_code"

    def lookups(self, request, model_admin):
        return CourtSurface.choices

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        from .surfaces import filter_courts_by_surfaces

        return filter_courts_by_surfaces(queryset, [value])


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    """Admin for Court model."""

    form = CourtAdminForm

    class Media:
        js = (
            "js/city_autocomplete.js",
            "js/admin_court_form.js",
            "js/admin_court_surfaces.js",
        )
        css = {"all": ("css/admin_court_surfaces.css",)}

    inlines = [CourtPhotoInline]

    list_display = (
        "name",
        "city",
        "geo_area",
        "surface",
        "courts_count",
        "is_indoor",
        "is_outdoor",
        "price_per_hour",
        "is_active",
    )
    list_filter = (
        "region",
        "geo_area",
        "city",
        CourtSurfaceListFilter,
        "is_indoor",
        "is_outdoor",
        "has_lighting",
        "racket_rental",
        "has_training",
        "is_active",
    )
    search_fields = ("name", "address")
    list_editable = ("is_active",)
    # Не используем prepopulated_fields: для кириллицы JS оставляет slug пустым,
    # форма падает с «Обязательное поле», а загруженные фото при этом пропадают.
    actions = ["geocode_selected_courts"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "slug",
                    "city",
                    "address",
                    "district",
                    "region",
                    "geo_area",
                    "description",
                ),
                "description": (
                    "URL (slug) можно оставить пустым — подставится из названия. "
                    "Для карты: в «Населённый пункт» только город (например: Сочи), "
                    "в «Адрес» — улица и дом без повторения города."
                ),
            },
        ),
        (
            "Характеристики",
            {
                "fields": (
                    "indoor_surfaces",
                    "outdoor_surfaces",
                    "courts_count",
                    "has_lighting",
                    "is_indoor",
                    "is_outdoor",
                )
            },
        ),
        (
            "Особенности",
            {
                "fields": (
                    "sells_balls",
                    "sells_water",
                    "multiple_payment_methods",
                    "racket_rental",
                    "has_parking",
                    "racket_stringing",
                    "has_training",
                )
            },
        ),
        ("Контакты", {"fields": ("phone", "working_hours", "whatsapp", "website")}),
        (
            "Карта",
            {
                "fields": ("latitude", "longitude"),
                "description": "Координаты подставляются по адресу при сохранении (Yandex Geocoder). Либо укажите вручную или действие «Получить координаты по адресу» в списке кортов.",
            },
        ),
        (
            "Цена и фото",
            {
                "fields": (
                    "price_per_hour",
                    "rental_price_min",
                    "rental_price_max",
                    "image",
                ),
                "description": (
                    "Сначала заполните все поля и нажмите «Сохранить», фото добавьте "
                    "через «Изменить» — так надёжнее. Если всё же грузите фото сразу: "
                    "выбирайте файлы в самом конце, прямо перед «Сохранить»."
                ),
            },
        ),
        ("Статус", {"fields": ("is_active",)}),
    )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """Показать понятное сообщение, если сохранение упало на валидации (фото сбросятся)."""
        response = super().changeform_view(request, object_id, form_url, extra_context)
        if request.method == "POST" and hasattr(response, "context_data"):
            adminform = (response.context_data or {}).get("adminform")
            form = getattr(adminform, "form", None)
            if form is not None and form.errors:
                messages.error(
                    request,
                    "Корт не сохранён из‑за ошибок в форме (смотрите красные подписи у полей). "
                    "Файлы фото браузер не возвращает после ошибки — выберите их заново "
                    "или сначала сохраните корт без фото.",
                )
        return response

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
            messages.warning(
                request,
                "Не удалось получить координаты (проверьте адреса или ключ API).",
            )


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
    pending = list(queryset.filter(status=CourtApplicationStatus.PENDING))
    updated = 0
    for app in pending:
        app.status = CourtApplicationStatus.REJECTED
        app.save(update_fields=["status", "updated_at"])
        updated += 1
        try:
            from apps.core.email_service import send_court_application_decision_email

            send_court_application_decision_email(app, approved=False)
        except Exception:
            logger.exception(
                "send_court_application_decision_email failed | application=%s",
                app.pk,
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
    list_filter = ("status", "city")
    search_fields = ("name", "city", "address", "applicant_name", "applicant_email")
    list_display_links = ("name",)
    actions = [approve_court_applications, reject_court_applications]
    readonly_fields = ("status", "court", "created_at", "updated_at")
    form = CourtApplicationAdminForm

    class Media:
        js = ("js/admin_court_surfaces.js",)
        css = {"all": ("css/admin_court_surfaces.css",)}

    fieldsets = (
        (
            "Заявитель",
            {"fields": ("applicant_name", "applicant_email", "applicant_phone")},
        ),
        (None, {"fields": ("name", "city", "address", "description")}),
        (
            "Характеристики",
            {
                "fields": (
                    "indoor_surfaces",
                    "outdoor_surfaces",
                    "courts_count",
                    "has_lighting",
                    "is_indoor",
                    "is_outdoor",
                )
            },
        ),
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
