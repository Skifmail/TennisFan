"""
Users admin configuration.
"""

from typing import cast

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.safestring import mark_safe

from .models import Notification, NtrpTestResult, Player, SkillLevel, User
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


class NtrpTestResultInline(admin.TabularInline):
    """Inline-отображение результатов теста NTRP в карточке игрока."""

    model = NtrpTestResult
    extra = 0
    can_delete = False
    readonly_fields = (
        "created_at",
        "source",
        "total_score",
        "level",
        "starting_points",
        "applied_to_rating",
        "answers_display",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None) -> bool:
        """Запретить ручное добавление результатов теста из админки."""
        return False

    @admin.display(description="Ответы по вопросам")
    def answers_display(self, obj: NtrpTestResult) -> str:
        """Отформатированные ответы на вопросы теста для отображения в админке.

        Табличное представление, адаптированное под мобильные устройства.

        Args:
            obj (NtrpTestResult): Экземпляр результата теста.

        Returns:
            str: HTML-разметка таблицы с ответами.
        """
        if not obj or not obj.answers:
            return "Ответы отсутствуют."

        rows: list[str] = []
        for raw in obj.answers:
            index_raw = raw.get("index")
            num = index_raw + 1 if isinstance(index_raw, int) else "—"
            score = raw.get("option_score")
            label = raw.get("option_label") or ""
            question = raw.get("question") or ""
            # Короткий заголовок вопроса + сам ответ
            answer_text = f"<div class='ntrp-answer-question'>{question}</div><div class='ntrp-answer-text'>{label}</div>"
            rows.append(
                f"<tr>"
                f"<td class='ntrp-cell-index'>{num}</td>"
                f"<td class='ntrp-cell-score'>{score if score is not None else '—'}</td>"
                f"<td class='ntrp-cell-answer'>{answer_text}</td>"
                f"</tr>"
            )

        table_html = """
<style>
  .ntrp-answers-table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.5rem 0;
    font-size: 13px;
  }
  .ntrp-answers-table th,
  .ntrp-answers-table td {
    border: 1px solid #ddd;
    padding: 4px 6px;
    vertical-align: top;
  }
  .ntrp-answers-table th {
    background: #f5f5f5;
    font-weight: 600;
    text-align: left;
  }
  .ntrp-answer-question {
    font-weight: 600;
    margin-bottom: 2px;
  }
  .ntrp-answer-text {
    font-weight: 400;
  }
  @media (max-width: 768px) {
    .ntrp-answers-table thead {
      display: none;
    }
    .ntrp-answers-table,
    .ntrp-answers-table tbody,
    .ntrp-answers-table tr,
    .ntrp-answers-table td {
      display: block;
      width: 100%;
    }
    .ntrp-answers-table tr {
      margin-bottom: 8px;
      border: 1px solid #ddd;
      border-radius: 4px;
      overflow: hidden;
    }
    .ntrp-answers-table td {
      border: none;
      border-bottom: 1px solid #eee;
    }
    .ntrp-answers-table td:last-child {
      border-bottom: none;
    }
    .ntrp-cell-index::before {
      content: "№ вопроса: ";
      font-weight: 600;
    }
    .ntrp-cell-score::before {
      content: "Баллы: ";
      font-weight: 600;
    }
  }
</style>
<table class="ntrp-answers-table">
  <thead>
    <tr>
      <th>№</th>
      <th>Баллы</th>
      <th>Ответ</th>
    </tr>
  </thead>
  <tbody>
    __ROWS_PLACEHOLDER__
  </tbody>
</table>
"""

        table_html = table_html.replace("__ROWS_PLACEHOLDER__", "".join(rows))

        return cast(str, mark_safe(table_html))


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    """Admin configuration for Player model."""

    form = PlayerAdminForm
    inlines = (NtrpTestResultInline,)

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
