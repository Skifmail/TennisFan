"""
Tournaments admin configuration.
"""

from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from apps.users.models import Player, SkillLevel
from apps.users.skill_levels import skill_with_ntrp

from .fan import generate_bracket
from .models import (
    HeadToHead,
    Match,
    MatchResultProposal,
    SeasonArchive,
    SeasonPoints,
    SeasonRating,
    Tournament,
    TournamentAllowedCategory,
    TournamentEntryRefundRequest,
    TournamentPhoto,
    TournamentPlayerResult,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
    TournamentTeam,
    TVDGroup,
    TVDGroupMember,
    TVDTournament,
)
from .olympic_consolation import generate_bracket as generate_olympic_bracket
from .postpayment import (
    ADMIN_CONFIRMABLE_PAYMENT_STATUSES,
    ADMIN_RESENDABLE_PAYMENT_STATUSES,
    ParticipantPaymentStatus,
    PaymentStatusTone,
    admin_confirm_postpayment_participation,
    build_participant_payment_statuses,
    finalize_postpayment_window,
    format_postpayment_open_summary,
    get_pending_postpayment_users,
    get_postpayment_progress,
    mark_postpayment_call,
    open_postpayment_window,
    phone_to_tel_href,
    resend_postpayment_payment_link,
    tournament_has_generated_matches,
    tournament_needs_fancoin_settlement,
    try_settle_pending_users_with_fancoin,
)
from .proposal_service import apply_proposal
from .round_robin import generate_bracket as generate_round_robin_bracket
from .tvd import (
    _assign_tvd_places_5_onwards,
)
from .tvd import (
    check_and_finalize as tvd_check_and_finalize,
)
from .tvd import (
    generate_groups as tvd_generate_groups,
)
from .tvd import (
    generate_playoffs as tvd_generate_playoffs,
)
from .utils import generate_unique_tournament_slug

_PAYMENT_STATUS_TONE_STYLES: dict[PaymentStatusTone, str] = {
    "success": "color:#2da44e; font-weight:600;",
    "warning": "color:#bf8700; font-weight:600;",
    "danger": "color:#cf222e; font-weight:600;",
    "neutral": "color:#57606a;",
}


@admin.action(description="Подтвердить результат матча")
def accept_proposal_action(modeladmin, request, queryset):
    """Применить выбранные заявки к матчам (подтвердить от имени админа)."""
    count = 0
    for p in queryset.filter(status=Match.ProposalStatus.PENDING):
        match = p.match
        if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
            continue
        apply_proposal(p)
        count += 1
    if count:
        messages.success(request, f"Подтверждено заявок: {count}.")
    else:
        messages.warning(
            request, "Нет заявок для подтверждения (или матчи уже завершены)."
        )


def _report_postpayment_window_opened(request, tournament: Tournament) -> None:
    """Показать администратору итог открытия окна постоплаты.

    Args:
        request: HTTP-запрос админки.
        tournament (Tournament): Турнир.

    Returns:
        None: Сообщение добавляется во flash messages.
    """
    invoice_count, fancoin_settled = open_postpayment_window(tournament)
    summary = format_postpayment_open_summary(tournament, invoice_count)
    if fancoin_settled:
        messages.success(
            request,
            f"{tournament.name}: списано FT у {fancoin_settled} участников. {summary}",
        )
    if invoice_count:
        messages.info(
            request,
            f"{tournament.name}: запущено окно постоплаты. {summary}",
        )
    elif not fancoin_settled:
        messages.info(
            request,
            f"{tournament.name}: окно постоплаты — {summary}",
        )


@admin.action(description="Списать FT у участников с постоплатой")
def settle_postpayment_fancoin_action(modeladmin, request, queryset):
    """Проверить баланс FT и закрыть инвойсы без оплаты в рублях."""
    total = 0
    for tournament in queryset:
        if not tournament_needs_fancoin_settlement(tournament):
            messages.info(
                request,
                f"{tournament.name}: все участники уже покрыты (FT, ₽ или клубный слот).",
            )
            continue
        settled = try_settle_pending_users_with_fancoin(tournament)
        total += settled
        if settled:
            progress = get_postpayment_progress(tournament)
            messages.success(
                request,
                f"{tournament.name}: списано FT у {settled} участников. "
                f"Ожидают оплату в ₽: {progress['pending']}.",
            )
        else:
            pending_users = get_pending_postpayment_users(tournament)
            messages.warning(
                request,
                f"{tournament.name}: списание FT не выполнено для "
                f"{len(pending_users)} участников — проверьте активную подписку "
                f"и баланс (нужно минимум 3 FT).",
            )
    if total and queryset.count() > 1:
        messages.success(request, f"Всего списано FT у {total} участников.")


@admin.action(description="Сформировать сетку (одноэтапная)")
def generate_fan_bracket_action(modeladmin, request, queryset):
    for t in queryset:
        _admin_generate_bracket_after_postpayment(request, t, generate_bracket)


@admin.action(description="Сформировать сетку (олимпийская)")
def generate_olympic_bracket_action(modeladmin, request, queryset):
    for t in queryset:
        _admin_generate_bracket_after_postpayment(request, t, generate_olympic_bracket)


def _admin_generate_bracket_after_postpayment(
    request,
    tournament: Tournament,
    generate_fn,
) -> bool:
    """Сформировать сетку с учётом окна постоплаты.

    Args:
        request: HTTP-запрос админки.
        tournament (Tournament): Турнир.
        generate_fn: Функция генерации сетки ``(tournament) -> (bool, str)``.

    Returns:
        bool: ``True``, если действие для турнира обработано (continue в цикле).
    """
    pending_users = get_pending_postpayment_users(tournament)
    if (
        tournament.allow_postpayment
        and tournament.postpayment_window_started_at is None
        and pending_users
    ):
        _report_postpayment_window_opened(request, tournament)
        return True
    if tournament.postpayment_window_started_at is not None:
        progress = get_postpayment_progress(tournament)
        if bool(progress["completed"]):
            ok, msg = finalize_postpayment_window(tournament)
            if ok:
                messages.success(request, f"{tournament.name}: {msg}")
            else:
                messages.warning(request, f"{tournament.name}: {msg}")
            return True
        if pending_users:
            messages.info(
                request,
                f"{tournament.name}: окно постоплаты открыто, "
                f"ожидают оплату {len(pending_users)} участников.",
            )
            return True
    if tournament.bracket_generated and not tournament_has_generated_matches(
        tournament
    ):
        tournament.bracket_generated = False
        tournament.save(update_fields=["bracket_generated", "updated_at"])
    ok, msg = generate_fn(tournament)
    if ok:
        messages.success(request, f"{tournament.name}: {msg}")
    else:
        messages.warning(request, f"{tournament.name}: {msg}")
    return True


@admin.action(description="Сформировать сетку (круговой)")
def generate_round_robin_bracket_action(modeladmin, request, queryset):
    for t in queryset:
        _admin_generate_bracket_after_postpayment(
            request, t, generate_round_robin_bracket
        )


@admin.action(description="Сформировать группы (ТВД)")
def generate_tvd_groups_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = tvd_generate_groups(t)
        if ok:
            messages.success(request, f"{t.name}: {msg}")
        else:
            messages.warning(request, f"{t.name}: {msg}")


@admin.action(description="Сформировать плей-офф (ТВД)")
def generate_tvd_playoffs_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = tvd_generate_playoffs(t)
        if ok:
            messages.success(request, f"{t.name}: {msg}")
        else:
            messages.warning(request, f"{t.name}: {msg}")


@admin.action(description="Завершить турнир (ТВД)")
def finalize_tvd_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = tvd_check_and_finalize(t)
        if ok:
            messages.success(request, f"{t.name}: {msg}")
        else:
            messages.warning(request, f"{t.name}: {msg}")


