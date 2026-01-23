"""
User forms.
"""

from django import forms
from django.contrib.auth import get_user_model

from .models import Player

User = get_user_model()


class PlayerRegistrationForm(forms.ModelForm):
    """Player information form for registration."""
    
    class Meta:
        model = Player
        fields = ('skill_level', 'birth_date', 'gender', 'forehand', 'city')
        widgets = {
            'skill_level': forms.Select(attrs={'class': 'form-control'}),
            'birth_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'gender': forms.Select(attrs={'class': 'form-control'}),
            'forehand': forms.Select(attrs={'class': 'form-control'}),
            'city': forms.Select(attrs={'class': 'form-control'}),
        }
        labels = {
            'skill_level': 'Уровень мастерства *',
            'birth_date': 'Дата рождения *',
            'gender': 'Пол *',
            'forehand': 'Forehand *',
            'city': 'Город',
        }


class UserRegistrationForm(forms.ModelForm):
    """User registration form."""

    password = forms.CharField(
        label='Пароль *',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        min_length=8,
    )
    password_confirm = forms.CharField(
        label='Подтвердите пароль *',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = User
        fields = ('email', 'phone', 'first_name', 'last_name')
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'email': 'Email *',
            'phone': 'Телефон',
            'first_name': 'Имя',
            'last_name': 'Фамилия',
        }

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
            'avatar',
            'skill_level',
            'birth_date',
            'gender',
            'forehand',
            'city',
            'age',
            'bio',
            'telegram',
            'whatsapp',
        )
        widgets = {
            'skill_level': forms.Select(attrs={'class': 'form-control'}),
            'birth_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'gender': forms.Select(attrs={'class': 'form-control'}),
            'forehand': forms.Select(attrs={'class': 'form-control'}),
            'city': forms.Select(attrs={'class': 'form-control'}),
            'age': forms.NumberInput(attrs={'class': 'form-control'}),
            'bio': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'telegram': forms.TextInput(attrs={'class': 'form-control'}),
            'whatsapp': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'skill_level': 'Уровень мастерства',
            'birth_date': 'Дата рождения',
            'gender': 'Пол',
            'forehand': 'Forehand',
            'city': 'Город',
            'age': 'Возраст',
            'bio': 'О себе',
            'telegram': 'Telegram',
            'whatsapp': 'WhatsApp',
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user:
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name

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
