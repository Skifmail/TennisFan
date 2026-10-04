"""
Training forms.
"""

from decimal import Decimal
from typing import Any

from django import forms

from apps.core.contact_utils import normalize_max_contact
from apps.core.sports import (
    Sport,
    sport_codes_from_venue,
    venue_sport_from_codes,
)
from apps.courts.models import Court
from apps.training.geo import advertised_training_courts
from apps.users.models import SkillLevel

from .models import Coach, CoachApplication, Training, TrainingEnrollment
from .widgets import MultiCheckboxWidget, SportCheckboxSelectMultiple, TypePricesWidget


class TrainingEnrollmentForm(forms.ModelForm):
    """Упрощённая заявка: имя, один контакт, корт, согласие."""

    CONTACT_TELEGRAM = "telegram"
    CONTACT_WHATSAPP = "whatsapp"
    CONTACT_EMAIL = "email"
    CONTACT_CHOICES = (
        (CONTACT_TELEGRAM, "Telegram"),
        (CONTACT_WHATSAPP, "WhatsApp / телефон"),
        (CONTACT_EMAIL, "Email"),
    )

    contact_method = forms.ChoiceField(
        choices=CONTACT_CHOICES,
        label="Способ связи",
        initial=CONTACT_TELEGRAM,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    contact_value = forms.CharField(
        label="Контакт",
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "@username, телефон или email",
            }
        ),
    )
    agree_legal = forms.BooleanField(
        required=True,
        label="",
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox"}),
    )

    class Meta:
        model = TrainingEnrollment
        fields = ("full_name", "desired_court", "message")
        widgets = {
            "full_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Имя и фамилия"}
            ),
            "desired_court": forms.Select(attrs={"class": "form-control"}),
            "message": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 2,
                    "placeholder": "Пожелания (необязательно)",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["full_name"].required = True
        self.fields["desired_court"].queryset = advertised_training_courts()
        self.fields["desired_court"].required = False
        self.fields["desired_court"].empty_label = (
            "—— Населённый пункт / корт (необязательно) ——"
        )
        self.fields["desired_court"].label_from_instance = (
            lambda court: f"{court.city} — {court.name}"
        )
        self.fields["message"].required = False
        # Подставляем контакт из instance при редактировании (на будущее).
        if self.instance and self.instance.pk and not self.data:
            if self.instance.telegram:
                self.fields["contact_method"].initial = self.CONTACT_TELEGRAM
                self.fields["contact_value"].initial = self.instance.telegram
            elif self.instance.whatsapp:
                self.fields["contact_method"].initial = self.CONTACT_WHATSAPP
                self.fields["contact_value"].initial = self.instance.whatsapp
            elif self.instance.email:
                self.fields["contact_method"].initial = self.CONTACT_EMAIL
                self.fields["contact_value"].initial = self.instance.email

    def clean_agree_legal(self):
        if not self.cleaned_data.get("agree_legal"):
            raise forms.ValidationError(
                "Необходимо согласие на обработку персональных данных."
            )
        return True

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("contact_method")
        value = (cleaned.get("contact_value") or "").strip()
        if not value:
            self.add_error("contact_value", "Укажите контакт для связи.")
            return cleaned
        cleaned["telegram"] = ""
        cleaned["whatsapp"] = ""
        cleaned["email"] = ""
        if method == self.CONTACT_TELEGRAM:
            cleaned["telegram"] = value
        elif method == self.CONTACT_WHATSAPP:
            cleaned["whatsapp"] = value
        elif method == self.CONTACT_EMAIL:
            if "@" not in value:
                self.add_error("contact_value", "Введите корректный email.")
            else:
                cleaned["email"] = value
        else:
            self.add_error("contact_method", "Выберите способ связи.")
        return cleaned

    def save(self, commit: bool = True):
        enrollment = super().save(commit=False)
        enrollment.telegram = self.cleaned_data.get("telegram", "")
        enrollment.whatsapp = self.cleaned_data.get("whatsapp", "")
        enrollment.email = self.cleaned_data.get("email", "") or ""
        if commit:
            enrollment.save()
        return enrollment


