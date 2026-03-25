"""
Формы клубного раздела: регистрация клуба, инвайты, приглашения, панель клуба.
"""

from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import UserTelegramLink
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
    ClubLegalDocument,
    ClubMember,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
    ClubPlayerPlan,
)
from .payment_utils import get_secret_mask


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
        self.fields["slug"].required = False
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

    def lock_structure_fields(self) -> None:
        """Блокирует поля, которые меняют структуру уже запущенного турнира."""
        for field_name in (
            "slug",
            "format",
            "variant",
            "gender",
            "allowed_categories",
            "tournament_type",
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

    def __init__(self, *args, user: Any | None = None, **kwargs) -> None:
        """Сохраняет пользователя формы для проверки Telegram-бота."""
        self._user = user
        super().__init__(*args, **kwargs)

    def clean_telegram_enabled(self) -> bool:
        """Запрещает включать Telegram без привязанного пользовательского бота."""
        telegram_enabled = bool(self.cleaned_data.get("telegram_enabled"))
        if not telegram_enabled:
            return False

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
            "has_unlimited_registrations",
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

    def __init__(self, *args, **kwargs):
        """Добавляет единое клубное оформление полям тарифа."""
        super().__init__(*args, **kwargs)
        for field_name in [
            "name",
            "description",
            "monthly_fee",
            "duration_days",
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
        if has_unlimited_registrations:
            cleaned_data["max_tournaments_per_month"] = None
        elif max_tournaments_per_month is None:
            self.add_error(
                "max_tournaments_per_month",
                "Укажите лимит турниров в месяц или включите безлимитные регистрации.",
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
