"""
Формы клубного раздела: регистрация клуба, инвайты, приглашения, панель клуба.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify

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

from .models import (
    Club,
    ClubMember,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
    ClubPlayerPlan,
)
from .payment_utils import get_secret_mask


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
        label="Город",
        max_length=100,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Москва"}
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
        widget=forms.NumberInput(attrs={"placeholder": "Пусто — без срока"}),
    )
    max_uses = forms.IntegerField(
        label="Лимит использований",
        required=False,
        min_value=1,
        max_value=10000,
        widget=forms.NumberInput(attrs={"placeholder": "Пусто — без лимита"}),
    )


class InviteByEmailForm(forms.Form):
    """Приглашение игрока по email."""

    email = forms.EmailField(label="Email пользователя")


class ClubProfileEditForm(forms.ModelForm):
    """Редактирование профиля клуба (название, контакты, описание, публичность)."""

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
    """Настройка взноса клуба (сумма, период, провайдер, реквизиты и блокировки)."""

    payment_provider = forms.ChoiceField(
        label="Платёжный провайдер",
        required=False,
        choices=[("", "— не подключён —")]
        + list(ClubMembershipFee.PaymentProvider.choices),
    )
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
            "amount",
            "currency",
            "period",
            "period_start_day",
            "description",
            "restrict_tournament_access",
            "is_active",
            "payment_provider",
            "payment_shop_id",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        """Инициализация формы с учётом уже сохранённого секрета."""
        super().__init__(*args, **kwargs)
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
        provider = cleaned_data.get("payment_provider") or ""
        shop_id = (cleaned_data.get("payment_shop_id") or "").strip()
        is_active = bool(cleaned_data.get("is_active"))
        new_secret = (cleaned_data.get("new_secret_key") or "").strip()

        # При активированных взносах и выбранном провайдере — shop_id обязателен.
        if is_active and provider:
            if not shop_id:
                self.add_error(
                    "payment_shop_id", "Укажите ID магазина для выбранного провайдера."
                )

            # Если это первое сохранение секрета — требуем его.
            fee: ClubMembershipFee | None = (
                self.instance if isinstance(self.instance, ClubMembershipFee) else None
            )
            has_existing_secret = bool(fee and fee.payment_api_key)
            if not has_existing_secret and not new_secret:
                self.add_error(
                    "new_secret_key", "Укажите Secret Key для подключения ЮKassa."
                )

        # При выборе Stripe пока показываем заглушку.
        if provider == ClubMembershipFee.PaymentProvider.STRIPE:
            self.add_error(
                "payment_provider",
                "Stripe будет доступен в следующих версиях. Сейчас поддерживается только ЮKassa.",
            )

        return cleaned_data


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
            "image",
            "format",
            "variant",
            "entry_fee",
            "is_one_day",
            "city",
            "court",
            "gender",
            "allowed_categories",
            "tournament_type",
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
        self.fields["format"].initial = TournamentFormat.WEEKEND_DAY
        self.fields["variant"].initial = TournamentVariant.SINGLES
        self.fields["tournament_type"].initial = TournamentType.REGULAR
        self.fields["match_days_per_round"].initial = 7
        self.fields["entry_fee"].initial = 0

        self.fields["description"].required = False
        self.fields["image"].required = False
        self.fields["court"].required = False
        self.fields["registration_deadline"].required = False
        self.fields["end_date"].required = False
        self.fields["match_format"].required = False
        self.fields["min_participants"].required = False
        self.fields["max_participants"].required = False
        self.fields["min_teams"].required = False
        self.fields["max_teams"].required = False
        self.fields["entry_fee"].required = False

        self.fields["description"].widget = forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "Описание турнира, покрытие, правила, регистрация, расписание и важные детали",
            }
        )
        self.fields["start_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["end_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["registration_deadline"].widget = forms.DateTimeInput(
            attrs={"type": "datetime-local"}
        )
        self.fields["court"].queryset = (
            Court.objects.filter(city=club.city).order_by("name")
            if club
            else Court.objects.all().order_by("city", "name")
        )
        self.fields["court"].empty_label = "Без привязки к корту"
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

    def clean_allowed_categories(self):
        value = self.cleaned_data.get("allowed_categories") or []
        if len(value) == 0:
            raise ValidationError("Выберите хотя бы одну категорию участников.")
        if len(value) > 5:
            raise ValidationError("Можно выбрать не более 5 категорий.")
        return value

    def clean_slug(self):
        return generate_unique_tournament_slug(
            name=self.cleaned_data.get("name") or "",
            slug=self.cleaned_data.get("slug") or None,
            instance=self.instance,
        )

    def clean_registration_deadline(self):
        value = self.cleaned_data.get("registration_deadline")
        if value and timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def clean(self):
        cleaned_data = super().clean()

        variant = cleaned_data.get("variant")
        gender = cleaned_data.get("gender")
        is_one_day = cleaned_data.get("is_one_day")
        entry_fee = cleaned_data.get("entry_fee")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        registration_deadline = cleaned_data.get("registration_deadline")

        if variant == TournamentVariant.SINGLES and gender == "mixed":
            raise ValidationError(
                "Категория «Микст» доступна только для парных турниров."
            )

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
            if (
                min_participants
                and max_participants
                and min_participants > max_participants
            ):
                self.add_error(
                    "min_participants",
                    "Минимум участников не может быть больше максимума.",
                )

        if cleaned_data.get("is_open_interclub") and not self.is_pro:
            cleaned_data["is_open_interclub"] = False

        return cleaned_data


class ClubNotificationSettingsForm(forms.ModelForm):
    """Настройки уведомлений участника клуба (ЛК игрока)."""

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
            "max_tournaments_per_month",
            "allow_self_change",
            "sort_order",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "max_tournaments_per_month": (
                "Оставьте пустым для безлимитного участия. "
                "Однодневные турниры не расходуют лимит."
            ),
        }


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
