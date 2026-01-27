"""
User forms.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import RegexValidator

from .models import Forehand, Gender, Player, SkillLevel

User = get_user_model()


class PlayerRegistrationForm(forms.ModelForm):
    """Player information form for registration."""
    
    class Meta:
        model = Player
        fields = ('skill_level', 'birth_date', 'gender', 'forehand', 'city')
        widgets = {
            'skill_level': forms.Select(attrs={'class': 'form-control'}),
            'birth_date': forms.DateInput(
                attrs={'class': 'form-control', 'type': 'date'},
                format='%Y-%m-%d',
            ),
            'gender': forms.Select(attrs={'class': 'form-control'}),
            'forehand': forms.Select(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Город'}),
        }
        labels = {
            'skill_level': 'Уровень мастерства *',
            'birth_date': 'Дата рождения *',
            'gender': 'Пол *',
            'forehand': 'Ведущая рука *',
            'city': 'Город',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['birth_date'].input_formats = ['%Y-%m-%d']
        self.fields['skill_level'].initial = SkillLevel.NOVICE
        self.fields['gender'].initial = Gender.MALE
        self.fields['forehand'].initial = Forehand.RIGHT
        for field_name in ('skill_level', 'gender', 'forehand'):
            field = self.fields[field_name]
            field.choices = [(value, label) for value, label in field.choices if value != '']


class UserRegistrationForm(forms.ModelForm):
    """User registration form."""

    email = forms.EmailField(
        label='Email *',
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    phone = forms.CharField(
        label='Телефон',
        required=False,
        initial='+7',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+7XXXXXXXXXX'}),
    )

    password = forms.CharField(
        label='Пароль *',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        min_length=8,
    )
    password_confirm = forms.CharField(
        label='Подтвердите пароль *',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )
    agree_legal = forms.BooleanField(
        required=True,
        label="",
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox"}),
    )

    class Meta:
        model = User
        fields = ('email', 'phone', 'first_name', 'last_name')
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'first_name': 'Имя',
            'last_name': 'Фамилия',
        }

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        # Если поле пустое или содержит только '+7', возвращаем пустую строку
        if phone in {'', '+7'}:
            return ''
        # Если поле заполнено, проверяем формат
        import re
        phone_pattern = r'^\+7\d{10}$'
        if not re.match(phone_pattern, phone):
            raise forms.ValidationError('Введите телефон в формате +7XXXXXXXXXX (10 цифр после +7)')
        return phone

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
            "skill_level",
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
            "skill_level": forms.Select(attrs={"class": "form-control"}),
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
            "skill_level": "Уровень мастерства",
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
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name
        self.fields['birth_date'].input_formats = ['%Y-%m-%d']

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
