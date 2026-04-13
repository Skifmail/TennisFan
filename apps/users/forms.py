"""
User forms.
"""

from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from apps.core.contact_utils import normalize_max_contact

from .models import Player

User = get_user_model()


class EmailAuthenticationForm(AuthenticationForm):
    """Форма входа с нормализацией email без учета регистра."""

    def clean_username(self) -> str:
        username = (self.cleaned_data.get("username") or "").strip().lower()
        return username


class UserRegistrationForm(forms.ModelForm):
    """Упрощённая форма регистрации: имя, фамилия, телефон, email, дата рождения, тест уровня силы."""

    first_name = forms.CharField(
        label="Имя *",
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Ваше имя"}
        ),
    )
    last_name = forms.CharField(
        label="Фамилия *",
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Ваша фамилия"}
        ),
    )
    phone = forms.CharField(
        label="Телефон *",
        required=True,
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": "form-control js-phone-input",
                "placeholder": "+7",
                "autocomplete": "tel",
            }
        ),
        help_text="Укажите номер в формате +7XXXXXXXXXX.",
    )
    email = forms.EmailField(
        label="Email *",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    city = forms.CharField(
        label="Город *",
        required=True,
        max_length=100,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Например: Москва"}
        ),
    )
    ntrp_level = forms.DecimalField(
        label="Уровень силы (NTRP)",
        required=True,
        min_value=Decimal("1.5"),
        max_value=Decimal("7.0"),
        decimal_places=1,
        widget=forms.NumberInput(
            attrs={
                "id": "id_ntrp_level",
                "class": "form-control",
                "min": "1.5",
                "max": "7",
                "step": "0.1",
                "placeholder": "например 3.7",
            }
        ),
        help_text="Число от 1.5 до 7.0.",
    )
    password = forms.CharField(
        label="Пароль *",
        widget=forms.PasswordInput(attrs={"class": "form-control", "minlength": 10}),
        min_length=10,
        help_text="Минимум 10 символов, не только цифры.",
    )
    password_confirm = forms.CharField(
        label="Подтвердите пароль *",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    ntrp_quiz_payload = forms.CharField(
        label="",
        required=False,
        widget=forms.HiddenInput(),
    )
    agree_legal = forms.BooleanField(
        required=True,
        label="",
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox"}),
    )

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "phone")
        widgets: dict = {}
        labels: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("Укажите email.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Пользователь с таким email уже зарегистрирован. "
                "Войдите в аккаунт или воспользуйтесь восстановлением пароля."
            )
        return email

    def clean_city(self):
        city = (self.cleaned_data.get("city") or "").strip()
        if not city:
            raise forms.ValidationError("Укажите город.")
        return city

    def clean_phone(self):
        phone_raw = (self.cleaned_data.get("phone") or "").strip()
        if not phone_raw:
            raise forms.ValidationError("Укажите телефон.")

        digits = "".join(ch for ch in phone_raw if ch.isdigit())
        # Допускаем ввод с 8 или 7 или +7, нормализуем к 11 цифрам
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]
        elif digits.startswith("7") and len(digits) == 11:
            pass
        elif len(digits) == 10:
            digits = "7" + digits
        if len(digits) != 11 or not digits.isdigit():
            raise forms.ValidationError(
                "Введите телефон в формате +7XXXXXXXXXX (11 цифр)."
            )
        normalized = f"+{digits}"

        if User.objects.filter(phone=normalized).exists():
            raise forms.ValidationError(
                "Пользователь с таким телефоном уже зарегистрирован. "
                "Войдите в аккаунт или воспользуйтесь восстановлением пароля."
            )

        return normalized

    def clean_ntrp_level(self):
        from decimal import InvalidOperation

        val = self.cleaned_data.get("ntrp_level")
        if val is None or val == "":
            raise forms.ValidationError(
                "Укажите уровень силы от 1.5 до 7.0 (например 3.7) или пройдите калькулятор ниже."
            )
        try:
            v = Decimal(str(val))
            if v < Decimal("1.5") or v > Decimal("7.0"):
                raise forms.ValidationError(
                    "Уровень должен быть от 1.5 до 7.0 (один знак после запятой)."
                )
            return v
        except (TypeError, ValueError, InvalidOperation) as err:
            raise forms.ValidationError(
                "Укажите уровень силы от 1.5 до 7.0 (например 3.7)."
            ) from err

    def clean_password_confirm(self):
        password = self.cleaned_data.get("password")
        password_confirm = self.cleaned_data.get("password_confirm")
        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError("Пароли не совпадают")
        return password_confirm

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        user.first_name = self.cleaned_data.get("first_name", "").strip()
        user.last_name = self.cleaned_data.get("last_name", "").strip()
        user.phone = self.cleaned_data.get("phone", "").strip()
        if commit:
            user.save()
        return user