class VenueSportCheckboxesMixin:
    """Чекбоксы теннис/падел, в модели одно поле ``sport``."""

    fields: dict[str, Any]
    initial: dict[str, Any]
    is_bound: bool
    cleaned_data: dict[str, Any]

    def _add_sports_field(self, *, initial_sport: str | None = None) -> None:
        """Добавить поле ``sports`` и подставить текущий вид спорта.

        Args:
            initial_sport: Явный код ``VenueSport``, иначе берётся из instance.
        """
        instance = getattr(self, "instance", None)
        current = initial_sport
        if current is None and instance is not None:
            current = str(getattr(instance, "sport", "") or "")
        codes = sport_codes_from_venue(current)
        self.fields["sports"] = forms.MultipleChoiceField(
            label="Вид спорта",
            choices=Sport.choices,
            required=True,
            widget=SportCheckboxSelectMultiple(),
            help_text="Можно выбрать теннис, падел или оба.",
        )
        if not getattr(self, "is_bound", False):
            self.initial.setdefault("sports", codes)
        self.fields["sports"].initial = self.initial.get("sports", codes)

    def clean_sports(self) -> str:
        """Собрать ``tennis`` / ``padel`` / ``both`` из чекбоксов."""
        codes = self.cleaned_data.get("sports") or []
        if not codes:
            raise forms.ValidationError("Выберите теннис, падел или оба вида спорта.")
        return venue_sport_from_codes(list(codes))

    def _apply_sports(self, obj: Coach | CoachApplication | Training) -> None:
        """Записать выбранный вид спорта в модель."""
        obj.sport = self.cleaned_data["sports"]


class CoachApplicationForm(VenueSportCheckboxesMixin, forms.ModelForm):
    """Форма заявки «Стать тренером». Поля как у тренера в админке."""

    class Meta:
        model = CoachApplication
        fields = (
            "applicant_name",
            "applicant_email",
            "applicant_phone",
            "name",
            "photo",
            "bio",
            "experience_years",
            "specialization",
            "phone",
            "telegram",
            "whatsapp",
            "max_contact",
            "city",
        )
        widgets = {
            "applicant_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "ФИО"}
            ),
            "applicant_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "email@example.com"}
            ),
            "applicant_phone": forms.TextInput(
                attrs={
                    "class": "form-control js-phone-input",
                    "placeholder": "+7",
                    "autocomplete": "tel",
                }
            ),
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Как вас представлять"}
            ),
            "photo": forms.FileInput(
                attrs={"class": "form-control", "accept": "image/*"}
            ),
            "bio": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Опыт, достижения, подход к тренировкам",
                }
            ),
            "experience_years": forms.NumberInput(
                attrs={"class": "form-control", "min": 0, "max": 50}
            ),
            "specialization": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Например: взрослые, дети, мини-теннис",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "form-control js-phone-input",
                    "placeholder": "+7",
                    "autocomplete": "tel",
                }
            ),
            "telegram": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "@username"}
            ),
            "whatsapp": forms.TextInput(
                attrs={
                    "class": "form-control js-phone-input",
                    "placeholder": "+7",
                    "autocomplete": "tel",
                }
            ),
            "max_contact": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ссылка на профиль MAX или номер телефона",
                }
            ),
            "city": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Населённый пункт"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["applicant_phone"].required = False
        self.fields["photo"].required = False
        self.fields["bio"].required = False
        self.fields["specialization"].required = False
        self.fields["phone"].required = False
        self.fields["telegram"].required = False
        self.fields["whatsapp"].required = False
        self.fields["max_contact"].required = False
        self._add_sports_field()

    def clean_max_contact(self) -> str:
        """Нормализует контакт MAX как ссылку или номер телефона."""
        return normalize_max_contact(self.cleaned_data.get("max_contact"))

    def save(self, commit: bool = True) -> CoachApplication:
        """Сохранить заявку с выбранным видом спорта."""
        application: CoachApplication = super().save(commit=False)
        self._apply_sports(application)
        if commit:
            application.save()
        return application


