"""
User forms.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import RegexValidator

from .models import Forehand, Gender, Player, SkillLevel

User = get_user_model()


class UserRegistrationForm(forms.ModelForm):
    """Упрощённая форма регистрации: имя, фамилия, телефон, email, дата рождения, NTRP-тест."""

    email = forms.EmailField(
        label="Email *",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    phone = forms.CharField(
        label="Телефон *",
        required=True,
        initial="+7",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+7XXXXXXXXXX"}),
    )
    birth_date = forms.DateField(
        label="Дата рождения *",
        required=True,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
    )
    city = forms.CharField(
        label="Город *",
        required=True,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Например: Москва"}),
    )
    ntrp_level = forms.IntegerField(
        label="NTRP",
        required=True,
        min_value=1,
        max_value=7,
        widget=forms.HiddenInput(attrs={"id": "id_ntrp_level"}),
    )
    password = forms.CharField(
        label="Пароль *",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        min_length=8,
    )
    password_confirm = forms.CharField(
        label="Подтвердите пароль *",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    agree_legal = forms.BooleanField(
        required=True,
        label="",
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox"}),
    )

    class Meta:
        model = User
        fields = ("email", "phone", "first_name", "last_name")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
        }
        labels = {
            "first_name": "Имя *",
            "last_name": "Фамилия *",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].input_formats = ["%Y-%m-%d"]
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True

    def clean_city(self):
        city = (self.cleaned_data.get("city") or "").strip()
        if not city:
            raise forms.ValidationError("Укажите город.")
        return city

    def clean_phone(self):
        import re

        phone = self.cleaned_data.get("phone", "").strip()
        if phone in ("", "+7"):
            raise forms.ValidationError("Укажите телефон в формате +7XXXXXXXXXX.")
        if not re.match(r"^\+7\d{10}$", phone):
            raise forms.ValidationError("Телефон: +7 и 10 цифр (например +79001234567).")
        return phone

    def clean_ntrp_level(self):
        val = self.cleaned_data.get("ntrp_level")
        if val is None or val == "":
            raise forms.ValidationError("Пройдите NTRP-тест и нажмите «Рассчитать» перед регистрацией.")
        try:
            v = int(val)
            if v < 1 or v > 7:
                raise forms.ValidationError("Некорректный результат NTRP. Пройдите тест заново.")
            return v
        except (TypeError, ValueError):
            raise forms.ValidationError("Пройдите NTRP-тест и нажмите «Рассчитать» перед регистрацией.")

    def clean_password_confirm(self):
        password = self.cleaned_data.get('password')
        password_confirm = self.cleaned_data.get('password_confirm')
        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError('Пароли не совпадают')
        return password_confirm

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class PlayerProfileForm(forms.ModelForm):
    """Player profile edit form."""

    first_name = forms.CharField(
        label='Имя',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    last_name = forms.CharField(
        label='Фамилия',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
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
            "city": forms.TextInput(attrs={"class": "form-control", "placeholder": "Город"}),
            "bio": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "telegram": forms.TextInput(attrs={"class": "form-control"}),
            "whatsapp": forms.TextInput(attrs={"class": "form-control"}),
            "max_contact": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ссылка на профиль MAX из «Поделиться»",
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
        self.fields["birth_date"].input_formats = ["%Y-%m-%d"]
        for fn in ("gender", "forehand"):
            f = self.fields.get(fn)
            if f and hasattr(f, "choices") and f.choices:
                choices = list(f.choices)
                if choices and choices[0][0] != "":
                    f.choices = [("", "———")] + choices

    def save(self, commit=True):
        player = super().save(commit=False)
        if self.user:
            self.user.first_name = self.cleaned_data['first_name']
            self.user.last_name = self.cleaned_data['last_name']
            if commit:
                self.user.save()
        if commit:
            player.save()
        return player
