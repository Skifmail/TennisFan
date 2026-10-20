"""Проверка заявок стрингеров в административной панели."""

from typing import cast

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse

from apps.users.models import User

from .models import StringerApplication, StringerApplicationPhoto
from .stringer_services import review_stringer_application


class StringerApplicationPhotoInline(admin.TabularInline):
    """Фотографии, приложенные заявителем."""

    model = StringerApplicationPhoto
    extra = 0
    fields = ("image", "caption")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(
        self, request: HttpRequest, obj: StringerApplication | None = None
    ) -> bool:
        return False


@admin.register(StringerApplication)
class StringerApplicationAdmin(admin.ModelAdmin):
    """Показывает заявку целиком и публикует компанию только через действие."""

    list_display = ("name", "applicant", "address", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "address", "email", "phone")
    readonly_fields = (
        "applicant",
        "name",
        "address",
        "price",
        "description",
        "contact_name",
        "email",
        "phone",
        "telegram",
        "whatsapp",
        "max_contact",
        "status",
        "company",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )
    inlines = (StringerApplicationPhotoInline,)
    actions = ("approve_applications", "reject_applications")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    @admin.action(
        description="Одобрить и опубликовать компании", permissions=["change"]
    )
    def approve_applications(
        self, request: HttpRequest, queryset: QuerySet[StringerApplication]
    ) -> None:
        """Публикует ожидающие заявки без повторного создания компаний."""
        for application in queryset:
            review_stringer_application(
                application.pk, actor=cast(User, request.user), approve=True
            )
        self.message_user(
            request,
            "Ожидающие заявки одобрены, компании опубликованы.",
            messages.SUCCESS,
        )

    @admin.action(description="Отклонить заявки", permissions=["change"])
    def reject_applications(
        self, request: HttpRequest, queryset: QuerySet[StringerApplication]
    ) -> None:
        """Отклоняет ожидающие заявки."""
        for application in queryset:
            review_stringer_application(
                application.pk, actor=cast(User, request.user), approve=False
            )
        self.message_user(request, "Ожидающие заявки отклонены.", messages.SUCCESS)

    def response_change(
        self, request: HttpRequest, obj: StringerApplication
    ) -> HttpResponse:
        """Позволяет принять решение непосредственно со страницы заявки."""
        if self.has_change_permission(request, obj):
            if "_approve" in request.POST or "_reject" in request.POST:
                review_stringer_application(
                    obj.pk,
                    actor=cast(User, request.user),
                    approve="_approve" in request.POST,
                )
                self.message_user(
                    request, "Решение по заявке сохранено.", messages.SUCCESS
                )
        return super().response_change(request, obj)

    change_form_template = "admin/content/stringerapplication/change_form.html"
