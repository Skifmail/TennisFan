"""
Формы клубного раздела: регистрация клуба, инвайты, приглашения, панель клуба.
"""

from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.text import slugify

from apps.core.geo import (
    GeoRegion,
    city_uses_moscow_geo,
    geo_areas_client_payload,
    is_moscow_city_name,
    oblast_geo_area_for_city,
    should_show_moscow_geo_fields,
)
from apps.core.models import GeoArea, UserTelegramLink
from apps.core.sports import Sport, VenueSport, sport_code
from apps.courts.models import Court
from apps.tournaments.models import (
    MatchFormat,
    Tournament,
    TournamentFormat,
    TournamentType,
    TournamentVariant,
)
from apps.tournaments.utils import generate_unique_tournament_slug
from apps.users.models import SkillLevel

from .court_search import active_courts
from .models import (
    Club,
    ClubLegalDocument,
    ClubMember,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
    ClubPlayerPlan,
    ClubRegistrationLimitPeriod,
)
from .payment_utils import get_secret_mask


class CourtSearchWidget(forms.Widget):
    """Поле поиска корта: в форму уходит id выбранной площадки."""

    def __init__(self, *args: Any, search_url: str = "", **kwargs: Any) -> None:
        self.search_url = search_url
        super().__init__(*args, **kwargs)

    def render(
        self,
        name: str,
        value: Any,
        attrs: dict[str, Any] | None = None,
        renderer: Any = None,
    ) -> SafeString:
        final_attrs = self.build_attrs(self.attrs, attrs)
        input_id = str(final_attrs.get("id") or f"id_{name}")
        css_class = str(final_attrs.get("class") or "form-control")
        disabled = " disabled" if final_attrs.get("disabled") else ""
        court_id = ""
        label = ""
        venue = ""
        if value not in (None, ""):
            try:
                court = (
                    Court.objects.filter(pk=int(value))
                    .only("name", "city", "venue_sport")
                    .first()
                )
            except (TypeError, ValueError):
                court = None
            if court is not None:
                court_id = str(court.pk)
                label = f"{court.name} — {court.city}"
                venue = court.venue_sport or "tennis"
        clear_hidden = "" if court_id else " hidden"
        return format_html(
            '<div class="court-search" data-court-search data-search-url="{}" data-venue-sport="{}">'
            '<input type="hidden" name="{}" value="{}" data-court-value>'
            '<input type="search" id="{}" class="{} court-search__input" value="{}" '
            'placeholder="Название или город" autocomplete="off" spellcheck="false" '
            'aria-autocomplete="list" data-court-query{}>'
            '<button type="button" class="court-search__clear" data-court-clear{}{}>Сбросить</button>'
            '<ul class="court-search__list" role="listbox" hidden data-court-list></ul>'
            "</div>",
            self.search_url,
            venue,
            name,
            court_id,
            input_id,
            css_class,
            label,
            disabled,
            clear_hidden,
            disabled,
        )


def _court_matches_sport(court: Court, sport: str) -> bool:
    """Проверить, подходит ли площадка выбранному виду спорта.

    Args:
        court: Корт из каталога.
        sport: ``tennis`` или ``padel``.

    Returns:
        bool: True, если корт можно выбрать для этого спорта.
    """
    venue = court.venue_sport or VenueSport.TENNIS
    if sport == Sport.PADEL:
        return venue in {VenueSport.PADEL, VenueSport.BOTH}
    return venue in {VenueSport.TENNIS, VenueSport.BOTH, ""}


class ClubLegalDocumentForm(forms.ModelForm):
    """Редактирование оферты клуба владельцем."""

    class Meta:
        model = ClubLegalDocument
        fields = ("title", "content", "version", "is_published")
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Заголовок документа"}
            ),
            "content": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 22,
                    "placeholder": "Текст в формате Markdown",
                }
            ),
            "version": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Например: 1.0"}
            ),
            "is_published": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class ClubRegistrationStep1Form(forms.Form):
    """Форма шага 1 регистрации клуба — данные о клубе."""

    name = forms.CharField(
        label="Официальное название клуба",
        max_length=255,
        min_length=2,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Например: Теннисный клуб «Спартак»",
            }
        ),
    )
    city = forms.CharField(
        label="Населённый пункт",
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Город, село, деревня, пгт…",
            }
        ),
    )
    address = forms.CharField(
        label="Адрес",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Улица, дом, корпус",
            }
        ),
    )
    logo = forms.ImageField(
        label="Логотип клуба",
        required=False,
        help_text="JPG, PNG или WebP до 5 МБ",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )
    email = forms.EmailField(
        label="Контактный email",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    phone = forms.CharField(
        label="Контактный телефон",
        max_length=50,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "+7 (999) 123-45-67"}
        ),
    )
    admin_name = forms.CharField(
        label="ФИО ответственного / администратора",
        max_length=255,
        min_length=2,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    description = forms.CharField(
        label="Краткое описание клуба",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )

    def get_slug(self) -> str:
        """Возвращает slug, сгенерированный из name."""
        name = self.cleaned_data.get("name", "")
        return (slugify(name) or "club")[:100]


class ClubInviteLinkForm(forms.Form):
    """Параметры создания инвайт-ссылки."""

    expires_days = forms.IntegerField(
        label="Срок действия (дней)",
        required=False,
        min_value=1,
        max_value=365,
        widget=forms.NumberInput(
            attrs={
                "placeholder": "Пусто — без срока",
                "class": "club-members-input",
                "inputmode": "numeric",
            }
        ),
    )
    max_uses = forms.IntegerField(
        label="Лимит использований",
        required=False,
        min_value=1,
        max_value=10000,
        widget=forms.NumberInput(
            attrs={
                "placeholder": "Пусто — без лимита",
                "class": "club-members-input",
                "inputmode": "numeric",
            }
        ),
    )


class InviteByEmailForm(forms.Form):
    """Приглашение игрока по email."""

    email = forms.EmailField(
        label="Email пользователя",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "player@example.com",
                "class": "club-members-input",
                "autocomplete": "email",
            }
        ),
    )