class PlayerProfileForm(forms.ModelForm):
    """Player profile edit form."""

    first_name = forms.CharField(
        label="Имя",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        label="Фамилия",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    phone = forms.CharField(
        label="Телефон",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control js-phone-input",
                "placeholder": "+7",
                "autocomplete": "tel",
            }
        ),
    )

    class Meta:
        model = Player
        fields = (
            "avatar",
            "birth_date",
            "gender",
            "forehand",
            "city",
            "bio",
            "telegram",
            "whatsapp",
            "max_contact",
        )
        widgets = {
            "birth_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
            "gender": forms.Select(attrs={"class": "form-control"}),
            "forehand": forms.Select(attrs={"class": "form-control"}),
            "city": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Город"}
            ),
            "bio": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "telegram": forms.TextInput(attrs={"class": "form-control"}),
            "whatsapp": forms.TextInput(attrs={"class": "form-control"}),
            "max_contact": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ссылка на профиль MAX или номер телефона",
                }
            ),
        }
        labels = {
            "birth_date": "Дата рождения",
            "gender": "Пол",
            "forehand": "Ведущая рука",
            "city": "Город",
            "bio": "О себе",
            "telegram": "Telegram",
            "whatsapp": "WhatsApp",
            "max_contact": "MAX",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user:
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name
            self.fields["phone"].initial = user.phone
        self.fields["birth_date"].input_formats = ["%Y-%m-%d"]
        for fn in ("gender", "forehand"):
            f = self.fields.get(fn)
            if f and hasattr(f, "choices") and f.choices:
                choices = list(f.choices)
                if choices and choices[0][0] != "":
                    f.choices = [("", "———")] + choices

    def clean(self):
        from django.core.exceptions import ValidationError

        cleaned_data = super().clean()
        if not self.user:
            return cleaned_data
        city = (cleaned_data.get("city") or "").strip()
        if not city:
            return cleaned_data
        from apps.subscriptions.utils import normalize_city_for_pricing

        new_city_norm = normalize_city_for_pricing(city)
        if new_city_norm != "moscow":
            return cleaned_data
        try:
            sub = self.user.subscription
            if (
                not sub.is_valid()
                or not sub.purchase_city
                or sub.purchase_city == "moscow"
            ):
                return cleaned_data
        except Exception:
            return cleaned_data
        raise ValidationError(
            "Нельзя сменить город на Москву: у вас активна подписка по региональному тарифу. "
            "Дождитесь окончания подписки или отмените её в профиле, после этого можно будет сменить город."
        )

    def clean_max_contact(self) -> str:
        """Нормализует контакт MAX как ссылку или номер телефона."""
        return normalize_max_contact(self.cleaned_data.get("max_contact"))

    def save(self, commit=True):
        player = super().save(commit=False)
        if self.user:
            self.user.first_name = self.cleaned_data["first_name"]
            self.user.last_name = self.cleaned_data["last_name"]
            self.user.phone = self.cleaned_data["phone"]
            if commit:
                self.user.save()
        if commit:
            player.save()
        return player