@admin.action(description="Дозаполнить итоги ТВД (места 5+)")
def backfill_tvd_standings_action(modeladmin, request, queryset):
    """Для уже завершённых ТВД: присвоить места 5–8 и 9+ всем участникам без результата."""
    from .models import TournamentStatus

    for t in queryset.filter(status=TournamentStatus.COMPLETED):
        _assign_tvd_places_5_onwards(t)
        messages.success(request, f"{t.name}: итоги дозаполнены (места 5+).")


class TournamentTeamInline(admin.TabularInline):
    model = TournamentTeam
    extra = 0
    raw_id_fields = ("player1", "player2")
    verbose_name = "Команда"
    verbose_name_plural = "Команды"
    classes = ("variant-doubles-only",)  # для JS: скрывать при варианте «Одиночный»


class TournamentPhotoInline(admin.TabularInline):
    """Фото с турнира для галереи: количество не ограничено, с подписями."""

    model = TournamentPhoto
    extra = 1
    fields = ("image", "caption", "order")


class TournamentAdminForm(forms.ModelForm):
    """Форма турнира с полем «Допустимые категории» в виде чекбоксов (1–5)."""

    allowed_categories = forms.MultipleChoiceField(
        choices=SkillLevel.choices,
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Допустимые категории участников",
        help_text="Отметьте от 1 до 5 категорий. Регистрироваться смогут только игроки с выбранными уровнями.",
    )

    class Meta:
        model = Tournament
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["allowed_categories"].initial = list(
                self.instance.allowed_categories.values_list("category", flat=True)
            )
        self.fields["slug"].help_text = (
            "Можно оставить пустым или ввести вручную. При совпадении с существующим "
            "турниром автоматически добавится суффикс -2, -3 и т.д."
        )

    def clean_allowed_categories(self):
        value = self.cleaned_data.get("allowed_categories") or []
        if len(value) == 0:
            raise ValidationError("Выберите хотя бы одну категорию участников.")
        if len(value) > 5:
            raise ValidationError("Можно выбрать не более 5 категорий.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        variant = cleaned_data.get("variant")
        gender = cleaned_data.get("gender")
        tournament_format = cleaned_data.get("format")
        # "Микст" доступен только для парных турниров
        if variant == "singles" and gender == "mixed":
            raise ValidationError(
                "Категория «Микст» доступна только для парных турниров. "
                "Для одиночных используйте «Любой» (любой пол)."
            )
        # Многодневный турнир: обязателен вступительный взнос (для регистрации по подписке или по оплате)
        is_one_day = cleaned_data.get("is_one_day")
        entry_fee = cleaned_data.get("entry_fee")
        allow_postpayment = bool(cleaned_data.get("allow_postpayment"))
        if is_one_day is False and (entry_fee is None or float(entry_fee or 0) <= 0):
            raise ValidationError(
                "Для многодневного турнира укажите сумму вступительного взноса (руб). "
                "По подписке игроки регистрируются бесплатно в рамках лимита; без подписки или при исчерпанном лимите — оплата взноса."
            )
        if allow_postpayment:
            if is_one_day:
                raise ValidationError("Постоплата недоступна для однодневных турниров.")
            if tournament_format == "weekend_day":
                raise ValidationError("Постоплата недоступна для формата ТВД.")
            if entry_fee is None or float(entry_fee or 0) <= 0:
                raise ValidationError(
                    "Постоплата доступна только при вступительном взносе больше 0 ₽."
                )
        # Автогенерация уникального slug: при одинаковых названиях добавляется суффикс -2, -3 и т.д.
        name = cleaned_data.get("name") or ""
        slug = cleaned_data.get("slug") or ""
        cleaned_data["slug"] = generate_unique_tournament_slug(
            name=name,
            slug=slug or None,
            instance=self.instance,
        )
        return cleaned_data


@admin.register(TournamentEntryRefundRequest)
class TournamentEntryRefundRequestAdmin(admin.ModelAdmin):
    """Заявки на возврат взноса (участник удалён с турнира, взнос был оплачен)."""

    list_display = ("refund_ref", "tournament", "user", "amount", "removed_at")
    list_filter = ("tournament", "removed_at")
    search_fields = ("refund_ref", "user__email", "user__last_name", "user__first_name")
    readonly_fields = ("tournament", "user", "removed_at", "amount", "refund_ref")
    date_hierarchy = "removed_at"


@admin.register(TournamentPostpaymentInvoice)
class TournamentPostpaymentInvoiceAdmin(admin.ModelAdmin):
    """Инвойсы постоплаты турниров."""

    list_display = ("tournament", "user", "amount", "status", "due_at", "paid_at")
    list_filter = ("status", "tournament")
    search_fields = ("tournament__name", "user__email")
    readonly_fields = (
        "tournament",
        "user",
        "amount",
        "status",
        "due_at",
        "created_at",
        "paid_at",
        "reminder_1h_sent_at",
        "payment_link_resent_at",
        "yookassa_payment_id",
    )


@admin.register(TournamentRegistrationCoverage)
class TournamentRegistrationCoverageAdmin(admin.ModelAdmin):
    """Источники покрытия регистрации на турниры."""

    list_display = ("tournament", "user", "coverage_type", "created_at")
    list_filter = ("coverage_type", "tournament")
    search_fields = ("tournament__name", "user__email")
    readonly_fields = ("tournament", "user", "coverage_type", "created_at")


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    form = TournamentAdminForm
    inlines = [TournamentTeamInline, TournamentPhotoInline]

    """Admin for Tournament model.

    Страница добавления турнира: базовая информация + выбор формата.
    Поля формата появляются динамически при выборе (одноэтапная сетка, олимпийская, круговой).
    """

    list_display = (
        "name",
        "city",
        "allowed_skill_levels",
        "format",
        "variant",
        "gender",
        "status",
        "bracket_generated",
        "allow_postpayment",
        "start_date",
        "court",
        "min_participants",
        "max_participants",
        "min_teams",
        "max_teams",
    )
    list_filter = (
        "city",
        "gender",
        "tournament_type",
        "format",
        "variant",
        "status",
        "bracket_generated",
    )
    search_fields = ("name", "description")
    list_editable = ("status",)
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("participants",)
    date_hierarchy = "start_date"
    readonly_fields = (
        "insufficient_participants_notified_at",
        "postpayment_window_started_at",
        "postpayment_window_schedule_display",
        "participant_payment_status_display",
    )
    actions = [
        settle_postpayment_fancoin_action,
        generate_fan_bracket_action,
        generate_olympic_bracket_action,
        generate_round_robin_bracket_action,
    ]

    def allowed_skill_levels(self, obj) -> str:
        """Отображение допущенных уровней участников с их числовыми диапазонами."""
        # Здесь важно показать и название уровня, и его числовой диапазон (NTRP),
        # чтобы администратору было сразу понятно, кого пускает турнир.
        codes = list(
            obj.allowed_categories.values_list("category", flat=True).order_by(
                "category"
            )
        )
        if not codes:
            return "—"
        parts = [skill_with_ntrp(code) for code in codes]
        return ", ".join(parts)

    allowed_skill_levels.short_description = "Уровень участников"

    def get_urls(self):
        """Добавить URL отметки звонка по постоплате.

        Returns:
            list: URL-паттерны админки турнира.
        """
        custom_urls = [
            path(
                "<path:object_id>/postpayment-call/<int:user_id>/",
                self.admin_site.admin_view(self.mark_postpayment_call_view),
                name="tournaments_tournament_postpayment_call",
            ),
            path(
                "<path:object_id>/postpayment-confirm/<int:user_id>/",
                self.admin_site.admin_view(self.confirm_postpayment_participation_view),
                name="tournaments_tournament_postpayment_confirm",
            ),
            path(
                "<path:object_id>/postpayment-resend-link/<int:user_id>/",
                self.admin_site.admin_view(self.resend_postpayment_link_view),
                name="tournaments_tournament_postpayment_resend_link",
            ),
            path(
                "<path:object_id>/postpayment-settle-fancoin/",
                self.admin_site.admin_view(self.settle_postpayment_fancoin_view),
                name="tournaments_tournament_postpayment_settle_fancoin",
            ),
        ]
        return custom_urls + super().get_urls()

    def mark_postpayment_call_view(
        self,
        request: HttpRequest,
        object_id: str,
        user_id: int,
    ) -> HttpResponse:
        """Отметить звонок участнику и открыть ``tel:``-ссылку.

        Args:
            request (HttpRequest): Запрос администратора.
            object_id (str): ID турнира.
            user_id (int): ID пользователя участника.

        Returns:
            HttpResponse: Редирект на ``tel:`` или обратно в карточку турнира.
        """
        tournament = get_object_or_404(Tournament, pk=object_id)
        user = get_object_or_404(get_user_model(), pk=user_id)
        mark_postpayment_call(tournament, user, called_by=request.user)
        change_url = reverse(
            "admin:tournaments_tournament_change",
            args=[tournament.pk],
        )
        tel_href = phone_to_tel_href(getattr(user, "phone", "") or "")
        if not tel_href:
            self.message_user(
                request,
                (
                    "Звонок отмечен, но у участника "
                    f"«{user.get_full_name() or user.email}» нет валидного телефона."
                ),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(change_url)
        # Открываем dialer и возвращаем в карточку турнира с обновлённой отметкой.
        return HttpResponse(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta http-equiv='refresh' content='0;url={change_url}'>"
            f"<script>window.location.href={tel_href!r};</script>"
            "</head><body>"
            f"<p>Открываем звонок… <a href={change_url!r}>Вернуться</a></p>"
            "</body></html>",
            content_type="text/html; charset=utf-8",
        )

    def confirm_postpayment_participation_view(
        self,
        request: HttpRequest,
        object_id: str,
        user_id: int,
    ) -> HttpResponse:
        """Подтвердить участие вручную: покрытие админом и отмена инвойса.

        Args:
            request (HttpRequest): Запрос администратора.
            object_id (str): ID турнира.
            user_id (int): ID пользователя участника.

        Returns:
            HttpResponse: Редирект обратно в карточку турнира.
        """
        tournament = get_object_or_404(Tournament, pk=object_id)
        user = get_object_or_404(get_user_model(), pk=user_id)
        display_name = user.get_full_name() or user.email or f"ID {user.pk}"
        admin_confirm_postpayment_participation(tournament, user)
        self.message_user(
            request,
            (
                f"Участие «{display_name}» подтверждено администратором. "
                "Инвойс постоплаты отменён (если был)."
            ),
            level=messages.SUCCESS,
        )
        tournament.refresh_from_db()
        progress = get_postpayment_progress(tournament)
        if tournament.postpayment_window_started_at is not None and bool(
            progress["completed"]
        ):
            ok, msg = finalize_postpayment_window(tournament)
            if ok:
                self.message_user(
                    request,
                    f"Все оплаты закрыты — сетка сформирована. {msg}",
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    f"Все оплаты закрыты, но сетку не удалось сформировать: {msg}",
                    level=messages.WARNING,
                )
        return HttpResponseRedirect(
            reverse("admin:tournaments_tournament_change", args=[tournament.pk])
        )

    def resend_postpayment_link_view(
        self,
        request: HttpRequest,
        object_id: str,
        user_id: int,
    ) -> HttpResponse:
        """Повторно отправить участнику ссылку на оплату постоплаты.

        Args:
            request (HttpRequest): Запрос администратора.
            object_id (str): ID турнира.
            user_id (int): ID пользователя участника.

        Returns:
            HttpResponse: Редирект обратно в карточку турнира.
        """
        tournament = get_object_or_404(Tournament, pk=object_id)
        user = get_object_or_404(get_user_model(), pk=user_id)
        display_name = user.get_full_name() or user.email or f"ID {user.pk}"
        ok, msg = resend_postpayment_payment_link(tournament, user)
        self.message_user(
            request,
            f"«{display_name}»: {msg}",
            level=messages.SUCCESS if ok else messages.WARNING,
        )
        return HttpResponseRedirect(
            reverse("admin:tournaments_tournament_change", args=[tournament.pk])
        )

    def settle_postpayment_fancoin_view(
        self,
        request: HttpRequest,
        object_id: str,
    ) -> HttpResponse:
        """Списать FT у участников с постоплатой для текущего турнира.

        Args:
            request (HttpRequest): Запрос администратора.
            object_id (str): ID турнира.

        Returns:
            HttpResponse: Редирект обратно в карточку турнира.
        """
        tournament = get_object_or_404(Tournament, pk=object_id)
        change_url = reverse(
            "admin:tournaments_tournament_change",
            args=[tournament.pk],
        )
        if not tournament_needs_fancoin_settlement(tournament):
            self.message_user(
                request,
                "Все участники уже покрыты (FT, ₽ или клубный слот).",
                level=messages.INFO,
            )
            return HttpResponseRedirect(change_url)

        settled = try_settle_pending_users_with_fancoin(tournament)
        progress = get_postpayment_progress(tournament)
        if settled:
            self.message_user(
                request,
                (
                    f"Списано FT у {settled} участников. "
                    f"Ожидают оплату в ₽: {progress['pending']}."
                ),
                level=messages.SUCCESS,
            )
        else:
            pending_users = get_pending_postpayment_users(tournament)
            self.message_user(
                request,
                (
                    f"Списание FT не выполнено для {len(pending_users)} участников — "
                    "проверьте активную подписку и баланс (нужно минимум 3 FT)."
                ),
                level=messages.WARNING,
            )

        tournament.refresh_from_db()
        progress = get_postpayment_progress(tournament)
        if (
            settled
            and tournament.postpayment_window_started_at is not None
            and bool(progress["completed"])
        ):
            ok, msg = finalize_postpayment_window(tournament)
            if ok:
                self.message_user(
                    request,
                    f"Все оплаты закрыты — сетка сформирована. {msg}",
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    f"Все оплаты закрыты, но сетку не удалось сформировать: {msg}",
                    level=messages.WARNING,
                )
        return HttpResponseRedirect(change_url)

    def participant_payment_status_display(self, obj: Tournament) -> str:
        """Карточки статусов оплаты участников (удобно на мобильных).

        Args:
            obj (Tournament): Редактируемый турнир.

        Returns:
            str: HTML для readonly-поля.
        """
        if not obj.pk:
            return "—"
        rows = build_participant_payment_statuses(obj)
        if not rows:
            return "Нет зарегистрированных участников."
        progress = get_postpayment_progress(obj)
        summary_parts = [
            f"Всего участников: {len(rows)}",
        ]
        if obj.postpayment_window_started_at:
            summary_parts.append(
                f"инвойсов: {progress['total']}, "
                f"оплачено ₽: {progress['paid']}, "
                f"ожидают ₽: {progress['pending']}"
            )
            started = timezone.localtime(obj.postpayment_window_started_at).strftime(
                "%d.%m.%Y %H:%M"
            )
            hours = obj.get_postpayment_deadline_hours()
            ends_at = obj.get_postpayment_window_ends_at()
            ends_text = (
                timezone.localtime(ends_at).strftime("%d.%m.%Y %H:%M")
                if ends_at
                else "—"
            )
            summary_parts.append(f"окно: {started} → {ends_text} ({hours} ч)")

        settle_url = reverse(
            "admin:tournaments_tournament_postpayment_settle_fancoin",
            args=[obj.pk],
        )
        btn_base = (
            "display:inline-flex; align-items:center; justify-content:center; "
            "box-sizing:border-box; margin:0 0 10px; padding:8px 12px; "
            "border-radius:6px; text-decoration:none; font-size:13px; "
            "font-weight:600; text-align:center; max-width:100%;"
        )
        needs_settle = tournament_needs_fancoin_settlement(obj)
        if needs_settle:
            settle_btn = format_html(
                '<a href="{}" onclick="return confirm('
                "'Списать FT у участников с достаточным балансом? "
                "Инвойсы таких участников будут отменены.'"
                ');" style="{} border:1px solid #8250df; '
                'background:#fbefff; color:#8250df;">'
                "Списать FT у участников с постоплатой</a>",
                settle_url,
                btn_base,
            )
        else:
            settle_btn = format_html(
                '<span style="{} border:1px solid #ccc; '
                'background:#f6f8fa; color:#8c959f;" '
                'title="Нет участников для списания FT">'
                "Списать FT — не требуется</span>",
                btn_base,
            )

        def _card(row: ParticipantPaymentStatus) -> str:
            tone_style = _PAYMENT_STATUS_TONE_STYLES.get(
                row.status_tone, _PAYMENT_STATUS_TONE_STYLES["neutral"]
            )
            call_url = reverse(
                "admin:tournaments_tournament_postpayment_call",
                args=[obj.pk, row.user_id],
            )
            if phone_to_tel_href(row.phone):
                call_btn = format_html(
                    '<a href="{}" style="'
                    "display:inline-flex; padding:6px 10px; "
                    "border:1px solid #0b5cad; border-radius:4px; "
                    "background:#e8f1fb; color:#0b5cad; text-decoration:none; "
                    'font-size:12px; font-weight:600;">Позвонить</a>',
                    call_url,
                )
            else:
                call_btn = mark_safe(
                    '<span style="'
                    "display:inline-flex; padding:6px 10px; "
                    "border:1px solid #ccc; border-radius:4px; "
                    "background:#f6f8fa; color:#8c959f; font-size:12px;"
                    '" title="Телефон не указан">Нет телефона</span>'
                )
            if row.called_at:
                called_label = format_html(
                    '<span style="color:#2da44e; font-size:12px;">звонил: {}</span>',
                    timezone.localtime(row.called_at).strftime("%d.%m.%Y %H:%M"),
                )
            else:
                called_label = mark_safe(
                    '<span style="color:#8c959f; font-size:12px;">не звонил</span>'
                )
            status_html = format_html(
                '<span style="{}">{}</span>',
                tone_style,
                row.status,
            )
            if row.status in ADMIN_CONFIRMABLE_PAYMENT_STATUSES:
                confirm_url = reverse(
                    "admin:tournaments_tournament_postpayment_confirm",
                    args=[obj.pk, row.user_id],
                )
                confirm_btn = format_html(
                    '<a href="{}" onclick="return confirm('
                    "'Подтвердить участие без оплаты? "
                    "Статус станет зелёным, инвойс будет отменён.'"
                    ');" style="'
                    "display:inline-flex; align-items:center; justify-content:center; "
                    "box-sizing:border-box; padding:6px 10px; "
                    "border:1px solid #1a7f37; border-radius:4px; "
                    "background:#dafbe1; color:#1a7f37; text-decoration:none; "
                    "font-size:12px; font-weight:600; "
                    'white-space:nowrap;">'
                    "Подтвердить участие</a>",
                    confirm_url,
                )
            else:
                confirm_btn = mark_safe("")
            if row.status in ADMIN_RESENDABLE_PAYMENT_STATUSES:
                resend_url = reverse(
                    "admin:tournaments_tournament_postpayment_resend_link",
                    args=[obj.pk, row.user_id],
                )
                resend_btn = format_html(
                    '<a href="{}" onclick="return confirm('
                    "'Отправить участнику ссылку на оплату ещё раз?'"
                    ');" style="'
                    "display:inline-flex; align-items:center; justify-content:center; "
                    "box-sizing:border-box; padding:6px 10px; "
                    "border:1px solid #9a6700; border-radius:4px; "
                    "background:#fff8c5; color:#9a6700; text-decoration:none; "
                    "font-size:12px; font-weight:600; "
                    'white-space:nowrap;">'
                    "Отправить ссылку ещё раз</a>",
                    resend_url,
                )
            else:
                resend_btn = mark_safe("")
            if row.link_resent_at:
                resend_label = format_html(
                    '<span style="color:#9a6700; font-size:12px;">'
                    "ссылка ещё раз: {}</span>",
                    timezone.localtime(row.link_resent_at).strftime("%d.%m.%Y %H:%M"),
                )
            else:
                resend_label = mark_safe("")
            action_btns = format_html("{}{}{}", confirm_btn, resend_btn, resend_label)
            # На широком экране — 3 колонки в ряд; на узком flex переносит вниз.
            return cast(
                str,
                format_html(
                    '<div style="'
                    "box-sizing:border-box; width:100%; max-width:100%; "
                    "margin:0 0 8px; padding:10px 12px; "
                    "border:1px solid #d0d7de; border-radius:8px; "
                    "background:var(--body-bg, #fff); "
                    "display:flex; flex-wrap:wrap; gap:10px 20px; "
                    "align-items:flex-start; "
                    'overflow-wrap:anywhere; word-break:break-word;">'
                    '<div style="flex:1 1 170px; min-width:150px;">'
                    '<div style="font-weight:600; font-size:14px; '
                    'margin-bottom:6px;">{}</div>'
                    '<div style="display:flex; flex-wrap:wrap; gap:8px; '
                    'align-items:center;">{}{}</div>'
                    "</div>"
                    '<div style="flex:1 1 220px; min-width:160px;">'
                    '<div style="font-size:11px; color:#656d76; '
                    'margin-bottom:4px;">Статус</div>'
                    '<div style="display:flex; flex-direction:column; '
                    'gap:8px; align-items:flex-start;">'
                    "{}"
                    '<div style="display:flex; flex-wrap:wrap; gap:8px; '
                    'align-items:center; width:100%;">{}</div>'
                    "</div>"
                    "</div>"
                    '<div style="flex:2 1 240px; min-width:180px;">'
                    '<div style="font-size:11px; color:#656d76; '
                    'margin-bottom:4px;">Детали</div>'
                    '<div style="font-size:12px; color:#656d76; '
                    'line-height:1.45;">{}</div>'
                    "</div>"
                    "</div>",
                    row.display_name,
                    call_btn,
                    called_label,
                    status_html,
                    action_btns,
                    row.details,
                ),
            )

        cards = format_html_join(
            "",
            "{}",
            ((_card(row),) for row in rows),
        )
        legend = format_html(
            "<p style='margin:8px 0 0; font-size:12px; line-height:1.5'>"
            '<span style="{}">■</span> оплачено &nbsp; '
            '<span style="{}">■</span> ожидает оплату &nbsp; '
            '<span style="{}">■</span> ждёт окно постоплаты'
            "</p>",
            _PAYMENT_STATUS_TONE_STYLES["success"],
            _PAYMENT_STATUS_TONE_STYLES["danger"],
            _PAYMENT_STATUS_TONE_STYLES["warning"],
        )
        summary = format_html(
            '<div style="margin-bottom:8px; font-size:13px; '
            'line-height:1.5; overflow-wrap:anywhere;">{}</div>{}',
            format_html_join(
                " · ",
                "{}",
                ((part,) for part in summary_parts),
            ),
            settle_btn,
        )
        return cast(
            str,
            format_html(
                '<div style="box-sizing:border-box; width:100%; max-width:100%; '
                'overflow-x:hidden;">'
                "{}<div style='margin-top:4px'>{}</div>{}"
                "</div>",
                summary,
                cards,
                legend,
            ),
        )

    participant_payment_status_display.short_description = (
        "Оплата и постоплата участников"
    )

    def get_queryset(self, request):
        """
        Исключить турниры клубов из списка платформенных.

        Args:
            request: HTTP-запрос админки.

        Returns:
            QuerySet с ``club`` равным NULL.
        """
        qs = super().get_queryset(request)
        return qs.filter(club__isnull=True)

    def changelist_view(self, request, extra_context=None):
        """Показать в админке статус активных окон постоплаты."""
        pending_qs = TournamentPostpaymentInvoice.objects.filter(
            tournament__club__isnull=True,
            tournament__bracket_generated=False,
            status=TournamentPostpaymentInvoice.Status.PENDING,
        )
        pending_total = pending_qs.count()
        if pending_total:
            tournaments = list(
                Tournament.objects.filter(
                    pk__in=pending_qs.values_list("tournament_id", flat=True).distinct()
                )
                .only("id", "name")
                .order_by("name")
            )
            links = format_html_join(
                ", ",
                '<a href="{}">{}</a>',
                (
                    (
                        reverse(
                            "admin:tournaments_tournament_change",
                            args=[tournament.pk],
                        ),
                        tournament.name,
                    )
                    for tournament in tournaments
                ),
            )
            self.message_user(
                request,
                format_html(
                    "Активна постоплата: ожидается оплата от {} участников "
                    "в {} турнирах: {}.",
                    pending_total,
                    len(tournaments),
                    links,
                ),
                level=messages.WARNING,
            )
        return super().changelist_view(request, extra_context=extra_context)

    def postpayment_window_schedule_display(self, obj: Tournament) -> str:
        """Показать расписание окна постоплаты (старт, длительность, конец).

        Args:
            obj (Tournament): Турнир.

        Returns:
            str: Текст для readonly-поля.
        """
        if not obj.pk:
            return "—"
        hours = obj.get_postpayment_deadline_hours()
        if not obj.allow_postpayment:
            return "Постоплата выключена."
        if obj.postpayment_window_started_at is None:
            return (
                f"Окно ещё не открыто. После дедлайна регистрации участникам "
                f"будет дано {hours} ч на оплату."
            )
        started = timezone.localtime(obj.postpayment_window_started_at)
        ends_at = obj.get_postpayment_window_ends_at()
        ends_text = (
            timezone.localtime(ends_at).strftime("%d.%m.%Y %H:%M") if ends_at else "—"
        )
        return (
            f"Старт: {started.strftime('%d.%m.%Y %H:%M')}; "
            f"длительность: {hours} ч; "
            f"оплатить до: {ends_text}."
        )

    postpayment_window_schedule_display.short_description = "Расписание окна постоплаты"

    fieldsets = (
        ("Базовая информация", {"fields": ("name", "slug", "description", "image")}),
        ("Формат турнира", {"fields": ("format", "variant")}),
        (
            "Постоплата: статус оплаты участников",
            {
                "fields": ("participant_payment_status_display",),
                "description": (
                    "Кто оплатил FT или рублями, кому отправлены уведомления, "
                    "кто ещё должен оплатить. «Списать FT…» — проверить баланс "
                    "и закрыть инвойсы. «Позвонить» / «Подтвердить участие» / "
                    "«Отправить ссылку ещё раз» — по участнику. "
                    "Обновите страницу после действий."
                ),
            },
        ),
        (
            "Постоплата: настройки окна",
            {
                "fields": (
                    "allow_postpayment",
                    "postpayment_deadline_hours",
                    "postpayment_window_started_at",
                    "postpayment_window_schedule_display",
                ),
                "description": (
                    "Сначала включите постоплату и задайте длительность окна в часах. "
                    "Старт заполняется автоматически при открытии окна; ниже видно, "
                    "до какого времени нужно оплатить."
                ),
            },
        ),
        (
            "Общие поля (одноэтапная сетка, Олимпийская, Круговой)",
            {
                "fields": (
                    "entry_fee",
                    "is_one_day",
                    "city",
                    "court",
                    "gender",
                    "allowed_categories",
                    "tournament_type",
                    "status",
                    "start_date",
                    "end_date",
                    "registration_deadline",
                    "min_participants",
                    "max_participants",
                    "min_teams",
                    "max_teams",
                    "insufficient_participants_notified_at",
                    "bracket_generated",
                    "match_days_per_round",
                    "participants",
                ),
                "description": "Блок отображается после выбора формата турнира (Одноэтапная сетка, Олимпийская система или Круговой).",
                "classes": ("format-common-section",),
            },
        ),
        (
            "Одноэтапная сетка / Олимпийская / Круговой: очки за раунды и места",
            {
                "fields": (
                    "fan_points_r1",
                    "fan_points_r2",
                    "fan_points_sf",
                    "fan_points_final",
                    "fan_points_winner",
                ),
                "description": (
                    "<b>Одноэтапная сетка:</b> очки начисляются при вылете из раунда. "
                    "<b>Олимпийская система:</b> очки по итоговому месту (1–2–3–4–5–8–9+). "
                    "<b>Круговой:</b> очки в общий рейтинг начисляются по итоговому месту после завершения турнира "
                    "(1 место = победитель, 2 = финалист, 3–4 = полуфинал, 5–8 = 2 круг, 9+ = 1 круг). "
                    "Внутри кругового турнира места определяются по победам (1 очко за победу, 0 за поражение), эти значения не редактируются.\n\n"
                    "<b>Значения по умолчанию:</b> 10–25–45–70–100."
                ),
                "classes": (
                    "format-fan-section",
                    "format-olympic-section",
                    "format-round-robin-section",
                ),
            },
        ),
        (
            "Круговой: формат матча",
            {
                "fields": ("match_format",),
                "classes": ("format-round-robin-section",),
                "description": (
                    "Формат матча влияет на тай-брейки и подсчёт очков в таблице. "
                    "Очки 1 за победу и 0 за поражение используются только для определения мест внутри турнира и не настраиваются."
                ),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        selected = form.cleaned_data.get("allowed_categories") or []
        obj.allowed_categories.all().delete()
        for category in selected:
            TournamentAllowedCategory.objects.create(tournament=obj, category=category)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """Добавить пустой выбор для формата на странице добавления."""
        if db_field.name == "format":
            resolver_match = getattr(request, "resolver_match", None)
            is_add_page = "/add/" in (request.path or "") or (
                resolver_match
                and "add" in (getattr(resolver_match, "url_name", "") or "")
            )
            if is_add_page:
                kwargs["choices"] = [("", "---------")] + list(db_field.choices)
                kwargs["initial"] = ""
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    class Media:
        css = {"all": ("css/admin_tournament.css",)}
        js = ("js/admin_tournament.js", "js/city_autocomplete.js")


class TVDGroupMemberInline(admin.TabularInline):
    model = TVDGroupMember
    extra = 0
    raw_id_fields = ("player",)
    ordering = ("final_place", "seed")


class TVDGroupInline(admin.TabularInline):
    model = TVDGroup
    extra = 0
    ordering = ("order",)
    show_change_link = True


class TVDTournamentAdminForm(TournamentAdminForm):
    """Форма ТВД: format и is_one_day выставляются автоматически, добавлено поле «Бесплатный»."""

    is_free = forms.BooleanField(
        required=False,
        initial=False,
        label="Бесплатный",
        help_text="Если отмечено — взнос 0 ₽, регистрироваться могут все желающие (с учётом категорий). Иначе обязательно укажите сумму взноса.",
    )

    class Meta(TournamentAdminForm.Meta):
        exclude = ("format", "is_one_day")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and (self.instance.entry_fee or 0) == 0:
            self.fields["is_free"].initial = True

    def clean(self):
        cleaned_data = super().clean()
        is_free = cleaned_data.get("is_free")
        entry_fee = cleaned_data.get("entry_fee")
        if is_free:
            cleaned_data["entry_fee"] = 0
        elif entry_fee is None or (entry_fee is not None and float(entry_fee) <= 0):
            raise ValidationError(
                "Укажите сумму взноса (руб) или отметьте «Бесплатный»."
            )
        return cleaned_data


@admin.register(TVDTournament)
class TVDTournamentAdmin(admin.ModelAdmin):
    """Админка для однодневных турниров (proxy над Tournament, format=weekend_day)."""

    form = TVDTournamentAdminForm
    inlines = [TVDGroupInline]

    list_display = (
        "name",
        "city",
        "variant",
        "status",
        "bracket_generated",
        "start_date",
        "max_participants",
    )
    list_filter = ("city", "status", "bracket_generated")
    search_fields = ("name", "description")
    list_editable = ("status",)
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("participants",)
    date_hierarchy = "start_date"
    readonly_fields = ("insufficient_participants_notified_at",)
    actions = [
        generate_tvd_groups_action,
        generate_tvd_playoffs_action,
        finalize_tvd_action,
        backfill_tvd_standings_action,
    ]

    fieldsets = (
        ("Базовая информация", {"fields": ("name", "slug", "description", "image")}),
        (
            "ТВД (формат и участники)",
            {
                "fields": (
                    "variant",
                    "is_free",
                    "entry_fee",
                    "city",
                    "court",
                    "gender",
                    "allowed_categories",
                    "tournament_type",
                    "status",
                    "start_date",
                    "end_date",
                    "registration_deadline",
                    "min_participants",
                    "max_participants",
                    "insufficient_participants_notified_at",
                    "bracket_generated",
                    "match_days_per_round",
                    "participants",
                ),
            },
        ),
        (
            "Очки за места",
            {
                "fields": (
                    "fan_points_r1",
                    "fan_points_r2",
                    "fan_points_sf",
                    "fan_points_final",
                    "fan_points_winner",
                ),
            },
        ),
        ("Формат матча в группах", {"fields": ("match_format",)}),
    )

    def get_queryset(self, request):
        """
        Только однодневные (ТВД) турниры платформы без клуба-организатора.

        Args:
            request: HTTP-запрос админки.

        Returns:
            QuerySet с ``format=weekend_day`` и ``club`` равным NULL.
        """
        return (
            super()
            .get_queryset(request)
            .filter(format="weekend_day", club__isnull=True)
        )

    def save_model(self, request, obj, form, change):
        from .models import TournamentDuration, TournamentFormat

        obj.format = TournamentFormat.WEEKEND_DAY
        obj.is_one_day = True  # ТВД всегда однодневный
        obj.duration = TournamentDuration.SINGLE_DAY
        if form.cleaned_data.get("is_free"):
            obj.entry_fee = 0
        super().save_model(request, obj, form, change)
        selected = form.cleaned_data.get("allowed_categories") or []
        obj.allowed_categories.all().delete()
        for category in selected:
            TournamentAllowedCategory.objects.create(tournament=obj, category=category)


WINNER_SIDE_PLAYER1 = "player1"
WINNER_SIDE_PLAYER2 = "player2"
WINNER_SIDE_TEAM1 = "team1"
WINNER_SIDE_TEAM2 = "team2"


def _count_sets_won(cleaned: dict[str, Any]) -> tuple[int, int]:
    """Подсчитать выигранные сеты у стороны 1 и 2.

    Args:
        cleaned: Очищенные данные формы матча.

    Returns:
        Кортеж (сеты_п1, сеты_п2).
    """
    sets_p1 = 0
    sets_p2 = 0
    for set_index in range(1, 4):
        games_p1 = cleaned.get(f"player1_set{set_index}")
        games_p2 = cleaned.get(f"player2_set{set_index}")
        if games_p1 is None and games_p2 is None:
            continue
        if games_p1 is None or games_p2 is None:
            raise forms.ValidationError(
                f"В {set_index}-м сете укажите геймы обеих сторон или оставьте сет пустым.",
            )
        if games_p1 > games_p2:
            sets_p1 += 1
        elif games_p2 > games_p1:
            sets_p2 += 1
    return sets_p1, sets_p2


def _score_entered(cleaned: dict[str, Any]) -> bool:
    """Проверить, введён ли хотя бы один сет.

    Args:
        cleaned: Очищенные данные формы матча.

    Returns:
        True, если есть хотя бы одно значение счёта.
    """
    return any(
        cleaned.get(f"player{side}_set{set_index}") is not None
        for side in (1, 2)
        for set_index in range(1, 4)
    )


class MatchAdminForm(forms.ModelForm):
    """Упрощённая форма ввода результата матча в админке."""

    winner_side = forms.ChoiceField(
        label="Победитель",
        required=False,
        widget=forms.RadioSelect(attrs={"class": "match-admin-winner-options"}),
        help_text="Выберите победителя из участников этого матча.",
    )

    class Meta:
        model = Match
        fields = [
            "tournament",
            "round_name",
            "deadline",
            "player1",
            "player2",
            "team1",
            "team2",
            "player1_set1",
            "player2_set1",
            "player1_set2",
            "player2_set2",
            "player1_set3",
            "player2_set3",
            "status",
            "completed_datetime",
            "court",
            "round_index",
            "round_order",
            "is_consolation",
            "tvd_group",
            "tvd_stage",
            "next_match",
            "loser_next_match",
            "placement_min",
            "placement_max",
            "scheduled_datetime",
            "points_player1",
            "points_player2",
            "match_type",
            "rating_status",
            "rating_delta_player1",
            "rating_delta_player2",
        ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._preserved_participant_ids = self._load_preserved_participant_ids()
        self.is_doubles_match = bool(
            self.instance.pk and self.instance.team1_id and self.instance.team2_id
        )
        self._configure_winner_side_field()
        self._configure_score_labels()
        self._hide_technical_fields()

    def _load_preserved_participant_ids(self) -> dict[str, int | None]:
        """Запомнить участников матча до clean() — readonly поля не приходят в POST.

        Returns:
            Словарь с ID player1, player2, team1, team2.
        """
        if not self.instance.pk:
            return {}
        return {
            "player1_id": self.instance.player1_id,
            "player2_id": self.instance.player2_id,
            "team1_id": self.instance.team1_id,
            "team2_id": self.instance.team2_id,
        }

    def _resolve_player(self, cleaned: dict[str, Any], side: int) -> Player | None:
        """Вернуть игрока стороны матча из POST или из сохранённого снимка.

        Args:
            cleaned: Очищенные данные формы.
            side: Номер стороны (1 или 2).

        Returns:
            Игрок или None.
        """
        field_name = f"player{side}"
        player = cleaned.get(field_name)
        if player is not None:
            return cast(Player, player)
        player_id = self._preserved_participant_ids.get(f"{field_name}_id")
        if not player_id:
            return None
        return cast(
            Player | None,
            Player.objects.filter(pk=player_id).first(),
        )

    def _resolve_team(
        self, cleaned: dict[str, Any], side: int
    ) -> TournamentTeam | None:
        """Вернуть команду стороны матча из POST или из сохранённого снимка.

        Args:
            cleaned: Очищенные данные формы.
            side: Номер стороны (1 или 2).

        Returns:
            Команда или None.
        """
        field_name = f"team{side}"
        team = cleaned.get(field_name)
        if team is not None:
            return cast(TournamentTeam, team)
        team_id = self._preserved_participant_ids.get(f"{field_name}_id")
        if not team_id:
            return None
        return cast(
            TournamentTeam | None,
            TournamentTeam.objects.filter(pk=team_id).first(),
        )

    def _configure_winner_side_field(self) -> None:
        """Настроить выбор победителя только из участников матча."""
        choices: list[tuple[str, str]] = [("", "— не выбран —")]
        if self.is_doubles_match:
            if self.instance.team1_id:
                choices.append(
                    (WINNER_SIDE_TEAM1, str(self.instance.team1)),
                )
            if self.instance.team2_id:
                choices.append(
                    (WINNER_SIDE_TEAM2, str(self.instance.team2)),
                )
            if self.instance.winner_team_id == self.instance.team1_id:
                self.fields["winner_side"].initial = WINNER_SIDE_TEAM1
            elif self.instance.winner_team_id == self.instance.team2_id:
                self.fields["winner_side"].initial = WINNER_SIDE_TEAM2
        else:
            if self.instance.player1_id:
                choices.append(
                    (WINNER_SIDE_PLAYER1, str(self.instance.player1)),
                )
            if self.instance.player2_id:
                choices.append(
                    (WINNER_SIDE_PLAYER2, str(self.instance.player2)),
                )
            if self.instance.winner_id == self.instance.player1_id:
                self.fields["winner_side"].initial = WINNER_SIDE_PLAYER1
            elif self.instance.winner_id == self.instance.player2_id:
                self.fields["winner_side"].initial = WINNER_SIDE_PLAYER2

        self.fields["winner_side"].choices = choices
        if len(choices) <= 1:
            self.fields["winner_side"].help_text = (
                "У матча не назначены оба участника — победителя выбрать нельзя."
            )

    def _configure_score_labels(self) -> None:
        """Понятные подписи для счёта по сетам."""
        side1 = (
            str(self.instance.team1)
            if self.is_doubles_match and self.instance.team1_id
            else str(self.instance.player1) if self.instance.player1_id else "Сторона 1"
        )
        side2 = (
            str(self.instance.team2)
            if self.is_doubles_match and self.instance.team2_id
            else str(self.instance.player2) if self.instance.player2_id else "Сторона 2"
        )
        for set_index in range(1, 4):
            self.fields[f"player1_set{set_index}"].label = (
                f"{side1} — {set_index}-й сет"
            )
            self.fields[f"player2_set{set_index}"].label = (
                f"{side2} — {set_index}-й сет"
            )
            self.fields[f"player1_set{set_index}"].help_text = ""
            self.fields[f"player2_set{set_index}"].help_text = ""

    def _hide_technical_fields(self) -> None:
        """Скрыть служебные поля, не нужные при вводе результата."""
        for field_name in (
            "rating_delta_player1",
            "rating_delta_player2",
        ):
            if field_name in self.fields:
                self.fields[field_name].widget = forms.HiddenInput()

    def clean(self) -> dict[str, Any]:
        """Проверить счёт, победителя и автоматически завершить матч.

        Returns:
            Очищенные данные формы.

        Raises:
            ValidationError: При несогласованном или неполном результате.
        """
        cleaned = cast(dict[str, Any], super().clean())
        winner_side = cleaned.get("winner_side") or ""
        has_score = _score_entered(cleaned)
        was_completed = self.instance.status in (
            Match.MatchStatus.COMPLETED,
            Match.MatchStatus.WALKOVER,
        )

        if winner_side and not has_score:
            raise forms.ValidationError(
                "Укажите счёт по сетам для выбранного победителя.",
            )

        if not has_score:
            if winner_side:
                raise forms.ValidationError("Укажите счёт по сетам.")
            if was_completed and self.instance.winner_id:
                raise forms.ValidationError(
                    "Нельзя очистить счёт у уже завершённого матча. "
                    "Введите новый результат целиком.",
                )
            return cleaned

        if cleaned.get("player1_set1") is None or cleaned.get("player2_set1") is None:
            raise forms.ValidationError(
                "Для завершения матча укажите счёт первого сета (геймы обеих сторон).",
            )

        sets_p1, sets_p2 = _count_sets_won(cleaned)
        if sets_p1 == sets_p2:
            raise forms.ValidationError(
                "По введённому счёту нельзя определить победителя. "
                "Проверьте геймы в сетах.",
            )

        if not winner_side:
            if self.is_doubles_match:
                winner_side = (
                    WINNER_SIDE_TEAM1 if sets_p1 > sets_p2 else WINNER_SIDE_TEAM2
                )
            else:
                winner_side = (
                    WINNER_SIDE_PLAYER1 if sets_p1 > sets_p2 else WINNER_SIDE_PLAYER2
                )
            cleaned["winner_side"] = winner_side

        if not winner_side:
            raise forms.ValidationError("Выберите победителя.")

        if self.is_doubles_match:
            team1 = self._resolve_team(cleaned, 1)
            team2 = self._resolve_team(cleaned, 2)
            winner_team = team1 if winner_side == WINNER_SIDE_TEAM1 else team2
            if winner_team is None:
                raise forms.ValidationError("Выберите победившую команду.")
            winner_won_more = (
                winner_side == WINNER_SIDE_TEAM1 and sets_p1 > sets_p2
            ) or (winner_side == WINNER_SIDE_TEAM2 and sets_p2 > sets_p1)
        else:
            player1 = self._resolve_player(cleaned, 1)
            player2 = self._resolve_player(cleaned, 2)
            winner = player1 if winner_side == WINNER_SIDE_PLAYER1 else player2
            if winner is None:
                raise forms.ValidationError("Выберите победителя из участников матча.")
            winner_won_more = (
                winner_side == WINNER_SIDE_PLAYER1 and sets_p1 > sets_p2
            ) or (winner_side == WINNER_SIDE_PLAYER2 and sets_p2 > sets_p1)

        if not winner_won_more:
            raise forms.ValidationError(
                "Счёт не соответствует выбранному победителю. "
                "Проверьте геймы в сетах и выбор победителя.",
            )

        cleaned["_finalize_match"] = True
        return cleaned

    def save(self, commit: bool = True) -> Match:
        """Сохранить матч и записать победителя из winner_side.

        Args:
            commit: Сохранять ли объект в БД.

        Returns:
            Экземпляр Match.
        """
        instance = cast(Match, super().save(commit=False))

        for field_name, field_id in self._preserved_participant_ids.items():
            if field_id and not getattr(instance, field_name):
                setattr(instance, field_name, field_id)

        winner_side = self.cleaned_data.get("winner_side")
        if winner_side == WINNER_SIDE_PLAYER1:
            instance.winner_id = instance.player1_id
            instance.winner_team = None
        elif winner_side == WINNER_SIDE_PLAYER2:
            instance.winner_id = instance.player2_id
            instance.winner_team = None
        elif winner_side == WINNER_SIDE_TEAM1:
            instance.winner_team_id = instance.team1_id
            if instance.team1_id:
                instance.winner_id = (
                    TournamentTeam.objects.filter(pk=instance.team1_id)
                    .values_list("player1_id", flat=True)
                    .first()
                )
        elif winner_side == WINNER_SIDE_TEAM2:
            instance.winner_team_id = instance.team2_id
            if instance.team2_id:
                instance.winner_id = (
                    TournamentTeam.objects.filter(pk=instance.team2_id)
                    .values_list("player1_id", flat=True)
                    .first()
                )

        if self.cleaned_data.get("_finalize_match"):
            instance.status = Match.MatchStatus.COMPLETED
            if not instance.completed_datetime:
                instance.completed_datetime = timezone.now()
            if instance.rating_status == Match.RatingCalcStatus.NOT_APPLICABLE:
                instance.rating_status = Match.RatingCalcStatus.PENDING

        if commit:
            instance.save()
            self.save_m2m()
        return instance


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Admin for Match model."""

    form = MatchAdminForm
    change_form_template = "admin/tournaments/match/change_form.html"
    list_display = (
        "tournament",
        "round_name",
        "round_index",
        "round_order",
        "is_consolation",
        "player1",
        "player2",
        "team1",
        "team2",
        "score_display",
        "winner",
        "status",
        "deadline",
    )
    list_filter = ("status", "is_consolation", "tournament__city", "tournament")
    search_fields = (
        "player1__user__first_name",
        "player1__user__last_name",
        "player2__user__first_name",
        "player2__user__last_name",
    )
    raw_id_fields = (
        "player1",
        "player2",
        "team1",
        "team2",
        "court",
        "next_match",
        "loser_next_match",
        "tvd_group",
    )
    date_hierarchy = "scheduled_datetime"

    _RESULT_FIELDSET = (
        "Результат",
        {
            "fields": (
                "winner_side",
                ("player1_set1", "player2_set1"),
                ("player1_set2", "player2_set2"),
                ("player1_set3", "player2_set3"),
            ),
            "description": (
                "Введите счёт и выберите победителя из участников матча. "
                "При сохранении матч автоматически получит статус «Завершён», "
                "обновится рейтинг и статистика игроков."
            ),
        },
    )

    _ADVANCED_FIELDSET = (
        "Служебные поля",
        {
            "classes": ("collapse",),
            "fields": (
                "court",
                "round_index",
                "round_order",
                "is_consolation",
                "tvd_group",
                "tvd_stage",
                "next_match",
                "loser_next_match",
                "placement_min",
                "placement_max",
                "scheduled_datetime",
                "match_type",
                "status",
                "completed_datetime",
                "points_player1",
                "points_player2",
                "rating_status",
                "rating_delta_player1",
                "rating_delta_player2",
            ),
        },
    )

    _ADD_FIELDSETS = (
        (
            "Матч",
            {
                "fields": (
                    "tournament",
                    "court",
                    "round_name",
                    "round_index",
                    "round_order",
                    "is_consolation",
                    "player1",
                    "player2",
                    "team1",
                    "team2",
                    "deadline",
                    "scheduled_datetime",
                    "status",
                ),
            },
        ),
        _RESULT_FIELDSET,
        _ADVANCED_FIELDSET,
    )

    def get_fieldsets(
        self,
        request: Any,
        obj: Match | None = None,
    ) -> tuple[Any, ...]:
        """Вернуть набор полей для создания или редактирования матча.

        Args:
            request: HTTP-запрос админки.
            obj: Редактируемый матч или None при создании.

        Returns:
            Кортеж fieldsets Django admin.
        """
        if obj is None:
            return self._ADD_FIELDSETS

        match_fields: list[str] = [
            "tournament",
            "round_name",
            "deadline",
        ]
        if obj.team1_id and obj.team2_id:
            match_fields.extend(["team1", "team2"])
        else:
            match_fields.extend(["player1", "player2"])

        return (
            (
                "Матч",
                {"fields": tuple(match_fields)},
            ),
            self._RESULT_FIELDSET,
            self._ADVANCED_FIELDSET,
        )

    def get_readonly_fields(
        self,
        request: Any,
        obj: Match | None = None,
    ) -> tuple[str, ...]:
        """Заблокировать участников и контекст турнира при редактировании.

        Args:
            request: HTTP-запрос админки.
            obj: Редактируемый матч или None при создании.

        Returns:
            Имена полей только для чтения.
        """
        if obj is None:
            return ()
        readonly: list[str] = [
            "tournament",
            "round_name",
            "deadline",
            "status",
            "completed_datetime",
            "points_player1",
            "points_player2",
            "rating_status",
            "rating_delta_player1",
            "rating_delta_player2",
        ]
        if obj.team1_id and obj.team2_id:
            readonly.extend(["team1", "team2"])
        else:
            readonly.extend(["player1", "player2"])
        return tuple(readonly)


@admin.register(HeadToHead)
class HeadToHeadAdmin(admin.ModelAdmin):
    """Admin for HeadToHead model."""

    list_display = ("player1", "player1_wins", "player2_wins", "player2")
    raw_id_fields = ("player1", "player2")


@admin.register(SeasonRating)
class SeasonRatingAdmin(admin.ModelAdmin):
    """Admin for SeasonRating model."""

    list_display = ("player", "season", "category", "points", "rank")
    list_filter = ("season", "category")
    search_fields = ("player__user__first_name", "player__user__last_name")
    raw_id_fields = ("player",)
    list_editable = ("points", "rank")


class MatchResultProposalAdminForm(forms.ModelForm):
    """Форма предложения результата с понятными подписями для счёта."""

    class Meta:
        model = MatchResultProposal
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["player1_set1"].label = "Игрок 1 — 1‑й сет (геймы)"
        self.fields["player2_set1"].label = "Игрок 2 — 1‑й сет (геймы)"
        self.fields["player1_set2"].label = "Игрок 1 — 2‑й сет (геймы)"
        self.fields["player2_set2"].label = "Игрок 2 — 2‑й сет (геймы)"
        self.fields["player1_set3"].label = "Игрок 1 — 3‑й сет (геймы)"
        self.fields["player2_set3"].label = "Игрок 2 — 3‑й сет (геймы)"


@admin.register(MatchResultProposal)
class MatchResultProposalAdmin(admin.ModelAdmin):
    """Admin for match proposals. При смене статуса на «Подтверждено» результат автоматически применяется к матчу."""

    form = MatchResultProposalAdminForm
    list_display = ("match", "proposer", "result", "status", "created_at")
    list_filter = ("status", "result")
    search_fields = ("match__tournament__name", "proposer__user__email")
    actions = [accept_proposal_action]
    raw_id_fields = ("match", "proposer")

    fieldsets = (
        (None, {"fields": ("match", "proposer", "result", "status")}),
        (
            "Предложенный счёт по сетам",
            {
                "fields": (
                    ("player1_set1", "player2_set1"),
                    ("player1_set2", "player2_set2"),
                    ("player1_set3", "player2_set3"),
                ),
                "description": "Игрок 1 и Игрок 2 — первая и вторая сторона в матче. Геймы в 1‑м, 2‑м и 3‑м сете.",
            },
        ),
    )


@admin.register(TournamentPlayerResult)
class TournamentPlayerResultAdmin(admin.ModelAdmin):
    """Результаты игроков в турнире (раунд вылета или итоговое место)."""

    list_display = (
        "tournament",
        "player",
        "place",
        "round_eliminated",
        "fan_points",
        "is_consolation",
    )


@admin.register(TVDGroup)
class TVDGroupAdmin(admin.ModelAdmin):
    """Группы ТВД. Участников можно редактировать во вкладке ниже."""

    list_display = ("tournament", "name", "order", "is_completed")
    list_filter = ("tournament", "is_completed")
    ordering = ("tournament", "order")
    inlines = [TVDGroupMemberInline]
    raw_id_fields = ("tournament",)


@admin.register(TVDGroupMember)
class TVDGroupMemberAdmin(admin.ModelAdmin):
    """Участник группы ТВД (ручное редактирование мест и статистики)."""

    list_display = (
        "group",
        "player",
        "seed",
        "wins",
        "losses",
        "games_won",
        "games_lost",
        "final_place",
    )
    list_filter = ("group__tournament",)
    search_fields = ("player__user__first_name", "player__user__last_name")
    raw_id_fields = ("group", "player")


@admin.register(SeasonPoints)
class SeasonPointsAdmin(admin.ModelAdmin):
    """Админка для сезонных очков игроков."""

    list_display = (
        "player",
        "current_season_points",
        "season_name",
        "season_year",
        "updated_at",
    )
    list_filter = ("season_name", "season_year")
    search_fields = (
        "player__user__first_name",
        "player__user__last_name",
        "player__user__email",
    )
    readonly_fields = ("updated_at",)
    ordering = ("-current_season_points", "-season_year", "-season_name")


@admin.register(SeasonArchive)
class SeasonArchiveAdmin(admin.ModelAdmin):
    """Админка для архива результатов сезонов."""

    list_display = (
        "player",
        "season_name",
        "season_year",
        "final_points",
        "final_rank",
        "archived_at",
    )
    list_filter = ("season_name", "season_year", "final_rank")
    search_fields = (
        "player__user__first_name",
        "player__user__last_name",
        "player__user__email",
    )
    readonly_fields = ("archived_at",)
    raw_id_fields = ("player",)
    ordering = ("-season_year", "-season_name", "final_rank", "-final_points")