class ClubInviteImportForm(forms.Form):
    """Импорт приглашений из CSV или TXT файла."""

    file = forms.FileField(
        label="Файл CSV или TXT",
        widget=forms.FileInput(
            attrs={
                "accept": ".csv,.txt",
                "class": "club-members-input club-invites-file-input",
            }
        ),
    )

    def clean_file(self):
        """Проверяет формат файла для импорта приглашений."""
        file = self.cleaned_data["file"]
        if not file.name.lower().endswith((".csv", ".txt")):
            raise forms.ValidationError(
                "Загрузите CSV или TXT с email в каждой строке."
            )
        return file


class ClubProfileEditForm(forms.ModelForm):
    """Редактирование профиля клуба (название, контакты, описание, публичность)."""

    def __init__(self, *args, **kwargs):
        """Добавляет компактное оформление и подсказки полям формы клуба."""
        super().__init__(*args, **kwargs)

        placeholders = {
            "name": "Например: Теннисный клуб «Спартак»",
            "city": "Москва",
            "address": "Улица, дом, корпус",
            "email": "club@example.com",
            "phone": "+7 (999) 123-45-67",
            "admin_name": "Имя ответственного",
            "description": "Коротко опишите клуб, атмосферу, кортовую базу и формат тренировок.",
        }

        textarea_rows = {
            "address": 3,
            "description": 4,
        }

        for name, field in self.fields.items():
            widget = field.widget
            widget.attrs["class"] = "club-edit-input"
            if name in placeholders:
                widget.attrs.setdefault("placeholder", placeholders[name])
            if name in textarea_rows:
                widget.attrs["rows"] = textarea_rows[name]

        self.fields["logo"].help_text = "JPG, PNG или WebP до 5 МБ"
        self.fields["hero_image"].help_text = (
            "Широкий баннер для публичной страницы клуба. JPG, PNG или WebP."
        )
        self.fields["is_public"].widget.attrs["class"] = "club-edit-checkbox"

    class Meta:
        model = Club
        fields = [
            "name",
            "city",
            "address",
            "logo",
            "hero_image",
            "email",
            "phone",
            "admin_name",
            "description",
            "is_public",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class ClubMembershipFeeSettingsForm(forms.ModelForm):
    """Настройка параметров клубного взноса без платёжных реквизитов."""

    class Meta:
        model = ClubMembershipFee
        fields = [
            "amount",
            "currency",
            "period",
            "period_start_day",
            "description",
            "restrict_tournament_access",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        """Добавляет единое оформление полям формы настройки взносов."""
        super().__init__(*args, **kwargs)
        for field_name in [
            "amount",
            "currency",
            "period",
            "period_start_day",
            "description",
        ]:
            self.fields[field_name].widget.attrs[
                "class"
            ] = "form-control club-payments-form__control"

        self.fields["amount"].widget.attrs.setdefault("placeholder", "Например, 4000")
        self.fields["currency"].widget.attrs.setdefault("placeholder", "RUB")
        self.fields["period_start_day"].widget.attrs.setdefault("min", 1)
        self.fields["period_start_day"].widget.attrs.setdefault("max", 28)
        self.fields["description"].widget.attrs.setdefault(
            "placeholder",
            "Коротко опишите правила и назначение клубного взноса для участников.",
        )

        for field_name in ["restrict_tournament_access", "is_active"]:
            self.fields[field_name].widget.attrs["class"] = "form-checkbox"


class ClubPaymentSettingsForm(forms.ModelForm):
    """Настройка подключения клубной YooKassa."""

    payment_shop_id = forms.CharField(
        label="ID магазина (ЮKassa)",
        max_length=255,
        required=False,
    )
    new_secret_key = forms.CharField(
        label="Новый Secret Key (ЮKassa)",
        required=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password"},
        ),
        help_text="Секретный ключ хранится в зашифрованном виде и не отображается. "
        "Оставьте поле пустым, чтобы не менять текущий ключ.",
    )

    class Meta:
        model = ClubMembershipFee
        fields = [
            "payment_shop_id",
        ]

    def __init__(self, *args, **kwargs):
        """Инициализация формы с учётом уже сохранённого секрета."""
        super().__init__(*args, **kwargs)
        self.fields["payment_shop_id"].widget.attrs.update(
            {
                "class": "form-control club-payments-form__control",
                "placeholder": "Например, 123456",
                "autocomplete": "off",
            }
        )
        self.fields["new_secret_key"].widget.attrs.update(
            {
                "class": "form-control club-payments-form__control",
                "placeholder": "Введите Secret Key",
            }
        )
        fee: ClubMembershipFee | None = (
            self.instance if isinstance(self.instance, ClubMembershipFee) else None
        )
        if fee and fee.payment_api_key:
            self.fields["new_secret_key"].help_text = (
                f"Секретный ключ уже сохранён ({get_secret_mask()}). "
                "Введите новый, чтобы заменить, или оставьте пустым."
            )

    def clean(self):
        """Валидация зависимых полей провайдера и реквизитов."""
        cleaned_data = super().clean()
        shop_id = (cleaned_data.get("payment_shop_id") or "").strip()
        new_secret = (cleaned_data.get("new_secret_key") or "").strip()

        if shop_id:
            fee: ClubMembershipFee | None = (
                self.instance if isinstance(self.instance, ClubMembershipFee) else None
            )
            has_existing_secret = bool(fee and fee.payment_api_key)
            if not has_existing_secret and not new_secret:
                self.add_error(
                    "new_secret_key", "Укажите Secret Key для подключения ЮKassa."
                )
        elif new_secret:
            self.add_error(
                "payment_shop_id", "Укажите ID магазина ЮKassa для сохранения ключа."
            )

        return cleaned_data

    def save(self, commit: bool = True) -> ClubMembershipFee:
        """Сохраняет настройки с единственным поддерживаемым провайдером YooKassa."""
        instance: ClubMembershipFee = super().save(commit=False)
        if instance.payment_shop_id:
            instance.payment_provider = ClubMembershipFee.PaymentProvider.YOOKASSA
        else:
            instance.payment_provider = ""
        if commit:
            instance.save()
        return instance


class MarkFeePaidForm(forms.Form):
    """Ручная отметка об оплате взноса участником."""

    member = forms.ModelChoiceField(
        queryset=ClubMember.objects.none(),
        label="Участник",
    )
    period_label = forms.CharField(
        label="Период (напр. 2026-03)",
        max_length=50,
    )
    amount = forms.DecimalField(
        label="Сумма",
        max_digits=10,
        decimal_places=2,
    )

    def __init__(self, *args, club=None, fee=None, **kwargs):
        super().__init__(*args, **kwargs)
        if club:
            self.fields["member"].queryset = (
                club.members.filter(status=ClubMemberStatus.ACTIVE)
                .select_related("user")
                .order_by("user__email")
            )
        if fee:
            self.fields["amount"].initial = fee.amount


class ClubMemberBalanceAdjustForm(forms.Form):
    """Ручная корректировка баланса участника клуба."""

    OPERATION_CHOICES = (
        ("credit", "Начислить сумму"),
        ("debit", "Списать сумму"),
        ("set", "Установить точный баланс"),
    )

    operation = forms.ChoiceField(
        label="Операция",
        choices=OPERATION_CHOICES,
    )
    amount = forms.DecimalField(
        label="Сумма",
        max_digits=10,
        decimal_places=2,
        min_value=0,
    )
    reason = forms.CharField(
        label="Причина корректировки",
        max_length=255,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    confirm_warning = forms.BooleanField(
        label="Подтверждаю, что меняю баланс вручную только для исправления ошибки или служебной корректировки.",
        required=True,
    )

    def __init__(self, *args, **kwargs):
        """Подключает единые CSS-классы для админской формы баланса."""
        super().__init__(*args, **kwargs)
        self.fields["operation"].widget.attrs.update({"class": "club-members-select"})
        self.fields["amount"].widget.attrs.update({"class": "club-members-input"})
        self.fields["reason"].widget.attrs.update({"class": "club-members-textarea"})
        self.fields["confirm_warning"].widget.attrs.update(
            {"class": "club-balance-form__checkbox"}
        )

    def clean(self):
        """Проверяет, что причина указана, а сумма корректна для выбранной операции."""
        cleaned_data = super().clean()
        operation = cleaned_data.get("operation")
        amount = cleaned_data.get("amount")
        reason = (cleaned_data.get("reason") or "").strip()

        if not reason:
            self.add_error("reason", "Укажите причину корректировки.")

        if amount is None:
            return cleaned_data

        if operation in {"credit", "debit"} and amount <= 0:
            self.add_error("amount", "Сумма изменения должна быть больше нуля.")

        return cleaned_data


class ClubTournamentCreateForm(forms.ModelForm):
    """Создание турнира клуба по модели Tournament с логикой глобальной платформы."""

    allowed_categories = forms.MultipleChoiceField(
        choices=SkillLevel.choices,
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Допустимые уровни участников",
        help_text="Выберите от 1 до 5 уровней. Зарегистрироваться смогут только игроки выбранных категорий.",
    )

    class Meta:
        model = Tournament
        fields = [
            "name",
            "slug",
            "description",
            "regulation_extra",
            "image",
            "sport",
            "format",
            "variant",
            "entry_fee",
            "is_one_day",
            "allow_postpayment",
            "postpayment_deadline_hours",
            "city",
            "region",
            "geo_area",
            "court",
            "gender",
            "allowed_categories",
            "tournament_type",
            "start_after_fill",
            "start_date",
            "end_date",
            "registration_deadline",
            "min_participants",
            "max_participants",
            "min_teams",
            "max_teams",
            "match_days_per_round",
            "match_format",
            "fan_points_r1",
            "fan_points_r2",
            "fan_points_sf",
            "fan_points_final",
            "fan_points_winner",
            "is_open_interclub",
        ]

    def __init__(self, *args, club: Club | None = None, is_pro: bool = False, **kwargs):
        self.club = club
        self.is_pro = is_pro
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields["allowed_categories"].initial = list(
                self.instance.allowed_categories.values_list("category", flat=True)
            )

        self.fields["city"].initial = club.city if club else self.fields["city"].initial
        self.fields["sport"].initial = Sport.TENNIS
        self.fields["sport"].help_text = (
            "Падел проводится только в парах. Рейтинг падела не смешивается с теннисом."
        )
        self.fields["format"].initial = TournamentFormat.WEEKEND_DAY
        self.fields["variant"].initial = TournamentVariant.SINGLES
        self.fields["tournament_type"].initial = TournamentType.REGULAR
        self.fields["match_days_per_round"].initial = 7
        self.fields["entry_fee"].initial = 0

        self._apply_geo_defaults_from_club()

        self.fields["description"].required = False
        self.fields["regulation_extra"].required = False
        self.fields["image"].required = False
        self.fields["slug"].required = False
        self.fields["region"].required = False
        self.fields["geo_area"].required = False
        self.fields["court"].required = False
        self.fields["registration_deadline"].required = False
        self.fields["end_date"].required = False
        self.fields["match_format"].required = False
        self.fields["min_participants"].required = False
        self.fields["max_participants"].required = False
        self.fields["min_teams"].required = False
        self.fields["max_teams"].required = False
        self.fields["entry_fee"].required = False
        self.fields["start_after_fill"].required = False

        start_after = False
        if self.is_bound:
            start_after = self.data.get("start_after_fill") in (True, "on", "True", "1")
        elif self.instance and getattr(self.instance, "start_after_fill", False):
            start_after = True
        self.fields["start_date"].required = not start_after
        if start_after:
            self.fields["start_date"].required = False
            self.fields["end_date"].required = False
            self.fields["registration_deadline"].required = False

        self.fields["start_after_fill"].help_text = (
            "Без дат старта и дедлайна: при минимуме — письмо админам клуба, "
            "при максимуме — автозапуск и сетка."
        )

        self.fields["description"].widget = forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "Описание турнира, покрытие, правила, регистрация, расписание и важные детали",
            }
        )
        self.fields["regulation_extra"].widget = forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Особые правила, мячи, судья, контакты на площадке — попадут в скачиваемый регламент",
            }
        )
        self.fields["regulation_extra"].help_text = (
            "Необязательно. Отдельный раздел скачиваемого регламента."
        )
        # type=date / datetime-local требуют ISO-формат; при ru-локали без
        # format значения не отображаются и POST не проходит валидацию.
        self.fields["start_date"].widget = forms.DateInput(
            attrs={"type": "date"},
            format="%Y-%m-%d",
        )
        self.fields["start_date"].input_formats = ["%Y-%m-%d", "%d.%m.%Y"]
        self.fields["end_date"].widget = forms.DateInput(
            attrs={"type": "date"},
            format="%Y-%m-%d",
        )
        self.fields["end_date"].input_formats = ["%Y-%m-%d", "%d.%m.%Y"]
        self.fields["registration_deadline"].widget = forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        )
        self.fields["registration_deadline"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
        ]
        self.fields["postpayment_deadline_hours"].required = False
        self.fields["postpayment_deadline_hours"].help_text = (
            "Сколько часов на оплату после открытия окна постоплаты (по умолчанию 12)."
        )
        self.fields["start_date"].help_text = (
            "Чтобы продлить регистрацию, сдвиньте и дату начала: "
            "дедлайн не может быть позже старта турнира."
        )
        court_qs = active_courts()
        if self.instance and self.instance.court_id:
            court_qs = Court.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.court_id)
            ).order_by("city", "name")
        search_url = ""
        if club is not None:
            search_url = reverse(
                "clubs:tournament_court_search",
                kwargs={"slug": club.slug},
            )
        self.fields["court"].queryset = court_qs
        self.fields["court"].widget = CourtSearchWidget(
            search_url=search_url,
            attrs=self.fields["court"].widget.attrs,
        )
        self.fields["court"].help_text = (
            "Начните вводить название или город. Список учитывает вид спорта: "
            "теннисный корт нельзя выбрать для падела."
        )
        self.fields["geo_area"].empty_label = "Не выбрано"
        self.fields["city"].widget.attrs["data-geo-city"] = "1"
        self.fields["region"].widget.attrs["data-geo-region"] = "1"
        self.fields["geo_area"].widget.attrs["data-geo-area"] = "1"
        self._configure_geo_area_queryset()
        self.fields["match_format"].choices = [("", "Выберите формат матча")] + list(
            MatchFormat.choices
        )
        self.fields["image"].help_text = "Афиша или обложка турнира, до 2 МБ."
        self.fields["slug"].help_text = (
            "Можно оставить пустым: при совпадении система сама добавит уникальный суффикс."
        )
        self.fields["registration_deadline"].help_text = (
            "Если поле пустое, регистрация будет открыта до старта турнира."
        )
        self.fields["region"].help_text = (
            "Только для Москвы и области: от выбора зависит список районов "
            "и городов. Для Санкт-Петербурга и других городов России поле скрыто."
        )
        self.fields["geo_area"].help_text = (
            "Район Москвы или город области — для рекламных страниц. "
            "Для остальных населённых пунктов достаточно поля «Населённый пункт»."
        )
        self.fields["is_open_interclub"].disabled = not is_pro
        if not is_pro:
            self.fields["is_open_interclub"].help_text = (
                "Межклубный режим доступен только на соответствующем тарифе платформы."
            )

        for _, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxSelectMultiple):
                widget.attrs["class"] = "club-tournament-create__checkboxes"
            elif isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "club-tournament-create__toggle"
            elif isinstance(widget, forms.ClearableFileInput):
                widget.attrs["class"] = "club-tournament-create__file"
            else:
                existing = widget.attrs.get("class", "")
                widget.attrs["class"] = (existing + " form-control").strip()

    def geo_areas_payload(self) -> list[dict[str, str | int | list[str]]]:
        """Данные активных площадок для каскада и скрытия московских полей.

        Returns:
            list[dict[str, str | int | list[str]]]: id, регион, название, псевдонимы.
        """
        return geo_areas_client_payload()

    def shows_moscow_geo(self) -> bool:
        """Нужно ли сразу показать регион и район на форме.

        Returns:
            bool: False, если населённый пункт вне Москвы и области.
        """
        city = ""
        if self.is_bound:
            city = str(self.data.get("city") or "")
        elif self.instance and self.instance.pk:
            city = self.instance.city or ""
        else:
            city = str(
                self.initial.get("city")
                or self.fields["city"].initial
                or (self.club.city if self.club else "")
                or ""
            )
        return should_show_moscow_geo_fields(city)

    def _selected_region(self) -> str:
        """Текущий регион из POST, инстанса или initial.

        Returns:
            str: Значение ``region`` либо пустая строка.
        """
        if self.is_bound:
            return str(self.data.get("region") or "").strip()
        if self.instance and self.instance.pk and self.instance.region:
            return str(self.instance.region)
        initial_region = self.initial.get("region") or self.fields["region"].initial
        return str(initial_region or "").strip()

    def _configure_geo_area_queryset(self) -> None:
        """Ограничить список площадок выбранным регионом."""
        queryset = GeoArea.objects.filter(is_active=True)
        region = self._selected_region()
        if region:
            queryset = queryset.filter(region=region)
        self.fields["geo_area"].queryset = queryset.order_by("sort_order", "name")

    def _apply_geo_defaults_from_club(self) -> None:
        """Подставить регион и площадку по городу клуба, если ещё не заданы."""
        if self.is_bound or not self.club:
            return
        if self.instance and self.instance.pk:
            return

        city = (self.club.city or "").strip()
        if not city:
            return

        if is_moscow_city_name(city):
            if not self.fields["region"].initial and "region" not in self.initial:
                self.fields["region"].initial = GeoRegion.MOSCOW
            return

        area = oblast_geo_area_for_city(city)
        if area is None:
            return
        if not self.fields["region"].initial and "region" not in self.initial:
            self.fields["region"].initial = area.region
        if not self.fields["geo_area"].initial and "geo_area" not in self.initial:
            self.fields["geo_area"].initial = area.pk

    def clean_allowed_categories(self):
        value = self.cleaned_data.get("allowed_categories") or []
        if len(value) == 0:
            raise ValidationError("Выберите хотя бы одну категорию участников.")
        if len(value) > 5:
            raise ValidationError("Можно выбрать не более 5 категорий.")
        return value

    def lock_structure_fields(self) -> None:
        """Блокирует поля, которые меняют структуру уже запущенного турнира."""
        for field_name in (
            "slug",
            "sport",
            "format",
            "variant",
            "gender",
            "allowed_categories",
            "tournament_type",
            "min_participants",
            "max_participants",
            "min_teams",
            "max_teams",
            "start_after_fill",
            "match_days_per_round",
            "match_format",
            "fan_points_r1",
            "fan_points_r2",
            "fan_points_sf",
            "fan_points_final",
            "fan_points_winner",
            "is_open_interclub",
            "allow_postpayment",
        ):
            if field_name in self.fields:
                self.fields[field_name].disabled = True
                self.fields[field_name].help_text = (
                    "Поле заблокировано после формирования сетки/групп."
                )

    def clean_slug(self):
        raw_slug = (self.cleaned_data.get("slug") or "").strip()
        if not raw_slug:
            raw_slug = slugify(self.cleaned_data.get("name") or "")
        if not raw_slug and self.club:
            club_slug = slugify(self.club.slug) or "club"
            raw_slug = f"{club_slug}-tournament"
        return generate_unique_tournament_slug(
            name=self.cleaned_data.get("name") or "",
            slug=raw_slug or None,
            instance=self.instance,
        )

    def clean_registration_deadline(self):
        value = self.cleaned_data.get("registration_deadline")
        if value and timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def clean(self):
        cleaned_data = super().clean()

        sport = sport_code(cleaned_data.get("sport") or Sport.TENNIS)
        cleaned_data["sport"] = sport
        if sport == Sport.PADEL:
            cleaned_data["variant"] = TournamentVariant.DOUBLES
            self.errors.pop("variant", None)

        variant = cleaned_data.get("variant")
        court = cleaned_data.get("court")
        if court is not None and not _court_matches_sport(court, sport):
            if sport == Sport.PADEL:
                self.add_error(
                    "court",
                    "Для падела выберите падел-корт или площадку «теннис и падел».",
                )
            else:
                self.add_error(
                    "court",
                    "Для тенниса выберите теннисный корт или площадку «теннис и падел».",
                )
        gender = cleaned_data.get("gender")
        is_one_day = cleaned_data.get("is_one_day")
        allow_postpayment = bool(cleaned_data.get("allow_postpayment"))
        entry_fee = cleaned_data.get("entry_fee")
        tournament_format = cleaned_data.get("format")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        registration_deadline = cleaned_data.get("registration_deadline")

        if variant == TournamentVariant.SINGLES and gender == "mixed":
            raise ValidationError(
                "Категория «Микст» доступна только для парных турниров."
            )

        start_after_fill = bool(cleaned_data.get("start_after_fill"))
        bracket_generated = bool(
            getattr(self.instance, "bracket_generated", False)
            if self.instance and self.instance.pk
            else False
        )
        if start_after_fill:
            cleaned_data["registration_deadline"] = None
            if not bracket_generated:
                cleaned_data["start_date"] = None
                cleaned_data["end_date"] = None
            else:
                if not cleaned_data.get("start_date") and self.instance.start_date:
                    cleaned_data["start_date"] = self.instance.start_date
                if not cleaned_data.get("end_date") and self.instance.end_date:
                    cleaned_data["end_date"] = self.instance.end_date
            start_date = cleaned_data.get("start_date")
            end_date = cleaned_data.get("end_date")
            registration_deadline = None
        else:
            if not start_date:
                self.add_error("start_date", "Укажите дату начала турнира.")

        if start_date and end_date and end_date < start_date:
            self.add_error(
                "end_date", "Дата окончания не может быть раньше даты начала."
            )

        if registration_deadline and start_date:
            deadline_date = timezone.localtime(registration_deadline).date()
            if deadline_date > start_date:
                self.add_error(
                    "registration_deadline",
                    "Дедлайн регистрации не может быть позже даты начала турнира.",
                )

        if is_one_day is False and (entry_fee is None or float(entry_fee or 0) <= 0):
            raise ValidationError(
                "Для многодневного турнира укажите вступительный взнос больше 0 ₽."
            )
        if allow_postpayment:
            if is_one_day:
                self.add_error(
                    "allow_postpayment",
                    "Постоплата недоступна для однодневных турниров.",
                )
            if tournament_format == TournamentFormat.WEEKEND_DAY:
                self.add_error(
                    "allow_postpayment",
                    "Постоплата недоступна для формата ТВД.",
                )
            if float(entry_fee or 0) <= 0:
                self.add_error(
                    "allow_postpayment",
                    "Постоплата доступна только при вступительном взносе больше 0 ₽.",
                )

        hours = cleaned_data.get("postpayment_deadline_hours")
        if hours is not None and int(hours) < 1:
            self.add_error(
                "postpayment_deadline_hours",
                "Длительность окна постоплаты должна быть не меньше 1 часа.",
            )

        if variant == TournamentVariant.DOUBLES:
            cleaned_data["min_participants"] = None
            cleaned_data["max_participants"] = None
            min_teams = cleaned_data.get("min_teams")
            max_teams = cleaned_data.get("max_teams")
            if not cleaned_data.get("max_teams"):
                self.add_error(
                    "max_teams",
                    "Для парного турнира укажите максимальное количество команд.",
                )
            if min_teams and max_teams and min_teams > max_teams:
                self.add_error(
                    "min_teams",
                    "Минимум команд не может быть больше максимума.",
                )
        else:
            cleaned_data["min_teams"] = None
            cleaned_data["max_teams"] = None
            min_participants = cleaned_data.get("min_participants")
            max_participants = cleaned_data.get("max_participants")
            if start_after_fill and not max_participants:
                self.add_error(
                    "max_participants",
                    "При «Старте после набора» укажите максимальное "
                    "количество участников — по нему турнир запустится автоматически.",
                )
            if (
                min_participants
                and max_participants
                and min_participants > max_participants
            ):
                self.add_error(
                    "min_participants",
                    "Минимум участников не может быть больше максимума.",
                )
        if start_after_fill and variant == TournamentVariant.DOUBLES:
            if not cleaned_data.get("max_teams"):
                self.add_error(
                    "max_teams",
                    "При «Старте после набора» укажите максимальное "
                    "количество команд — по нему турнир запустится автоматически.",
                )

        if cleaned_data.get("is_open_interclub") and not self.is_pro:
            cleaned_data["is_open_interclub"] = False

        city = (cleaned_data.get("city") or "").strip()
        region = (cleaned_data.get("region") or "").strip()
        geo_area = cleaned_data.get("geo_area")
        if city and not city_uses_moscow_geo(city):
            cleaned_data["region"] = ""
            cleaned_data["geo_area"] = None
        elif geo_area is not None:
            if region and geo_area.region != region:
                self.add_error(
                    "geo_area",
                    (
                        f"«{geo_area.name}» относится к региону "
                        f"«{geo_area.get_region_display()}», а не к выбранному."
                    ),
                )
            elif not region:
                cleaned_data["region"] = geo_area.region

        return cleaned_data


