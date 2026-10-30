"""Единая группа детской части платформы в Django-admin."""

from typing import ClassVar

import requests
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from apps.users.models import User

from .billing import execute_refund
from .forms import ProfileSupportForm, VerificationForm
from .lifecycle import (
    add_representative,
    correct_profile,
    revoke_guardian,
    transfer_to_adult,
)
from .models import (
    AuditEvent,
    ChildConsent,
    ChildProfile,
    GuardianLink,
    JuniorAppeal,
    JuniorAward,
    JuniorEntry,
    JuniorMatch,
    JuniorNotificationPolicy,
    JuniorPayment,
    JuniorRankingSnapshot,
    JuniorRefund,
    JuniorSeason,
    JuniorTournament,
    LegalRelease,
)
from .permissions import accessible_competitions, require_verifier
from .services import verify_guardian


@admin.register(ChildProfile)
class ChildProfileAdmin(admin.ModelAdmin):
    list_display: tuple[str, ...] = (
        "id",
        "first_name",
        "last_name",
        "city",
        "ball",
        "status",
        "support_link",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("status", "ball", "city")
    search_fields = ("first_name", "last_name")
    readonly_fields = (
        *tuple(field.name for field in ChildProfile._meta.fields),
        "support_link",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.has_perm("juniors.verify_representative"):
            return qs
        return qs.filter(
            guardians__user=request.user, guardians__status="verified"
        ).distinct()

    @admin.display(description="Операции с профилем")
    def support_link(self, obj):
        return format_html(
            '<a href="{}">Исправления, представители, передача →</a>',
            reverse("admin:juniors_profile_support", args=[obj.pk]),
        )

    def get_urls(self):
        return [
            path(
                "<int:pk>/support/",
                self.admin_site.admin_view(self.support_view),
                name="juniors_profile_support",
            )
        ] + super().get_urls()

    def support_view(self, request, pk):
        require_verifier(request.user)
        child = get_object_or_404(ChildProfile, pk=pk)
        form = ProfileSupportForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            try:
                if data["action"] == "add_guardian":
                    if not data["user_id"]:
                        raise ValidationError("Укажите ID аккаунта представителя.")
                    link = add_representative(
                        actor=request.user,
                        child_id=pk,
                        user_id=data["user_id"],
                        relation=data["relation"],
                    )
                    return redirect("admin:juniors_verify", pk=link.pk)
                if data["action"] == "transfer":
                    if not data["user_id"]:
                        raise ValidationError("Укажите ID аккаунта самого участника.")
                    transfer_to_adult(
                        actor=request.user,
                        child_id=pk,
                        user_id=data["user_id"],
                        evidence=data["evidence"],
                    )
                else:
                    changes = {
                        key: data[key]
                        for key in (
                            "first_name",
                            "last_name",
                            "birth_date",
                            "city",
                            "ball",
                        )
                        if data[key]
                    }
                    correct_profile(
                        actor=request.user,
                        child_id=pk,
                        data=changes,
                        evidence=data["evidence"],
                        reason=data["reason"],
                    )
            except (ValidationError, User.DoesNotExist) as exc:
                form.add_error(
                    None,
                    exc if isinstance(exc, ValidationError) else "Аккаунт не найден.",
                )
            else:
                self.message_user(request, "Операция выполнена и записана в журнал.")
                return redirect("admin:juniors_childprofile_change", pk)
        duplicates = ChildProfile.objects.filter(
            first_name=child.first_name,
            last_name=child.last_name,
            birth_date=child.birth_date,
        ).exclude(pk=pk)
        return TemplateResponse(
            request,
            "admin/juniors/support.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Проверенные операции: {child}",
                "opts": self.model._meta,
                "form": form,
                "duplicates": duplicates,
            },
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GuardianLink)
class GuardianLinkAdmin(admin.ModelAdmin):
    list_display: tuple[str, ...] = (
        "child",
        "user",
        "relation",
        "status",
        "verified_at",
        "verification_link",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("status", "relation")
    search_fields = ("child__first_name", "child__last_name", "user__email")
    readonly_fields = tuple(field.name for field in GuardianLink._meta.fields)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return (
            qs
            if request.user.has_perm("juniors.verify_representative")
            else qs.filter(user=request.user)
        )

    @admin.display(description="Проверка")
    def verification_link(self, obj):
        return format_html(
            '<a href="{}">Рассмотреть</a>',
            reverse("admin:juniors_verify", args=[obj.pk]),
        )

    def get_urls(self):
        return [
            path(
                "<int:pk>/verify/",
                self.admin_site.admin_view(self.verify_view),
                name="juniors_verify",
            )
        ] + super().get_urls()

    def verify_view(self, request, pk):
        require_verifier(request.user)
        obj = get_object_or_404(
            GuardianLink.objects.select_related("child", "user"), pk=pk
        )
        form = VerificationForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                if form.cleaned_data["approved"] == "revoke":
                    revoke_guardian(
                        actor=request.user,
                        link_id=obj.pk,
                        reason=form.cleaned_data["reason"],
                    )
                else:
                    verify_guardian(
                        actor=request.user,
                        link_id=obj.pk,
                        approved=form.cleaned_data["approved"] == "yes",
                        evidence=form.cleaned_data["evidence"],
                        reason=form.cleaned_data["reason"],
                    )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                self.message_user(request, "Решение сохранено.")
                return redirect("admin:juniors_guardianlink_changelist")
        return TemplateResponse(
            request,
            "admin/juniors/verify.html",
            {
                **self.admin_site.each_context(request),
                "title": "Проверка законного представителя",
                "opts": self.model._meta,
                "object": obj,
                "form": form,
            },
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display: tuple[str, ...] = ("created_at", "actor", "child", "action")
    list_filter: ClassVar[tuple[str, ...]] = ("action",)
    readonly_fields = tuple(field.name for field in AuditEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class CompetitionReadOnlyAdmin(AuditEventAdmin):
    tournament_lookup: ClassVar[str] = "tournament__organizer"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        prefix = self.tournament_lookup.removesuffix("__organizer")
        return qs.filter(**{f"{prefix}__in": accessible_competitions(request.user)})


@admin.register(LegalRelease)
class LegalReleaseAdmin(admin.ModelAdmin):
    list_display: tuple[str, ...] = ("version", "reviewed", "is_current", "created_at")
    readonly_fields = ("created_at",)


@admin.register(ChildConsent)
class ChildConsentAdmin(AuditEventAdmin):
    list_display: tuple[str, ...] = (
        "guardian",
        "kind",
        "release",
        "accepted_at",
        "withdrawn_at",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("kind",)
    readonly_fields = tuple(field.name for field in ChildConsent._meta.fields)


@admin.register(JuniorSeason)
class JuniorSeasonAdmin(admin.ModelAdmin):
    list_display: tuple[str, ...] = (
        "name",
        "starts_on",
        "ends_on",
        "best_results",
        "rules_version",
    )


@admin.register(JuniorTournament)
class JuniorTournamentAdmin(admin.ModelAdmin):
    list_display: tuple[str, ...] = (
        "name",
        "ball",
        "min_age",
        "max_age",
        "city",
        "starts_at",
        "status",
        "manage_link",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("status", "ball", "season", "city")
    search_fields = ("name", "event_name", "city")
    readonly_fields = ("status", "created_at")
    fieldsets = (
        (
            "Соревнование",
            {
                "fields": (
                    "name",
                    "event_name",
                    "city",
                    "venue",
                    "club",
                    "organizer",
                    "season",
                    "status",
                )
            },
        ),
        (
            "Допуск и спортивные правила",
            {
                "fields": (
                    "ball",
                    "min_age",
                    "max_age",
                    "age_rule",
                    "gender",
                    "format",
                    "score_format",
                    "capacity",
                )
            },
        ),
        (
            "Даты и взнос",
            {"fields": ("starts_at", "registration_ends_at", "entry_fee")},
        ),
        (
            "Регламент и рейтинг",
            {"fields": ("regulation", "regulation_version", "ranked", "created_at")},
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(pk__in=accessible_competitions(request.user))

    @admin.display(description="Проведение")
    def manage_link(self, obj):
        return format_html(
            '<a href="{}">Участники и результаты →</a>',
            reverse("juniors:manage", args=[obj.pk]),
        )

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.organizer = request.user
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(
            actor=request.user,
            action="tournament.updated" if change else "tournament.created",
            details={"tournament_id": obj.pk},
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "club" and not request.user.is_superuser:
            from apps.clubs.models import Club

            kwargs["queryset"] = Club.objects.filter(
                members__user=request.user,
                members__role__in=["admin", "manager"],
                members__status="active",
            ).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        return (
            (*self.readonly_fields, "organizer")
            if not request.user.is_superuser
            else self.readonly_fields
        )


@admin.register(JuniorEntry)
class JuniorEntryAdmin(CompetitionReadOnlyAdmin):
    list_display: tuple[str, ...] = (
        "tournament",
        "child",
        "guardian",
        "status",
        "amount",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("status", "tournament")
    readonly_fields = tuple(field.name for field in JuniorEntry._meta.fields)


@admin.register(JuniorPayment)
class JuniorPaymentAdmin(CompetitionReadOnlyAdmin):
    tournament_lookup = "entry__tournament__organizer"
    list_display: tuple[str, ...] = ("entry", "payer", "amount", "status", "created_at")
    list_filter: ClassVar[tuple[str, ...]] = ("status",)
    readonly_fields = tuple(field.name for field in JuniorPayment._meta.fields)


@admin.register(JuniorRefund)
class JuniorRefundAdmin(CompetitionReadOnlyAdmin):
    tournament_lookup = "payment__entry__tournament__organizer"
    list_display: tuple[str, ...] = ("payment", "reason", "status", "created_at")
    list_filter: ClassVar[tuple[str, ...]] = ("status",)
    readonly_fields = tuple(field.name for field in JuniorRefund._meta.fields)
    actions = ["approve_refunds"]

    def has_approve_permission(self, request):
        return request.user.has_perm("juniors.manage_competition")

    @admin.action(
        description="Одобрить и выполнить полный возврат", permissions=["approve"]
    )
    def approve_refunds(self, request, queryset):
        for refund in queryset:
            try:
                execute_refund(actor=request.user, refund_id=refund.pk)
            except (ValidationError, requests.RequestException, ValueError) as exc:
                self.message_user(
                    request,
                    f"Возврат #{refund.pk} не завершён: {type(exc).__name__}. Проверьте журнал и повторите с тем же запросом.",
                    level="error",
                )
            else:
                self.message_user(request, f"Возврат #{refund.pk} обработан.")


@admin.register(JuniorMatch)
class JuniorMatchAdmin(CompetitionReadOnlyAdmin):
    list_display: tuple[str, ...] = (
        "tournament",
        "round_number",
        "position",
        "status",
        "winner",
        "result_link",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("tournament", "status")
    readonly_fields = tuple(field.name for field in JuniorMatch._meta.fields)

    @admin.display(description="Результат")
    def result_link(self, obj):
        return format_html(
            '<a href="{}">Ввести / исправить</a>',
            reverse("juniors:result", args=[obj.pk]),
        )


@admin.register(JuniorAward)
class JuniorAwardAdmin(CompetitionReadOnlyAdmin):
    list_display: tuple[str, ...] = (
        "child",
        "tournament",
        "ball",
        "place",
        "points",
        "season",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("season", "ball")
    readonly_fields = tuple(field.name for field in JuniorAward._meta.fields)


@admin.register(JuniorAppeal)
class JuniorAppealAdmin(CompetitionReadOnlyAdmin):
    list_display: tuple[str, ...] = (
        "match",
        "guardian",
        "status",
        "created_at",
        "manage_link",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("status", "tournament")
    readonly_fields = tuple(field.name for field in JuniorAppeal._meta.fields)

    @admin.display(description="Рассмотрение")
    def manage_link(self, obj):
        return format_html(
            '<a href="{}">Кабинет организатора →</a>',
            reverse("juniors:manage", args=[obj.tournament_id]),
        )


@admin.register(JuniorNotificationPolicy)
class JuniorNotificationPolicyAdmin(admin.ModelAdmin):
    list_display: tuple[str, ...] = (
        "title",
        "email_enabled",
        "push_enabled",
        "inbox_enabled",
    )
    list_editable = ("email_enabled", "push_enabled", "inbox_enabled")
    readonly_fields = ("key", "title", "group", "description")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(key__startswith="junior.")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(JuniorRankingSnapshot)
class JuniorRankingSnapshotAdmin(CompetitionReadOnlyAdmin):
    list_display: tuple[str, ...] = (
        "child",
        "season",
        "ball",
        "position",
        "points",
        "valid",
        "created_at",
    )
    list_filter: ClassVar[tuple[str, ...]] = ("season", "ball", "valid")
    readonly_fields = tuple(field.name for field in JuniorRankingSnapshot._meta.fields)
