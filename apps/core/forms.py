"""
Core forms.
"""

from django import forms


class FeedbackForm(forms.Form):
    """Форма обратной связи. Только для зарегистрированных пользователей."""

    subject = forms.CharField(
        label="Тема",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Кратко о чём сообщение"}
        ),
    )
    message = forms.CharField(
        label="Сообщение *",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 5, "placeholder": "Ваше сообщение"}
        ),
    )

    def clean_message(self):
        msg = (self.cleaned_data.get("message") or "").strip()
        if not msg:
            raise forms.ValidationError("Введите сообщение.")
        return msg


class PlatformActivityAnnounceForm(forms.Form):
    """Форма ручного сообщения администратора в публичную ленту."""

    message = forms.CharField(
        label="Сообщение в ленту",
        min_length=2,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "maxlength": "500",
                "placeholder": "Текст, который увидят все посетители главной",
            }
        ),
    )

    def clean_message(self) -> str:
        """Вернуть очищенный текст объявления.

        Returns:
            str: Сообщение без крайних пробелов.

        Raises:
            forms.ValidationError: Если после очистки текст пустой.
        """
        msg = (self.cleaned_data.get("message") or "").strip()
        if len(msg) < 2:
            raise forms.ValidationError("Введите текст сообщения.")
        return msg