class TrainingForm(VenueSportCheckboxesMixin, forms.ModelForm):
    """Форма создания/редактирования тренировки тренером (через сайт)."""

    type_prices = forms.JSONField(
        label="Типы тренировки и цены",
        required=True,
        widget=TypePricesWidget(),
    )
    skill_levels = forms.MultipleChoiceField(
        label="Уровни (игроков с этим уровнем беру на тренировку)",
        choices=SkillLevel.choices,
        widget=MultiCheckboxWidget(choices=SkillLevel.choices, show_ntrp=True),
        required=True,
    )
    target_levels = forms.MultipleChoiceField(
        label="Целевой уровень силы",
        choices=SkillLevel.choices,
        widget=MultiCheckboxWidget(choices=SkillLevel.choices, show_ntrp=True),
        required=False,
    )

    class Meta:
        model = Training
        fields = (
            "title",
            "short_description",
            "description",
            "type_prices",
            "skill_levels",
            "target_levels",
            "courts",
            "city",
            "duration_minutes",
            "max_participants",
            "court_has_extra_fee",
            "court_price_min",
            "court_price_max",
            "schedule",
            "image",
            "is_active",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "short_description": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "courts": forms.SelectMultiple(attrs={"class": "form-control", "size": 8}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "duration_minutes": forms.NumberInput(
                attrs={"class": "form-control", "min": 1}
            ),
            "max_participants": forms.NumberInput(
                attrs={"class": "form-control", "min": 1}
            ),
            "court_has_extra_fee": forms.CheckboxInput(
                attrs={"class": "form-checkbox"}
            ),
            "court_price_min": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "1",
                    "min": 0,
                    "placeholder": "от",
                }
            ),
            "court_price_max": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "1",
                    "min": 0,
                    "placeholder": "до",
                }
            ),
            "schedule": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "image": forms.FileInput(
                attrs={"class": "form-control", "accept": "image/*"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["courts"].queryset = Court.objects.filter(is_active=True).order_by(
            "name"
        )
        # На сайте корты выбираем чекбоксами
        self.fields["courts"].widget = forms.CheckboxSelectMultiple(
            attrs={"class": "checkbox-list courts-checkbox-list"}
        )
        # Важно: явно прокидываем choices в виджет, иначе он рендерит пустой список
        self.fields["courts"].widget.choices = self.fields["courts"].choices
        self.fields["courts"].required = False
        self.fields["short_description"].required = False
        self.fields["schedule"].required = False
        self.fields["court_price_min"].required = False
        self.fields["court_price_max"].required = False
        self.fields["image"].required = False
        self._add_sports_field()

        if self.instance and self.instance.pk:
            self.initial["type_prices"] = self.instance.type_prices or {}
            self.initial["skill_levels"] = self.instance.skill_levels or []
            self.initial["target_levels"] = self.instance.target_levels or []

    def clean_type_prices(self):
        value = self.cleaned_data.get("type_prices")
        if not value:
            raise forms.ValidationError("Выберите хотя бы один тип тренировки.")
        return value

    def _apply_price_range(self, training: Training) -> None:
        """Устанавливает общий диапазон цен по выбранным типам."""
        type_prices = self.cleaned_data.get("type_prices") or {}
        prices: list[Decimal] = []
        for raw in type_prices.values():
            if raw in (None, ""):
                continue
            try:
                prices.append(Decimal(str(raw)))
            except Exception:
                continue
        if prices:
            training.price_min = min(prices)
            training.price_max = max(prices)
        else:
            training.price_min = None
            training.price_max = None

    def save(self, commit: bool = True) -> Training:
        training: Training = super().save(commit=False)
        self._apply_sports(training)
        self._apply_price_range(training)
        if commit:
            training.save()
            self.save_m2m()
        return training


class AdminTrainingForm(VenueSportCheckboxesMixin, forms.ModelForm):
    """Форма тренировки для админки: чекбоксы + цены по типам в одном блоке."""

    type_prices = forms.JSONField(
        label="Типы тренировки и цены",
        required=True,
        widget=TypePricesWidget(),
    )
    skill_levels = forms.MultipleChoiceField(
        label="Уровни (игроков с этим уровнем беру на тренировку)",
        choices=SkillLevel.choices,
        widget=MultiCheckboxWidget(choices=SkillLevel.choices, show_ntrp=True),
        required=True,
    )
    target_levels = forms.MultipleChoiceField(
        label="Целевой уровень силы",
        choices=SkillLevel.choices,
        widget=MultiCheckboxWidget(choices=SkillLevel.choices, show_ntrp=True),
        required=False,
    )

    class Meta:
        model = Training
        fields = (
            "title",
            "slug",
            "short_description",
            "description",
            "type_prices",
            "skill_levels",
            "target_levels",
            "coach",
            "courts",
            "city",
            "duration_minutes",
            "max_participants",
            "court_has_extra_fee",
            "court_price_min",
            "court_price_max",
            "schedule",
            "image",
            "is_active",
            "is_featured",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["courts"].queryset = Court.objects.filter(is_active=True).order_by(
            "name"
        )
        self.fields["courts"].required = False
        self.fields["short_description"].required = False
        self.fields["schedule"].required = False
        self.fields["court_price_min"].required = False
        self.fields["court_price_max"].required = False
        self.fields["image"].required = False
        self._add_sports_field()

        if self.instance and self.instance.pk:
            self.initial["type_prices"] = self.instance.type_prices or {}
            self.initial["skill_levels"] = self.instance.skill_levels or []
            self.initial["target_levels"] = self.instance.target_levels or []

    def clean_type_prices(self):
        value = self.cleaned_data.get("type_prices")
        if not value:
            raise forms.ValidationError("Выберите хотя бы один тип тренировки.")
        return value

    def _apply_price_range(self, training: Training) -> None:
        """Устанавливает общий диапазон цен по выбранным типам."""
        type_prices = self.cleaned_data.get("type_prices") or {}
        prices: list[Decimal] = []
        for raw in type_prices.values():
            if raw in (None, ""):
                continue
            try:
                prices.append(Decimal(str(raw)))
            except Exception:
                continue
        if prices:
            training.price_min = min(prices)
            training.price_max = max(prices)
        else:
            training.price_min = None
            training.price_max = None

    def save(self, commit: bool = True) -> Training:
        training: Training = super().save(commit=False)
        self._apply_sports(training)
        self._apply_price_range(training)
        if commit:
            training.save()
            self.save_m2m()
        return training


class CoachAdminForm(VenueSportCheckboxesMixin, forms.ModelForm):
    """Форма тренера в админке: пустые поля берутся из профиля игрока."""

    class Meta:
        model = Coach
        fields = "__all__"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        for field_name in ("name", "slug", "city"):
            self.fields[field_name].required = False
        self.fields.pop("sport", None)
        self._add_sports_field()

    def clean(self):
        cleaned = super().clean()
        user = cleaned.get("user")
        if user is not None:
            current = {
                field: str(cleaned.get(field) or "")
                for field in (
                    "name",
                    "phone",
                    "telegram",
                    "whatsapp",
                    "max_contact",
                    "city",
                )
            }
            from apps.training.coach_defaults import (
                apply_coach_defaults,
                coach_defaults_from_user,
                unique_coach_slug,
            )

            filled = apply_coach_defaults(current, coach_defaults_from_user(user))
            cleaned.update(filled)
            if filled.get("name") and not str(cleaned.get("slug") or "").strip():
                cleaned["slug"] = unique_coach_slug(
                    filled["name"],
                    exclude_pk=self.instance.pk,
                )
        if not str(cleaned.get("name") or "").strip():
            self.add_error(
                "name",
                "Укажите имя или выберите пользователя с заполненным ФИО.",
            )
        if not str(cleaned.get("city") or "").strip():
            self.add_error(
                "city",
                "Укажите населённый пункт или выберите пользователя с городом в профиле.",
            )
        if not str(cleaned.get("slug") or "").strip():
            self.add_error("slug", "Укажите URL.")
        return cleaned

    def save(self, commit: bool = True) -> Coach:
        """Сохранить тренера с выбранным видом спорта."""
        coach: Coach = super().save(commit=False)
        self._apply_sports(coach)
        if commit:
            coach.save()
        return coach


class CoachApplicationAdminForm(VenueSportCheckboxesMixin, forms.ModelForm):
    """Форма заявки в админке: вид спорта чекбоксами теннис/падел."""

    class Meta:
        model = CoachApplication
        fields = "__all__"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields.pop("sport", None)
        self._add_sports_field()

    def save(self, commit: bool = True) -> CoachApplication:
        """Сохранить заявку с выбранным видом спорта."""
        application: CoachApplication = super().save(commit=False)
        self._apply_sports(application)
        if commit:
            application.save()
        return application