class ClubNotificationSettingsForm(forms.ModelForm):
    """Настройки уведомлений участника клуба (ЛК игрока)."""

    def __init__(self, *args, user: Any | None = None, **kwargs) -> None:
        """Сохраняет пользователя формы для проверки Telegram-бота."""
        self._user = user
        super().__init__(*args, **kwargs)

    def clean_telegram_enabled(self) -> bool:
        """Запрещает включать Telegram без привязанного пользовательского бота."""
        from apps.telegram_bot.telegram_http import is_telegram_api_enabled

        telegram_enabled = bool(self.cleaned_data.get("telegram_enabled"))
        if not telegram_enabled:
            return False

        if not is_telegram_api_enabled():
            raise ValidationError(
                "Подключение Telegram временно недоступно на стороне сайта."
            )

        user = self._user or getattr(self.instance, "user", None)
        if user is None:
            return telegram_enabled

        is_connected = (
            UserTelegramLink.objects.filter(
                user=user,
                user_bot_chat_id__isnull=False,
            )
            .exclude(user_bot_chat_id=0)
            .exists()
        )
        if not is_connected:
            raise ValidationError(
                "Сначала подключите Telegram-бота в профиле, затем включите этот канал."
            )
        return True

    class Meta:
        model = ClubNotificationSettings
        fields = ["is_enabled", "email_enabled", "telegram_enabled"]


