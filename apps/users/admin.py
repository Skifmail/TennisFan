"""Users admin configuration."""

from decimal import Decimal
from typing import cast

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q, QuerySet, Sum
from django.http import HttpRequest
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from .models import Notification, NtrpTestResult, Player, SkillLevel, User
from .skill_levels import skill_with_ntrp


def _case_variants_for_cyrillic(value: str) -> list[str]:
    """
    Сгенерировать варианты регистра для поиска по кириллице.

    Поиск выполняется через case-sensitive `contains`, поэтому для регистронезависимости
    подставляем варианты: `lower/upper/title`.

    Args:
        value: Исходная строка токена поиска.

    Returns:
        Список уникальных вариантов, пригодных для `__contains`.
    """

    variants = [value, value.lower(), value.upper(), value.title()]
    # Убираем дубликаты с сохранением порядка.
    return list(dict.fromkeys(v for v in variants if v))


def _phone_variants_for_search(token: str) -> list[str]:
    """
    Нормализовать телефонный токен для поиска в `User.phone`.

    Поддерживаются варианты ввода:
    - `+79381138222`
    - `79381138222`
    - `9381138222`

    Args:
        token: Строка из поля поиска админки.

    Returns:
        Список вариантов, которые можно подставлять в `__contains`.
    """

    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        return []

    normalized_digits: str | None = None
    if len(digits) == 11:
        if digits.startswith("8"):
            normalized_digits = "7" + digits[1:]
        elif digits.startswith("7"):
            normalized_digits = digits
    elif len(digits) == 10:
        # Для РФ 10 цифр обычно начинаются с 9 (9XXXXXXXXX), приводим к 7XXXXXXXXXX.
        normalized_digits = "7" + digits

    if not normalized_digits:
        return []

    alt8_digits = "8" + normalized_digits[1:]
    variants = [
        f"+{normalized_digits}",
        normalized_digits,
        f"+{alt8_digits}",
        alt8_digits,
    ]
    return list(dict.fromkeys(variants))


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for User model."""

    readonly_fields = (
        "last_login",
        "date_joined",
        "legal_acceptances_summary",
        "subscription_summary",
        "payment_methods_summary",
        "payments_summary",
    )

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
            "Юридические фиксации",
            {"fields": ("legal_acceptances_summary",)},
        ),
        (
            "Подписка и автосписания",
            {"fields": ("subscription_summary", "payment_methods_summary")},
        ),
        (
            "Платежи",
            {"fields": ("payments_summary",)},
        ),
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

    def get_search_results(
        self,
        request: HttpRequest,
        queryset: QuerySet[User],
        search_term: str,
    ) -> tuple[QuerySet[User], bool]:
        """
        Выполнить поиск по email/ФИО/телефону с учетом регистра кириллицы.

        Args:
            request: Текущий HTTP-запрос админки.
            queryset: Базовый queryset для фильтрации.
            search_term: Строка поиска из формы админки.

        Returns:
            Кортеж (queryset, may_have_duplicates).
        """

        if not search_term:
            return queryset, False

        tokens = [t for t in search_term.split() if t]
        if not tokens:
            return queryset, False

        combined_q = Q()
        for token in tokens:
            email_q = Q(email__icontains=token)

            name_q = Q()
            for variant in _case_variants_for_cyrillic(token):
                name_q |= Q(last_name__contains=variant)
                name_q |= Q(first_name__contains=variant)

            token_q = email_q | name_q

            phone_variants = _phone_variants_for_search(token)
            if phone_variants:
                phone_q = Q()
                for variant in phone_variants:
                    phone_q |= Q(phone__contains=variant)
                token_q |= phone_q

            combined_q &= token_q

        return queryset.filter(combined_q), False

    @admin.display(description="Согласия и акцепты")
    def legal_acceptances_summary(self, obj: User | None) -> str:
        """Показать сводку по юридическим согласиям пользователя.

        Args:
            obj (User | None): Пользователь, открытый в админке.

        Returns:
            str: HTML-блок со списком юридических согласий.
        """
        if obj is None:
            return "Сохраните пользователя, чтобы увидеть историю согласий."

        from apps.core.models import LegalAcceptanceLog, TelegramTransferConsentLog

        legal_logs = LegalAcceptanceLog.objects.filter(user=obj).order_by(
            "-accepted_at"
        )[:10]
        telegram_logs = TelegramTransferConsentLog.objects.filter(user=obj).order_by(
            "-consented_at"
        )[:5]

        sections: list[str] = []

        if legal_logs:
            legal_rows = format_html_join(
                "",
                "<li><strong>{}</strong>: {}<br><span style='color:#666'>версия {}, источник {}, IP {}</span></li>",
                (
                    (
                        log.get_document_slug_display(),
                        log.accepted_at.strftime("%d.%m.%Y %H:%M"),
                        log.document_version,
                        log.source,
                        log.ip_address or "не указан",
                    )
                    for log in legal_logs
                ),
            )
            sections.append(
                str(
                    format_html(
                        "<div><strong>Сайт:</strong><ul style='margin:8px 0 0 18px'>{}</ul></div>",
                        legal_rows,
                    )
                )
            )
        else:
            sections.append("<div><strong>Сайт:</strong> фиксаций пока нет.</div>")

        if telegram_logs:
            telegram_rows = format_html_join(
                "",
                "<li>{} — версия {}, IP {}</li>",
                (
                    (
                        log.consented_at.strftime("%d.%m.%Y %H:%M"),
                        log.consent_version,
                        log.ip_address or "не указан",
                    )
                    for log in telegram_logs
                ),
            )
            sections.append(
                str(
                    format_html(
                        "<div style='margin-top:12px'><strong>Telegram:</strong><ul style='margin:8px 0 0 18px'>{}</ul></div>",
                        telegram_rows,
                    )
                )
            )
        else:
            sections.append(
                "<div style='margin-top:12px'><strong>Telegram:</strong> согласий нет.</div>"
            )

        return cast(str, mark_safe("".join(sections)))

    @admin.display(description="Подписка")
    def subscription_summary(self, obj: User | None) -> str:
        """Показать сводку по текущей подписке пользователя.

        Args:
            obj (User | None): Пользователь, открытый в админке.

        Returns:
            str: HTML-блок с данными о подписке.
        """
        if obj is None:
            return "Сохраните пользователя, чтобы увидеть подписку."

        try:
            subscription = obj.subscription
        except Exception:
            return "Подписка отсутствует."

        current_status = subscription.status_display
        cancelled_at = (
            subscription.cancelled_at.strftime("%d.%m.%Y %H:%M")
            if subscription.cancelled_at
            else "нет"
        )
        return cast(
            str,
            format_html(
                "<div>"
                "<div><strong>Текущий тариф:</strong> {}</div>"
                "<div><strong>Статус:</strong> {}</div>"
                "<div><strong>Начало:</strong> {}</div>"
                "<div><strong>Окончание:</strong> {}</div>"
                "<div><strong>Отменена:</strong> {}</div>"
                "<div><strong>Баланс FT:</strong> {}</div>"
                "<div><strong>Населённый пункт покупки:</strong> {}</div>"
                "</div>",
                subscription.tier.get_name_display(),
                current_status,
                subscription.start_date.strftime("%d.%m.%Y %H:%M"),
                subscription.end_date.strftime("%d.%m.%Y %H:%M"),
                cancelled_at,
                subscription.fancoin_balance,
                subscription.purchase_city or "не указан",
            ),
        )

    @admin.display(description="Сохранённые способы оплаты")
    def payment_methods_summary(self, obj: User | None) -> str:
        """Показать сохранённые карты и статус автосписания.

        Args:
            obj (User | None): Пользователь, открытый в админке.

        Returns:
            str: HTML-блок с картами пользователя.
        """
        if obj is None:
            return "Сохраните пользователя, чтобы увидеть способы оплаты."

        from apps.payments.models import SavedPaymentMethod

        methods = SavedPaymentMethod.objects.filter(user=obj).order_by("-created_at")
        if not methods:
            return "Сохранённых способов оплаты нет."

        rows = format_html_join(
            "",
            "<li><strong>{}</strong> — активна: {}, по подписке: {}, создана: {}</li>",
            (
                (
                    str(method),
                    "да" if method.is_active else "нет",
                    "да" if method.is_default_for_subscriptions else "нет",
                    method.created_at.strftime("%d.%m.%Y %H:%M"),
                )
                for method in methods
            ),
        )
        return cast(
            str,
            format_html("<ul style='margin:0 0 0 18px'>{}</ul>", rows),
        )

    @admin.display(description="История оплат")
    def payments_summary(self, obj: User | None) -> str:
        """Показать агрегированную сводку и последние оплаты пользователя.

        Args:
            obj (User | None): Пользователь, открытый в админке.

        Returns:
            str: HTML-блок со сводкой по оплатам.
        """
        if obj is None:
            return "Сохраните пользователя, чтобы увидеть историю оплат."

        from apps.payments.models import PaymentRecord

        payments = PaymentRecord.objects.filter(user=obj).order_by("-paid_at")
        total_paid = payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        subscription_count = payments.filter(payment_type="subscription").count()
        tournament_count = payments.filter(payment_type="tournament").count()
        donation_count = payments.filter(payment_type="donation").count()

        if not payments.exists():
            return cast(
                str,
                mark_safe(
                    "<div>"
                    "<div><strong>Всего оплачено:</strong> 0 ₽</div>"
                    "<div><strong>История:</strong> пока пусто.</div>"
                    "</div>"
                ),
            )

        rows = format_html_join(
            "",
            "<li>{} — <strong>{} ₽</strong>, {}, {}, рекуррентный: {}</li>",
            (
                (
                    payment.paid_at.strftime("%d.%m.%Y %H:%M"),
                    payment.amount,
                    payment.get_payment_type_display(),
                    payment.item_label or "без названия",
                    "да" if payment.is_recurring else "нет",
                )
                for payment in payments[:10]
            ),
        )
        return cast(
            str,
            format_html(
                "<div>"
                "<div><strong>Всего оплачено:</strong> {} ₽</div>"
                "<div><strong>Подписок:</strong> {}, <strong>турниров:</strong> {}, <strong>донатов:</strong> {}</div>"
                "<div style='margin-top:12px'><strong>Последние оплаты:</strong><ul style='margin:8px 0 0 18px'>{}</ul></div>"
                "</div>",
                total_paid,
                subscription_count,
                tournament_count,
                donation_count,
                rows,
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
    white-space: normal;
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
    ordering = ("-created_at",)

    list_display = (
        "user_email",
        "user_last_name",
        "user_first_name",
        "is_verified",
        "city",
        "skill_level_with_ntrp",
        "gender",
        "ntrp_level",
        "total_points",
        "matches_played",
        "matches_won",
        "is_bye",
        "is_legend",
        "is_hidden_on_home",
    )

    @admin.display(description="Email", ordering="user__email")
    def user_email(self, obj: Player) -> str:
        """Вернуть email пользователя игрока.

        Args:
            obj: Экземпляр игрока.

        Returns:
            Email пользователя.
        """

        return cast(str, obj.user.email)

    @admin.display(description="Фамилия", ordering="user__last_name")
    def user_last_name(self, obj: Player) -> str:
        """Вернуть фамилию пользователя игрока.

        Args:
            obj: Экземпляр игрока.

        Returns:
            Фамилия пользователя.
        """

        return cast(str, obj.user.last_name)

    @admin.display(description="Имя", ordering="user__first_name")
    def user_first_name(self, obj: Player) -> str:
        """Вернуть имя пользователя игрока.

        Args:
            obj: Экземпляр игрока.

        Returns:
            Имя пользователя.
        """

        return cast(str, obj.user.first_name)

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
        "is_hidden_on_home",
        "is_bye",
    )
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "user__phone",
    )
    list_editable = ("is_verified", "is_legend", "is_hidden_on_home")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    def get_search_results(
        self,
        request: HttpRequest,
        queryset: QuerySet[Player],
        search_term: str,
    ) -> tuple[QuerySet[Player], bool]:
        """
        Выполнить поиск по email/ФИО/телефону игрока с учетом регистра кириллицы.

        Args:
            request: Текущий HTTP-запрос админки.
            queryset: Базовый queryset для фильтрации.
            search_term: Строка поиска из формы админки.

        Returns:
            Кортеж (queryset, may_have_duplicates).
        """

        if not search_term:
            return queryset, False

        tokens = [t for t in search_term.split() if t]
        if not tokens:
            return queryset, False

        combined_q = Q()
        for token in tokens:
            email_q = Q(user__email__icontains=token)

            name_q = Q()
            for variant in _case_variants_for_cyrillic(token):
                name_q |= Q(user__last_name__contains=variant)
                name_q |= Q(user__first_name__contains=variant)

            token_q = email_q | name_q

            phone_variants = _phone_variants_for_search(token)
            if phone_variants:
                phone_q = Q()
                for variant in phone_variants:
                    phone_q |= Q(user__phone__contains=variant)
                token_q |= phone_q

            combined_q &= token_q

        return queryset.filter(combined_q), False

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
        ("Статус", {"fields": ("is_verified", "is_legend", "is_hidden_on_home")}),
        ("Даты", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Admin for notifications."""

    list_display = ("user", "message", "is_read", "created_at")
    list_filter = ("is_read",)
    search_fields = ("message", "user__email")
