"""
Users admin configuration.
"""

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Notification, Player, SkillLevel, User
from .skill_levels import skill_with_ntrp


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for User model."""

    list_display = (
        "email",
        "first_name",
        "last_name",
        "phone",
        "is_staff",
        "is_active",
    )
    list_filter = ("is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name", "phone")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Персональная информация", {"fields": ("first_name", "last_name", "phone")}),
        (
            "Права доступа",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                ),
            },
        ),
    )


class PlayerAdminForm(forms.ModelForm):
    """Форма редактирования игрока с числовым отображением уровней силы в селекте."""

    skill_level = forms.ChoiceField(
        choices=[(code, skill_with_ntrp(code)) for code, _ in SkillLevel.choices],
        label="Уровень силы",
        required=True,
    )

    class Meta:
        model = Player
        fields = "__all__"


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    """Admin configuration for Player model."""

    form = PlayerAdminForm

    list_display = (
        "user",
        "is_bye",
        "city",
        "skill_level_with_ntrp",
        "gender",
        "ntrp_level",
        "total_points",
        "matches_played",
        "matches_won",
        "is_verified",
        "is_legend",
    )

    @admin.display(description="Уровень силы")
    def skill_level_with_ntrp(self, obj: Player) -> str:
        """Возвращает уровень силы с числовым диапазоном NTRP."""
        return skill_with_ntrp(obj.skill_level) if obj.skill_level else ""

    list_filter = (
        "city",
        "skill_level",
        "gender",
        "forehand",
        "is_verified",
        "is_legend",
        "is_bye",
    )
    search_fields = ("user__email", "user__first_name", "user__last_name")
    list_editable = ("is_verified", "is_legend")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Пользователь", {"fields": ("user", "avatar")}),
        (
            "Основная информация",
            {
                "fields": (
                    "skill_level",
                    "birth_date",
                    "gender",
                    "forehand",
                    "city",
                    "age",
                )
            },
        ),
        ("О себе", {"fields": ("bio",)}),
        ("Уровень силы", {"fields": ("ntrp_level",)}),
        ("Контакты", {"fields": ("telegram", "whatsapp", "max_contact")}),
        ("Статистика", {"fields": ("total_points", "matches_played", "matches_won")}),
        ("Статус", {"fields": ("is_verified", "is_legend")}),
        ("Даты", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Admin for notifications."""

    list_display = ("user", "message", "is_read", "created_at")
    list_filter = ("is_read",)
    search_fields = ("message", "user__email")