class ClubNotificationConfigForm(forms.ModelForm):
    """Глобальные настройки уведомлений клуба (панель админа)."""

    class Meta:
        model = ClubNotificationConfig
        fields = [
            "notify_by_email",
            "notify_by_telegram",
            "fee_reminders_enabled",
            "fee_overdue_enabled",
            "fee_paid_enabled",
            "subscription_expiring_enabled",
            "tournament_reminders_enabled",
            "new_member_enabled",
            "debtors_summary_enabled",
        ]


class ClubPlayerPlanForm(forms.ModelForm):
    """Форма создания/редактирования клубного тарифа игроков."""

    class Meta:
        model = ClubPlayerPlan
        fields = [
            "name",
            "description",
            "is_active",
            "monthly_fee",
            "duration_days",
            "registration_limit_period",
            "has_unlimited_registrations",
            "max_tournaments_per_month",
            "allow_self_change",
            "sort_order",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "registration_limit_period": (
                "Определяет, когда лимит регистраций обновляется: ежемесячно или один раз на весь срок тарифа."
            ),
            "max_tournaments_per_month": (
                "Оставьте пустым для безлимитного участия. "
                "Однодневные турниры не расходуют лимит."
            ),
        }

    def __init__(self, *args, **kwargs):
        """Добавляет единое клубное оформление полям тарифа."""
        super().__init__(*args, **kwargs)
        for field_name in [
            "name",
            "description",
            "monthly_fee",
            "duration_days",
            "registration_limit_period",
            "max_tournaments_per_month",
            "sort_order",
        ]:
            self.fields[field_name].widget.attrs[
                "class"
            ] = "form-control club-payments-form__control"
        for field_name in [
            "is_active",
            "allow_self_change",
            "has_unlimited_registrations",
        ]:
            self.fields[field_name].widget.attrs["class"] = "form-checkbox"
        self.fields["name"].widget.attrs.setdefault("placeholder", "Например, Базовый")
        self.fields["description"].widget.attrs.setdefault(
            "placeholder",
            "Кратко опишите, что входит в тариф и кому он подходит.",
        )
        self.fields["monthly_fee"].widget.attrs.setdefault("placeholder", "1000")
        self.fields["duration_days"].widget.attrs.setdefault("placeholder", "30")
        self.fields["registration_limit_period"].initial = (
            self.instance.registration_limit_period
            if self.instance and self.instance.pk
            else ClubRegistrationLimitPeriod.MONTHLY
        )
        self.fields["max_tournaments_per_month"].widget.attrs.setdefault(
            "placeholder",
            "Например, 5",
        )
        self.fields["sort_order"].widget.attrs.setdefault("placeholder", "0")
        self.fields["duration_days"].widget.attrs.setdefault("min", 1)

    def clean(self) -> dict[str, Any]:
        """Проверяет согласованность лимита и флага безлимитных регистраций."""
        cleaned_data = cast(dict[str, Any], super().clean())
        has_unlimited_registrations = bool(
            cleaned_data.get("has_unlimited_registrations")
        )
        max_tournaments_per_month = cleaned_data.get("max_tournaments_per_month")
        registration_limit_period = cleaned_data.get("registration_limit_period")
        if has_unlimited_registrations:
            cleaned_data["max_tournaments_per_month"] = None
            cleaned_data["registration_limit_period"] = (
                registration_limit_period or ClubRegistrationLimitPeriod.MONTHLY
            )
        elif max_tournaments_per_month is None:
            self.add_error(
                "max_tournaments_per_month",
                "Укажите лимит турниров или включите безлимитные регистрации.",
            )
        return cleaned_data


