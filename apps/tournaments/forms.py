"""Формы приложения tournaments."""

from django import forms

from config.validators import validate_image_max_2mb


class TournamentPhotoUploadForm(forms.Form):
    """Форма загрузки фото участником турнира."""

    image = forms.ImageField(
        label="Фото",
        validators=[validate_image_max_2mb],
        widget=forms.FileInput(
            attrs={
                "class": "form-control",
                "accept": "image/*",
            }
        ),
    )
    caption = forms.CharField(
        label="Подпись",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Необязательно",
            }
        ),
    )
