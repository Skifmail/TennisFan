"""
Формы клубного раздела: регистрация клуба, инвайты, приглашения, панель клуба.
"""

from django import forms
from django.utils.text import slugify

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
            attrs={"placeholder": "Например: Теннисный клуб «Спартак»"}
        ),
    )
    city = forms.CharField(
        label="Город",
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "Москва"}),
    )
    address = forms.CharField(
        label="Адрес",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Улица, дом, корпус"}),
    )
    logo = forms.ImageField(
        label="Логотип клуба",
        required=False,
        help_text="JPG, PNG или WebP до 5 МБ",
    )
    email = forms.EmailField(label="Контактный email")
    phone = forms.CharField(
        label="Контактный телефон",
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "+7 (999) 123-45-67"}),
    )
    admin_name = forms.CharField(
        label="ФИО ответственного / администратора",
        max_length=255,
        min_length=2,
    )
    description = forms.CharField(
        label="Краткое описание клуба",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
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
            "email",
            "phone",
            "admin_name",
            "description",
            "is_public",
            "logo",
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


class ClubTournamentCreateForm(forms.Form):
    """Создание турнира клуба (минимальный набор полей)."""

    name = forms.CharField(label="Название", max_length=200)
    slug = forms.SlugField(label="URL", max_length=100, required=False)
    city = forms.CharField(label="Город", max_length=100)
    start_date = forms.DateField(label="Дата начала")
    end_date = forms.DateField(label="Дата окончания", required=False)
    format = forms.ChoiceField(
        label="Формат",
        choices=[
            ("single_elimination", "Олимпийский (до 1 поражения)"),
            ("olympic_consolation", "Олимпийский (за все места)"),
            ("round_robin", "Круговой"),
            ("weekend_day", "Однодневный турнир"),
        ],
    )
    variant = forms.ChoiceField(
        label="Вариант",
        choices=[("singles", "Одиночный"), ("doubles", "Парный")],
    )
    gender = forms.ChoiceField(
        label="Категория по полу",
        choices=[
            ("male", "Мужчины"),
            ("female", "Женщины"),
            ("open", "Смешанный"),
            ("mixed", "Микст"),
        ],
    )
    min_participants = forms.IntegerField(
        label="Мин. участников",
        required=False,
        min_value=2,
    )
    max_participants = forms.IntegerField(
        label="Макс. участников",
        required=False,
        min_value=2,
    )
    is_open_interclub = forms.BooleanField(
        label="Открытый межклубный турнир",
        required=False,
        help_text="Другие клубы смогут подать заявку на участие.",
    )

    def clean_slug(self):
        slug = self.cleaned_data.get("slug") or ""
        if not slug and self.cleaned_data.get("name"):
            from django.utils.text import slugify

            slug = slugify(self.cleaned_data["name"]) or "tournament"
        return (slug or "tournament")[:100]


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
            "monthly_slots",
            "allow_rollover_slots",
            "rollover_cap",
            "allow_self_change",
            "sort_order",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        """Валидация зависимостей rollover-полей тарифа."""
        cleaned_data = super().clean()
        allow_rollover = bool(cleaned_data.get("allow_rollover_slots"))
        rollover_cap = int(cleaned_data.get("rollover_cap") or 0)
        if not allow_rollover and rollover_cap > 0:
            self.add_error(
                "rollover_cap",
                "Лимит переноса должен быть 0, если перенос слотов отключен.",
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