class ClubMemberPlanSelectForm(forms.Form):
    """Форма выбора тарифа участником клуба."""

    plan_id = forms.ModelChoiceField(
        queryset=ClubPlayerPlan.objects.none(),
        empty_label=None,
        label="Тариф",
    )

    def __init__(self, *args, club: Club | None = None, **kwargs):
        """Инициализирует форму выбора тарифов клуба.

        Args:
            club: Клуб, чьи активные тарифы доступны для выбора.
        """
        super().__init__(*args, **kwargs)
        if club is not None:
            self.fields["plan_id"].queryset = ClubPlayerPlan.objects.filter(
                club=club,
                is_active=True,
            ).order_by("sort_order", "name")


class ClubMemberPlanAssignForm(forms.Form):
    """Форма назначения тарифа участнику администратором клуба."""

    member = forms.ModelChoiceField(
        queryset=ClubMember.objects.none(),
        label="Участник клуба",
    )
    plan = forms.ModelChoiceField(
        queryset=ClubPlayerPlan.objects.none(),
        label="Тариф",
    )
    reason = forms.CharField(
        label="Причина",
        required=False,
        max_length=255,
    )

    def __init__(self, *args, club: Club | None = None, **kwargs):
        """Инициализирует выбор участников и тарифов конкретного клуба.

        Args:
            club: Клуб, в рамках которого назначается тариф.
        """
        super().__init__(*args, **kwargs)
        self.fields["member"].widget.attrs[
            "class"
        ] = "form-control club-payments-form__control"
        self.fields["plan"].widget.attrs[
            "class"
        ] = "form-control club-payments-form__control"
        self.fields["reason"].widget.attrs.update(
            {
                "class": "form-control club-payments-form__control",
                "placeholder": "Например, приветственный тариф или ручная корректировка",
            }
        )
        if club is not None:
            self.fields["member"].queryset = (
                ClubMember.objects.filter(club=club, status=ClubMemberStatus.ACTIVE)
                .select_related("user")
                .order_by("user__email")
            )
            self.fields["plan"].queryset = ClubPlayerPlan.objects.filter(
                club=club,
                is_active=True,
            ).order_by("sort_order", "name")
