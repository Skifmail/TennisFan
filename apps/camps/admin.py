"""Администрирование собственных программ и безопасные служебные ссылки."""

from __future__ import annotations

from typing import Any, cast

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Min, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .admin_forms import (
    BaseCampPackageFormSet,
    CampAdminForm,
    CampCreateForm,
    CampPackageFormSet,
    CampPackageInlineForm,
    CampSessionAdminForm,
    CampSessionCreateForm,
)
from .models import (
    Camp,
    CampApplication,
    CampCancellation,
    CampExpense,
    CampGame,
    CampPackage,
    CampParticipant,
    CampPayment,
    CampReminder,
    CampSession,
    CampTournament,
)

CAMP_MAIN_FIELDS = (
    "title",
    "subtitle",
    "audience",
    "description",
    "image",
    "coaches",
    "organizers",
)
CAMP_EXTRA_FIELDS = ("slug", "levels", "program", "preparation")
SESSION_MAIN_FIELDS = (
    "start_date",
    "end_date",
    "city",
    "venue",
    "capacity",
    "terms",
)
SESSION_EXTRA_FIELDS = (
    "registration_deadline",
    "country",
    "minimum_players",
    "min_age",
    "max_age",
    "auto_reserve",
    "tennis_hours",
    "players_per_court",
    "surface",
    "accommodation",
    "included",
    "excluded",
    "weather_policy",
    "contact_email",
    "contact_phone",
)


@admin.register(Camp)
class CampAdmin(admin.ModelAdmin):
    """Редактор программы и единая точка входа в создание кемпа."""

    form = CampAdminForm
    list_display = (
        "title",
        "audience",
        "is_published",
        "sessions_summary",
        "public_link",
        "updated_at",
    )
    list_display_links = ("title",)
    list_filter = ("is_published", "audience")
    search_fields = ("title", "subtitle", "description")
    prepopulated_fields = {"slug": ("title",)}
    change_list_template = "admin/camps/camp/change_list.html"
    readonly_fields = ("setup_help", "public_link", "session_list", "updated_at")
    fieldsets = (
        ("Порядок работы", {"fields": ("setup_help",)}),
        (
            "1. Программа кемпа",
            {
                "fields": (
                    "title",
                    "slug",
                    "subtitle",
                    "audience",
                    "description",
                    "image",
                )
            },
        ),
        (
            "2. Команда",
            {
                "fields": ("coaches", "organizers"),
                "description": (
                    "Тренеры и организаторы, которых увидят на публичной странице кемпа. "
                    "Показываются только действующие тренеры и активные участники платформы."
                ),
            },
        ),
        (
            "3. Дополнительно",
            {
                "classes": ("collapse",),
                "fields": ("levels", "program", "preparation"),
            },
        ),
        (
            "4. Публикация",
            {
                "fields": ("is_published", "public_link", "updated_at"),
                "description": (
                    "Кемп появится на сайте, когда включена галочка «Опубликован». "
                    "Заявки принимает заезд со статусом «Идёт набор» и активным пакетом."
                ),
            },
        ),
        (
            "Заезды",
            {
                "fields": ("session_list",),
                "description": (
                    "У одного кемпа может быть несколько дат. Пакеты добавляются внутри заезда."
                ),
            },
        ),
    )

    class Media:
        """Подключает стили подсказок и автодополнение города."""

        js = ("js/city_autocomplete.js",)
        css = {"all": ["admin/css/camps_admin.css"]}

    def get_queryset(self, request: HttpRequest) -> QuerySet[Camp]:
        """Добавляет сводку по заездам без запроса на каждую строку."""
        queryset = cast(QuerySet[Camp], super().get_queryset(request))
        return queryset.annotate(
            sessions_count=Count("sessions", distinct=True),
            next_start=Min("sessions__start_date"),
        )

    def add_view(
        self,
        request: HttpRequest,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """Создаёт кемп, первый заезд и пакеты на одной странице."""
        if not self.has_add_permission(request):
            raise PermissionDenied
        publish = request.method == "POST" and request.POST.get("publish") == "on"
        if request.method == "POST":
            camp_form = CampCreateForm(request.POST, request.FILES)
            session_form = CampSessionCreateForm(request.POST)
            package_formset = cast(
                BaseCampPackageFormSet,
                CampPackageFormSet(
                    request.POST,
                    queryset=CampPackage.objects.none(),
                    prefix="packages",
                ),
            )
            package_formset.require_active = publish
            camp_valid = camp_form.is_valid()
            session_valid = session_form.is_valid()
            formset_valid = package_formset.is_valid()
            if camp_valid and session_valid and formset_valid:
                try:
                    with transaction.atomic():
                        camp = camp_form.save(commit=False)
                        camp.is_published = publish
                        camp.save()
                        camp_form.save_m2m()
                        session = session_form.save(commit=False)
                        session.camp = camp
                        session.status = (
                            CampSession.Status.OPEN
                            if publish
                            else CampSession.Status.DRAFT
                        )
                        session.save()
                        for package in package_formset.save(commit=False):
                            package.session = session
                            package.save()
                        if publish:
                            session.full_clean()
                            session.save(update_fields=["status"])
                except ValidationError as exc:
                    if exc.message_dict:
                        for field_name, field_messages in exc.message_dict.items():
                            target = (
                                field_name
                                if field_name in session_form.fields
                                else None
                            )
                            for message in field_messages:
                                session_form.add_error(target, message)
                    else:
                        session_form.add_error(None, exc)
                else:
                    self.log_addition(
                        request, camp, "Создан через форму «Создать кемп»"
                    )
                    self.message_user(
                        request,
                        (
                            "Кемп создан и опубликован на сайте."
                            if publish
                            else "Кемп сохранён как черновик. Опубликуйте его, когда будете готовы."
                        ),
                        messages.SUCCESS,
                    )
                    return redirect("admin:camps_camp_change", camp.pk)
        else:
            camp_form = CampCreateForm()
            session_form = CampSessionCreateForm(
                initial={
                    "country": "Россия",
                    "capacity": 12,
                    "minimum_players": 1,
                    "min_age": 18,
                    "tennis_hours": 0,
                    "players_per_court": 4,
                    "auto_reserve": True,
                }
            )
            package_formset = cast(
                BaseCampPackageFormSet,
                CampPackageFormSet(
                    queryset=CampPackage.objects.none(),
                    prefix="packages",
                ),
            )
        context = {
            **self.admin_site.each_context(request),
            "title": "Создать кемп",
            "opts": self.model._meta,
            "camp_form": camp_form,
            "session_form": session_form,
            "package_formset": package_formset,
            "camp_main_fields": [camp_form[name] for name in CAMP_MAIN_FIELDS],
            "camp_extra_fields": [camp_form[name] for name in CAMP_EXTRA_FIELDS],
            "session_main_fields": [session_form[name] for name in SESSION_MAIN_FIELDS],
            "session_extra_fields": [
                session_form[name] for name in SESSION_EXTRA_FIELDS
            ],
            "camp_extra_open": any(
                camp_form[name].errors for name in CAMP_EXTRA_FIELDS
            ),
            "session_extra_open": any(
                session_form[name].errors for name in SESSION_EXTRA_FIELDS
            ),
            "has_errors": bool(
                camp_form.errors
                or session_form.errors
                or package_formset.non_form_errors()
                or any(package_formset.errors)
            ),
            "publish": publish if request.method == "POST" else True,
        }
        return TemplateResponse(request, "admin/camps/camp/create_camp.html", context)

    @admin.display(description="Заезды")
    def sessions_summary(self, obj: Camp) -> str:
        """Показывает количество заездов и ближайшую дату."""
        count = int(getattr(obj, "sessions_count", 0) or 0)
        next_start = getattr(obj, "next_start", None)
        if not count:
            return "Нет заездов"
        if next_start is not None:
            return f"{count} · ближайший {next_start:%d.%m.%Y}"
        return str(count)

    @admin.display(description="На сайте")
    def public_link(self, obj: Camp) -> str:
        """Показывает, опубликован ли кемп, и ведёт на публичную страницу."""
        if not obj.pk:
            return "—"
        if not obj.is_published:
            return "Черновик — не виден на сайте"
        return cast(
            str,
            format_html(
                '<a href="{}" target="_blank" rel="noopener">Открыть ↗</a>',
                obj.get_absolute_url(),
            ),
        )

    @admin.display(description="Порядок работы")
    def setup_help(self, obj: Camp) -> str:
        """Объясняет, что делать после создания кемпа."""
        if not obj.pk:
            return "Сначала сохраните кемп."
        return cast(
            str,
            mark_safe(
                '<div class="camp-admin-callout">'
                "<strong>Как управлять кемпом</strong>"
                "<ol>"
                "<li>Проверьте программу и галочку «Опубликован».</li>"
                "<li>Создайте заезд: даты, место и вместимость.</li>"
                "<li>В заезде добавьте хотя бы один активный пакет участия.</li>"
                "<li>Поставьте заезду статус «Идёт набор».</li>"
                "</ol>"
                "<p>Заявки приходят в раздел «Заявки». Состав и оплату удобнее "
                "вести на экране заезда.</p>"
                "</div>"
            ),
        )

    @admin.display(description="Заезды кемпа")
    def session_list(self, obj: Camp) -> str:
        """Показывает заезды кемпа и ссылки на их настройку."""
        if not obj.pk:
            return "Сначала сохраните кемп."
        sessions = obj.sessions.annotate(
            active_packages_count=Count(
                "packages",
                filter=Q(packages__is_active=True),
                distinct=True,
            ),
            applications_count=Count("applications", distinct=True),
        ).order_by("start_date")
        return cast(
            str,
            mark_safe(
                render_to_string(
                    "admin/camps/_session_list.html",
                    {"sessions": sessions, "camp": obj},
                )
            ),
        )


class PackageInline(admin.StackedInline):
    """Пакеты, доступные для выбранного заезда."""

    model = CampPackage
    form = CampPackageInlineForm
    extra = 1
    verbose_name = "Пакет участия"
    verbose_name_plural = "Пакеты участия (обязательно)"
    classes = ("camp-admin-package-inline",)


class ExpenseInline(admin.TabularInline):
    """План расходов по валютам."""

    model = CampExpense
    extra = 0
    classes = ("collapse",)
    verbose_name = "Плановый расход"
    verbose_name_plural = "Плановые расходы (необязательно)"


class TournamentInline(admin.TabularInline):
    """Связи с существующими турнирами платформы."""

    model = CampTournament
    extra = 0
    classes = ("collapse",)
    verbose_name = "Турнир кемпа"
    verbose_name_plural = "Турниры кемпа (необязательно)"
    raw_id_fields = ("tournament",)


@admin.register(CampSession)
class CampSessionAdmin(admin.ModelAdmin):
    """Редактор заезда со ссылкой на рабочий экран команды."""

    form = CampSessionAdminForm
    change_form_template = "admin/camps/campsession/change_form.html"
    list_display = (
        "camp",
        "city",
        "start_date",
        "status",
        "capacity",
        "packages_ready",
        "team_link",
    )
    list_filter = ("status", "country")
    search_fields = ("camp__title", "city", "venue")
    date_hierarchy = "start_date"
    list_select_related = ("camp",)
    inlines = (PackageInline, ExpenseInline, TournamentInline)
    readonly_fields = ("team_link",)
    raw_id_fields = ("court", "coordinator")
    fieldsets = (
        (
            "1. Даты",
            {
                "fields": ("camp", "start_date", "end_date", "registration_deadline"),
                "description": "Когда проходит заезд и до какого дня принимаются заявки.",
            },
        ),
        (
            "2. Место",
            {
                "fields": ("country", "city", "venue", "court", "surface"),
                "description": "Где проходит заезд. Город и площадку увидят на сайте.",
            },
        ),
        (
            "3. Вместимость и возраст",
            {
                "fields": (
                    "capacity",
                    "minimum_players",
                    "min_age",
                    "max_age",
                    "tennis_hours",
                    "players_per_court",
                    "auto_reserve",
                ),
                "description": "Сколько игроков берём и для какого возраста.",
            },
        ),
        (
            "4. Условия и контакты",
            {
                "fields": (
                    "accommodation",
                    "included",
                    "excluded",
                    "terms",
                    "weather_policy",
                    "coordinator",
                    "contact_email",
                    "contact_phone",
                ),
                "description": "Что входит, что оплачивается отдельно и с кем связаться.",
            },
        ),
        (
            "5. Публикация",
            {
                "fields": ("status", "team_link"),
                "description": (
                    "«Идёт набор» — заезд виден на сайте и принимает заявки, "
                    "если есть активный пакет. «Черновик» — скрыт."
                ),
            },
        ),
    )

    class Media:
        """Подключает стили подсказок и автодополнение города."""

        js = ("js/city_autocomplete.js",)
        css = {"all": ["admin/css/camps_admin.css"]}

    def has_module_permission(self, request: HttpRequest) -> bool:
        """Скрывает заезды из меню: они создаются со страницы кемпа."""
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[CampSession]:
        """Добавляет счётчик активных пакетов без запроса на каждую строку."""
        queryset = cast(QuerySet[CampSession], super().get_queryset(request))
        return queryset.annotate(
            active_packages_count=Count("packages", filter=Q(packages__is_active=True))
        )

    @admin.display(boolean=True, description="Есть пакеты")
    def packages_ready(self, obj: CampSession) -> bool:
        """Показывает, можно ли принять заявку на заезд."""
        return bool(getattr(obj, "active_packages_count", 0))

    def save_related(
        self, request: HttpRequest, form: Any, formsets: Any, change: bool
    ) -> None:
        """Предупреждает, если открытый заезд сохранили без активных пакетов."""
        super().save_related(request, form, formsets, change)
        session = cast(CampSession, form.instance)
        if session.status == CampSession.Status.OPEN and not (
            session.packages.filter(is_active=True).exists()
        ):
            self.message_user(
                request,
                "У заезда нет активных пакетов: кнопка заявки на публичной странице скрыта.",
                level=messages.WARNING,
            )

    @admin.display(description="Участники и заявки")
    def team_link(self, obj: CampSession) -> str:
        """Возвращает ссылку на управление заездом."""
        if not obj.pk:
            return "Сначала сохраните заезд."
        return cast(
            str,
            format_html(
                '<a href="{}">Открыть заезд →</a>',
                reverse("camps:manage_session", args=[obj.pk]),
            ),
        )


@admin.register(CampPackage)
class CampPackageAdmin(admin.ModelAdmin):
    """Скрытый служебный список пакетов; обычная работа идёт внутри заезда."""

    list_display = ("name", "session", "price", "currency", "is_active")
    list_filter = ("is_active", "currency", "session__camp")
    search_fields = ("name", "session__camp__title")
    list_select_related = ("session__camp",)
    raw_id_fields = ("session",)

    def has_module_permission(self, request: HttpRequest) -> bool:
        """Не показывает пакеты отдельным пунктом меню."""
        return False


@admin.register(CampApplication)
class CampApplicationAdmin(admin.ModelAdmin):
    """Не позволяет обходить проверку мест обычным сохранением модели."""

    list_display = ("contact_name", "session", "status", "created_at", "manage_link")
    list_filter = ("status", "session")
    search_fields = ("contact_name", "email", "phone")
    list_select_related = ("session__camp",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Заявки создаются формой с полным составом поездки."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Сохраняет историю участия и финансов."""
        return False

    def get_readonly_fields(
        self, request: HttpRequest, obj: Any = None
    ) -> tuple[str, ...]:
        """Все изменения проходят через рабочий экран и бизнес-операции."""
        return tuple(field.name for field in self.model._meta.fields) + ("manage_link",)

    @admin.display(description="Работа с заявкой")
    def manage_link(self, obj: CampApplication) -> str:
        """Открывает подтверждение участия и журнал расчётов."""
        return cast(
            str,
            format_html(
                '<a href="{}">Открыть заявку →</a>',
                reverse("camps:manage_application", args=[obj.pk]),
            ),
        )


@admin.register(CampParticipant)
class CampParticipantAdmin(admin.ModelAdmin):
    """Позволяет назначать группы и профили без изменения подтверждённого состава."""

    list_display = ("name", "role", "application", "training_group")
    list_filter = ("application__session", "role", "training_group")
    search_fields = ("name",)
    raw_id_fields = ("player",)
    readonly_fields = ("application", "role")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Состав фиксируется при отправке заявки."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Не позволяет освободить место удалением участника."""
        return False


@admin.register(CampPayment)
class CampPaymentAdmin(admin.ModelAdmin):
    """Журнал только для чтения; исправления вносятся возвратной записью."""

    list_display = ("application", "amount", "reference", "recorded_by", "created_at")
    readonly_fields = (
        "application",
        "amount",
        "reference",
        "recorded_by",
        "created_at",
        "operation_key",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Проводки создаются через сервис с проверкой остатка."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Сохраняет неизменяемую историю ручного учёта."""
        return False


@admin.register(CampGame)
class CampGameAdmin(admin.ModelAdmin):
    """Журнал товарищеских игр, изолированный от рейтингового движка."""

    list_display = ("session", "player1", "player2", "score", "played_at")
    list_filter = ("session",)
    raw_id_fields = ("player1", "player2")


@admin.register(CampCancellation)
class CampCancellationAdmin(admin.ModelAdmin):
    """История запросов; решения принимаются только через сервис координатора."""

    list_display = ("application", "status", "created_at", "resolved_at", "manage_link")
    list_filter = ("status", "application__session")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрос подаёт сам участник."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Сохраняет историю обращений."""
        return False

    def get_readonly_fields(
        self, request: HttpRequest, obj: Any = None
    ) -> tuple[str, ...]:
        """Защищает статус от изменения в обход проверки мест и уведомлений."""
        return tuple(field.name for field in self.model._meta.fields) + ("manage_link",)

    @admin.display(description="Рассмотреть")
    def manage_link(self, obj: CampCancellation) -> str:
        """Открывает единый рабочий экран заявки."""
        return cast(
            str,
            format_html(
                '<a href="{}">Открыть заявку →</a>',
                reverse("camps:manage_application", args=[obj.application_id]),
            ),
        )


@admin.register(CampReminder)
class CampReminderAdmin(admin.ModelAdmin):
    """Диагностика плановых событий, включая неопределённую SMTP-доставку."""

    list_display = ("application", "kind", "basis", "status", "attempts", "updated_at")
    list_filter = ("status", "kind")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Поводы создаёт планировщик."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Не позволяет повторить доставку удалением отметки."""
        return False

    def get_readonly_fields(
        self, request: HttpRequest, obj: Any = None
    ) -> tuple[str, ...]:
        """Журнал доступен только для чтения."""
        return tuple(field.name for field in self.model._meta.fields)
